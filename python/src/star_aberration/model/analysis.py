"""The analysis of one image and its report.

Analysis and drawing are separate steps, so that the preview can switch
layers on and off without measuring the stars again:

    analyze_image()    the expensive part - debayer, star detection, PSF
                       fit, PSF asymmetry, coma field and the stretched
                       background. Depends only on the settings in
                       analysis_key(); the result is kept by the controller.
    stars_for()        the stars with or without the tracking component,
                       cached per setting in the analysis.
    report_analysis()  the console summary (tracking, coma, rings, FWHM
                       surface, tilt) and the metrics the assessment judges.
    render.maps        draws the enabled layers onto the background; layers
                       that need a computation of their own are cached in
                       the analysis under the settings they depend on.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .coma import fit_coma_field, log_coma_field, measure_star_asymmetry
from .detection import detect_star_candidates, estimate_background
from .fwhm_grid import (FWHM_CORNER_NAMES, FWHM_GRID_SIZE, compute_siril_tilt, evaluate_fwhm_grid,
                        format_fwhm_grid_evaluation)
from .image import ImageData, debayer_superpixel, stretched_rgb8
from .log import Log
from .optics import GUIDING_TYPES, MOUNT_ALTAZ, MOUNT_TYPES, Optics, field_rotation_smear_px
from .psf import fit_stars
from .shape import evaluate_shape_field, format_shape_evaluation
from .star import Star
from .surface import analyze_radial_tangential, fit_fwhm_surface
from .tilt import tilt_angle_lines
from .tracking import apply_tracking_correction, compute_tracking_component, log_tracking_component

FWHM_CELL_PX, FWHM_CELL_ARCSEC, FWHM_CELL_RATIO = 0, 1, 2

MEASUREMENT_KEYS = ["threshold", "radius", "use_moffat", "mad_outlier_factor", "max_candidates", "debayer",
                    "bayer_pattern", "sd_custom", "sd_structure_layers", "sd_sensitivity", "sd_peak_response",
                    "sd_max_distortion", "sd_allow_clustered"]


class Progress:
    """The progress of a run: steps with a label and a range of the whole,
    each with an optional fraction within it. sink(fraction, label)."""

    def __init__(self, sink: Callable[[float, str], None] | None = None,
                 cancelled: Callable[[], bool] | None = None):
        self._sink = sink
        self._cancelled = cancelled
        self._lo, self._hi, self._label = 0.0, 1.0, ""

    def step(self, label: str, lo: float, hi: float) -> None:
        self.check()
        self._lo, self._hi, self._label = lo, hi, label
        if self._sink:
            self._sink(lo, label)

    def within(self, fraction: float) -> None:
        self.check()
        if self._sink:
            self._sink(self._lo + (self._hi - self._lo) * min(1.0, max(0.0, fraction)), self._label)

    def check(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise Cancelled()

    def nested(self, i: int, n: int, prefix: str = "") -> "Progress":
        """A progress for part i of n of the current step."""
        parent = self

        def sink(fraction: float, label: str) -> None:
            if parent._sink:
                parent._sink(parent._lo + (parent._hi - parent._lo) * (i + fraction) / n, prefix + label)
        return Progress(sink, self._cancelled)


class Cancelled(Exception):
    """The user stopped the run."""


@dataclass
class Analysis:
    key: str
    target_name: str
    debayer: bool
    w: int
    h: int
    stars: list[Star]
    tracking: dict
    has_asymmetry: bool
    coma_fit: dict | None
    coma_fit_centroid: dict | None
    background: np.ndarray | None  # uint8 RGB (h, w, 3)
    header: dict  # {name, path, keywords}
    layers: dict = field(default_factory=dict)
    measurement: dict = field(default_factory=dict)
    file: str = ""  # the results file it was loaded from
    created: str = ""


def analysis_key(name: str, path: str, s) -> str:
    """The settings the analysis depends on."""
    return json.dumps([name, path, s.threshold, s.radius, s.use_moffat, s.mad_outlier_factor, s.max_candidates,
                       s.debayer, s.bayer_pattern if s.debayer else "",
                       [s.sd_structure_layers, s.sd_sensitivity, s.sd_peak_response, s.sd_max_distortion,
                        s.sd_allow_clustered] if s.sd_custom else None])


def cached_layer(analysis: Analysis, name: str, key: Any, compute: Callable[[], Any]) -> Any:
    """The value of a layer cached under name, computed again when missing or
    computed for another key. Only the latest value per layer is kept."""
    entry = analysis.layers.get(name)
    if entry is None or entry[0] != key:
        entry = (key, compute())
        analysis.layers[name] = entry
    return entry[1]


def stars_for(analysis: Analysis, subtract_tracking: bool) -> list[Star]:
    """The stars with the tracking component subtracted or not (copies)."""
    def compute():
        stars = [s.copy() for s in analysis.stars]
        if subtract_tracking:
            apply_tracking_correction(stars, analysis.tracking)
        return stars
    return cached_layer(analysis, "stars", subtract_tracking, compute)


def shape_evaluation_for(analysis: Analysis, s) -> dict:
    stars = stars_for(analysis, s.subtract_tracking)

    def compute():
        radius = math.hypot(analysis.w, analysis.h) * (s.streamline_radius_percent / 100)
        return evaluate_shape_field(stars, analysis.w, analysis.h, radius)
    return cached_layer(analysis, "shape_evaluation", (s.subtract_tracking, s.streamline_radius_percent), compute)


def analyze_image(img: ImageData, s, measure_asymmetry: bool, log: Log, progress: Progress | None = None,
                  with_background: bool = True) -> Analysis | None:
    """Runs the expensive part on an image; None when no stars could be
    measured."""
    progress = progress or Progress()
    debayer = s.debayer and not img.is_color
    if s.debayer and not debayer:
        log.warning("Debayer skipped: the target image is a color image, not a Bayer mosaic.")
    target = img
    if debayer:
        progress.step("Debayering (SuperPixel)", 0.0, 0.05)
        try:
            target = debayer_superpixel(img, s.bayer_pattern)
        except ValueError as e:
            log.error(str(e))
            return None
        log.note(f"Debayer (SuperPixel) applied ({target.width} x {target.height}).")

    channel = target.channel(0)
    progress.step("Detecting stars", 0.05, 0.12)
    t0 = time.monotonic()
    cands = detect_star_candidates(channel, s, log)
    log.write(f"Star detection finished after {time.monotonic() - t0:.1f} s.")
    if not cands:
        log.error("The star detector found no candidates. Possible causes: image not yet debayered (Bayer mosaic "
                  "instead of true RGB), uncalibrated raw image, or no star field in the image area.")
        return None

    _, noise = estimate_background(channel.astype(np.float32))
    min_threshold = 0.2
    t = s.threshold
    stars: list[Star] = []
    attempt = 1
    while True:
        log.write(f"Fit attempt {attempt}: threshold={t:.2f}  search radius={s.radius}  "
                  f"Moffat={'true' if s.use_moffat else 'false'}")
        progress.step(f"Fitting PSFs of {len(cands)} stars", 0.12, 0.65)
        t0 = time.monotonic()
        stars = fit_stars(channel, cands, t, s.radius, s.use_moffat, s.mad_outlier_factor, noise, log,
                          progress.within)
        log.write(f"PSF fit finished after {time.monotonic() - t0:.1f} s.")
        if stars or t <= min_threshold or attempt >= 6:
            break
        nxt = max(min_threshold, t * 0.6)
        log.warning(f"No fits at threshold={t:.2f} - automatically reducing to {nxt:.2f} ...")
        t = nxt
        attempt += 1
    if not stars:
        log.error(f"Still no successful PSF fit even after automatic threshold reduction, although {len(cands)} "
                  "candidate(s) were found. Consider adjusting the search radius.")
        return None
    if t != s.threshold:
        log.note(f"Note: threshold automatically reduced from {s.threshold:.2f} to {t:.2f}. Enter this value "
                 "directly for future runs.")
    for st in stars:
        st.eccentricity_raw = st.eccentricity
        st.rotation_raw = st.rotation

    w, h = target.width, target.height
    coma_fit = coma_fit_centroid = None
    if measure_asymmetry:
        progress.step("Measuring PSF asymmetry", 0.65, 0.78)
        n = measure_star_asymmetry(channel, stars, progress.within)
        log.write(f"PSF asymmetry measured for {n} / {len(stars)} stars.")
        coma_fit = fit_coma_field(stars, w, h, False)
        coma_fit_centroid = fit_coma_field(stars, w, h, True)

    background = None
    if with_background:
        progress.step("Rendering the background", 0.78, 0.85)
        background = stretched_rgb8(target)

    return Analysis(
        key=analysis_key(img.name, img.path, s), target_name=target.name, debayer=debayer, w=w, h=h, stars=stars,
        tracking=compute_tracking_component(stars, w, h), has_asymmetry=measure_asymmetry, coma_fit=coma_fit,
        coma_fit_centroid=coma_fit_centroid, background=background,
        header={"name": img.name, "path": img.path, "keywords": list(img.keywords)},
        measurement={k: getattr(s, k) for k in MEASUREMENT_KEYS})


def fwhm_arcsec_scale(optics: Optics) -> float:
    """Arcseconds per pixel (effective pitch), 0 without pitch and focal length."""
    return 206.265 * optics.pixel_pitch_um / optics.focal_length_mm \
        if optics.pixel_pitch_um > 0 and optics.focal_length_mm > 0 else 0.0


def fwhm_display_unit(s, optics: Optics) -> tuple[float, str]:
    """(scale, unit): arcseconds when chosen and known, else pixels."""
    arcsec = fwhm_arcsec_scale(optics)
    if s.fwhm_cell_value == FWHM_CELL_ARCSEC and arcsec > 0:
        return arcsec, "\""
    return 1.0, "px"


def report_analysis(analysis: Analysis, stars: list[Star], s, optics: Optics, log: Log,
                    verbose: bool = False) -> dict:
    """The console summary; returns {tilt (Siril quadrants or None), metrics}."""
    w, h = analysis.w, analysis.h
    show_coma = analysis.has_asymmetry and s.measure_asymmetry
    if verbose:
        log_tracking_component(analysis.tracking, s.subtract_tracking, log)
        if show_coma:
            log_coma_field(analysis.coma_fit, analysis.coma_fit_centroid, w, h, optics.pixel_pitch_um, log)
    rings = analyze_radial_tangential(stars, w, h, show_coma, log)
    surface = fit_fwhm_surface(stars, w, h, optics.pixel_pitch_um, optics.focal_length_mm, optics.aperture_mm, log)
    if s.fwhm_cell_value == FWHM_CELL_ARCSEC:
        if optics.pixel_pitch_um > 0 and optics.focal_length_mm > 0:
            log.write(f"FWHM grid in arcseconds: {206.265 * optics.pixel_pitch_um / optics.focal_length_mm:.3f}″/px "
                      f"(pitch {optics.pixel_pitch_um:.2f} µm, focal length {optics.focal_length_mm:.0f} mm).")
        else:
            log.warning("FWHM grid in arcseconds needs pixel pitch and focal length - showing pixels.")

    tilt = compute_siril_tilt(stars, w, h)
    if tilt is None:
        log.warning("Siril tilt evaluation: at least one quadrant/ring has no stars - skipped.")
    else:
        q = [tilt["m1"], tilt["m2"], tilt["m3"], tilt["m4"]]
        best, worst, ref = min(q), max(q), sum(q) / 4
        log.note(f"Siril-Style Tilt: Sensor tilt[FWHM]={worst - best:.2f} ({(worst - best) / ref * 100:.0f}%), "
                 f"Off-axis aberration[FWHM]={tilt['m_outer'] - tilt['m_inner']:.2f}")
        if optics.physical and verbose:
            for line in tilt_angle_lines(tilt, w, h, optics.pixel_pitch_um, optics.focal_length_mm, optics.aperture_mm):
                log.note(line)

    grid = evaluate_fwhm_grid(stars, w, h, FWHM_GRID_SIZE)
    log.note("--- FWHM grid assessment (11x11, ratio to the center) ---")
    for line in format_fwhm_grid_evaluation(grid):
        log.write(line)
    grid_summary = {
        "center": grid["center"]["value"] if grid["center"] else None,
        "center_se": grid["center"]["se"] if grid["center"] else None,
        "corner_ratio": grid["corners"]["ratio"] if grid["corners"] else None,
        "corner_ratios": grid["corners"]["ratios"] if grid["corners"] else None,
        "tilt_rel": grid["tilt"]["rel"] if grid["tilt"] else None,
        "tilt_sigma": grid["tilt"]["z"] if grid["tilt"] else None,
        "tilt_soft_corner": FWHM_CORNER_NAMES[grid["tilt"]["soft"]] if grid["tilt"] else None,
        "edge_spread": grid["edge_spread"],
        "cells": grid["class_counts"],
    }

    shape = shape_evaluation_for(analysis, s)
    log.note("--- Star shape assessment (11x11 cells, optics model, singular points) ---")
    for line in format_shape_evaluation(shape, analysis.tracking, s.subtract_tracking):
        log.write(line)
    sm = shape["model"]
    shape_summary = {
        "center_elongation": shape["center"]["chi"] if shape["center"] else None,
        "center_se": shape["center"]["se"] if shape["center"] else None,
        "edge_eps_rad": shape["edge"]["mean"] if shape["edge"] else None,
        "edge_eps_rad_se": shape["edge"]["se"] if shape["edge"] else None,
        "corner_eps_rad": [c["mean"] if c else None for c in shape["corners"]],
        "corner_eps_rad_se": [c["se"] if c else None for c in shape["corners"]],
        "model_explained": sm.explained if sm else None,
        "model_chi2dof": sm.chi2dof if sm else None,
        "curvature_term": sm.k if sm else None,
        "curvature_term_se": sm.k_se if sm else None,
        "uniform_term": sm.constant if sm else None,
        "linear_astigmatism": sm.binodal if sm else None,
        "axis_x": sm.axis["x"] if sm and sm.axis else None,
        "axis_y": sm.axis["y"] if sm and sm.axis else None,
        "unexplained_cells": len(sm.unexplained) if sm else None,
        "singular_plus": sum(1 for d in shape["defects"] if d["index"] > 0),
        "singular_minus": sum(1 for d in shape["defects"] if d["index"] < 0),
        "cells": shape["counts"],
    }

    metrics = {
        "n": len(stars), "w": w, "h": h,
        "tracking": analysis.tracking, "tracking_subtracted": s.subtract_tracking,
        "rings": rings, "surface": surface, "siril": tilt, "grid": grid_summary, "shape": shape_summary,
        "guiding": {"type": optics.guiding["type"], "name": GUIDING_TYPES[optics.guiding["type"]],
                    "source": optics.guiding["source"]} if optics.guiding else None,
        "mount": {"type": optics.mount["type"], "name": MOUNT_TYPES[optics.mount["type"]],
                  "source": optics.mount["source"],
                  "field_rotation_corner_px": field_rotation_smear_px(optics.mount["obs"], w, h)
                  if optics.mount["type"] == MOUNT_ALTAZ else None,
                  "exposure_s": optics.mount["obs"]["exposure_s"]} if optics.mount else None,
        "f_ratio": optics.f_ratio,
    }
    if show_coma:
        metrics["coma"] = analysis.coma_fit  # absent: not measured; None: too few stars
    return {"tilt": tilt, "metrics": metrics}


def export_csv(stars: list[Star], path: str) -> None:
    """One line per star. Eccentricity/Rotation_deg as drawn (tracking
    subtracted if on); *_raw as fitted; Asym*/M3* empty when not measured."""
    def num(v, d):
        return "" if v is None else f"{v:.{d}f}"
    lines = ["X,Y,FWHMx,FWHMy,Eccentricity,Rotation_deg,Eccentricity_raw,Rotation_raw_deg,AsymX_px,AsymY_px,M3X,M3Y"]
    for s in stars:
        lines.append(f"{s.x:.3f},{s.y:.3f},{s.fwhm_x:.3f},{s.fwhm_y:.3f},{s.eccentricity:.4f},{s.rotation:.2f}," +
                     ",".join([num(s.eccentricity_raw, 4), num(s.rotation_raw, 2), num(s.asym_x, 4),
                               num(s.asym_y, 4), num(s.m3_x, 4), num(s.m3_y, 4)]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
