"""The maps of an analysis. Each shows one measured quantity on the stretched
frame:

    size   star size: the 11 x 11 FWHM grid
    shape  star shape: one ellipse per star, the streamlines of its trend,
           optionally the orientation heatmap as background, the tracking
           marker, the cell means, the optics model and singular points
    coma   PSF asymmetry: the coma field (arrows, coma-free point), its
           streamlines and optionally one arrow per star
    tilt   Siril-style tilt: the quadrant polygon and the tilt axis
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ..model.analysis import (FWHM_CELL_RATIO, Analysis, cached_layer, fwhm_display_unit, shape_evaluation_for,
                              stars_for)
from ..model.coma import coma_grid_arrows, generate_coma_streamlines
from ..model.fwhm_grid import FWHM_GRID_SIZE, compute_siril_tilt, evaluate_fwhm_grid
from ..model.heatmap import orientation_heatmap_rgb
from ..model.log import Log, SILENT
from ..model.optics import Optics
from ..model.shape import (SHAPE_MIN_Z, ellipticity_from_ecc, shape_model_field_at, shape_model_significant_at,
                           shape_significant_at, star_image_angle)
from ..model.star import Star
from ..model.stats import median_of
from ..model.streamlines import generate_streamlines, trace_field
from ..model.tilt import compute_tilt_axis
from .paint import Canvas, ecc_color, font, image_from_rgb


@dataclass(frozen=True)
class MapView:
    key: str
    label: str
    file_suffix: str
    ai: bool  # attached to the AI review


MAP_VIEWS = [
    MapView("size", "Star size", "_Size.png", True),
    MapView("shape", "Star shape", "_Shape.png", True),
    MapView("coma", "Coma", "_Coma.png", True),
    MapView("tilt", "Tilt", "_Tilt.png", False),
]


def map_view_by_key(key: str) -> MapView:
    return next((v for v in MAP_VIEWS if v.key == key), MAP_VIEWS[0])


def render_map(analysis: Analysis, s, optics: Optics, map_key: str, log: Log = SILENT) -> QImage:
    """The map map_key of an analysis with the given settings, as a QImage of
    the size of the measured image."""
    w, h = analysis.w, analysis.h
    stars = stars_for(analysis, s.subtract_tracking)
    show_coma = analysis.has_asymmetry and s.measure_asymmetry
    background = analysis.background
    if map_key == "shape" and s.show_orientation_heatmap:
        def compute():
            log.write("Computing orientation heatmap ...")
            hm = orientation_heatmap_rgb(stars, w, h, s.orientation_heatmap_degree)
            if hm is None:
                log.warning(f"Orientation heatmap: too few stars for a meaningful degree-"
                            f"{s.orientation_heatmap_degree} fit - using the normal background instead.")
            return hm
        hm = cached_layer(analysis, "heatmap", (s.subtract_tracking, s.orientation_heatmap_degree), compute)
        if hm is not None:
            background = hm
    if background is None:
        background = np.full((h, w, 3), 16, dtype=np.uint8)
    img = image_from_rgb(background)
    g = Canvas(img)
    try:
        if map_key == "shape":
            draw_shape_layers(g, analysis, stars, s, log)
        elif map_key == "size":
            draw_size_layers(g, w, h, stars, s, optics)
        elif map_key == "coma":
            draw_coma_layers(g, analysis, stars, s, show_coma, log)
        elif map_key == "tilt":
            draw_tilt_layers(g, w, h, stars, s, optics)
    finally:
        g.end()
    return img


# ---------------------------------------------------------------------------
# Shared

def draw_cell_grid(g: Canvas, w, h, grid_size) -> None:
    """The grid lines, with a frame around the inner 3 x 3 cells (the center
    the ratios and the shape center refer to)."""
    cw, ch = w / grid_size, h / grid_size
    for argb, width in ((0xB0000000, 3), (0xFFFFCCB2, 1)):
        g.pen(argb, width)
        for i in range(grid_size + 1):
            g.line(i * cw, 0, i * cw, h)
            g.line(0, i * ch, w, i * ch)
    mid = grid_size >> 1
    fx0, fy0, fx1, fy1 = (mid - 1) * cw, (mid - 1) * ch, (mid + 2) * cw, (mid + 2) * ch
    fw = max(2, round(min(cw, ch) * 0.012))
    g.pen(0xC0000000, fw + 2)
    g.rect(fx0, fy0, fx1, fy1)
    g.pen(0xFFFFFFFF, fw)
    g.rect(fx0, fy0, fx1, fy1)


def _note(g: Canvas, w, h, text: str) -> None:
    fs = max(14, round(min(w, h) / 40))
    g.set_font(font(fs))
    g.text_with_halo(fs, fs * 2, text, 0xFFFFCCB2, fs)


# ---------------------------------------------------------------------------
# Star size

FWHM_FILL_RATIO_MAX = 1.5
FWHM_FILL_STOPS = [(1.00, 70, 215, 95), (1.10, 70, 215, 95), (1.17, 255, 228, 90), (1.25, 255, 160, 40),
                   (1.50, 214, 38, 30)]


def fwhm_fill_color(ratio: float) -> int:
    """Green up to x1.10, yellow around x1.17, orange around x1.25, red from
    x1.50; the opacity grows with the ratio."""
    r = min(max(ratio, FWHM_FILL_STOPS[0][0]), FWHM_FILL_RATIO_MAX)
    i = 0
    while i < len(FWHM_FILL_STOPS) - 2 and r > FWHM_FILL_STOPS[i + 1][0]:
        i += 1
    a, b = FWHM_FILL_STOPS[i], FWHM_FILL_STOPS[i + 1]
    u = (r - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0

    def mix(k):
        return round(a[k] + (b[k] - a[k]) * u)
    t = (r - 1) / (FWHM_FILL_RATIO_MAX - 1)
    alpha = round(150 + 75 * t)
    return (alpha << 24) | (mix(1) << 16) | (mix(2) << 8) | mix(3)


def draw_fwhm_grid(g: Canvas, w, h, ev: dict, show: dict) -> None:
    n = ev["grid_size"]
    cw, ch = w / n, h / n
    for r in range(n):
        for c in range(n):
            e = ev["cells"][r][c]
            if e["value"] is None:
                continue
            x0, y0 = c * cw, r * ch
            if e["uncertain"]:
                g.fill_rect(x0, y0, x0 + cw, y0 + ch, 0x90B0B0B0, Qt.BrushStyle.BDiagPattern)
            elif e["cls"] >= 0 and e["ratio"] is not None:
                g.fill_rect(x0, y0, x0 + cw, y0 + ch, fwhm_fill_color(e["ratio"]))
    draw_cell_grid(g, w, h, n)
    fs = max(12, round(min(cw, ch) * 0.24))
    big = font(fs, True)
    small_size = max(9, round(fs * 0.55))
    small = font(small_size)
    for r in range(n):
        for c in range(n):
            e = ev["cells"][r][c]
            if e["value"] is None:
                continue
            lines = []
            if show["px"]:
                lines.append(f"{e['value']:.2f} px")
            if show["arcsec_scale"] > 0:
                lines.append(f"{e['value'] * show['arcsec_scale']:.2f}\"")
            if show["ratio"]:
                lines.append(f"×{e['ratio']:.2f}" if e["ratio"] is not None else f"{e['value']:.2f} px")
            if lines and e["uncertain"]:
                lines[0] = "(" + lines[0] + ")"
            if show["details"]:
                unit = " px" if show["detail_unit"] == "px" else show["detail_unit"]
                lines.append((f"±{e['se'] * show['detail_scale']:.2f}{unit}, " if e["se"] is not None else "")
                             + f"n={e['n']}")
            if not lines:
                continue
            ccx, ccy = (c + 0.5) * cw, (r + 0.5) * ch
            label_y = ccy + fs / 3 - (len(lines) - 1) * small_size * 0.7
            g.set_font(big)
            g.text_with_halo(ccx - g.text_width(lines[0]) / 2, label_y, lines[0], 0xFFFFFFFF, fs)
            g.set_font(small)
            for k in range(1, len(lines)):
                g.text_with_halo(ccx - g.text_width(lines[k]) / 2, label_y + small_size * 1.4 * k, lines[k],
                                 0xFFD8D8D8, small_size)


def draw_size_layers(g: Canvas, w, h, stars: list[Star], s, optics: Optics) -> None:
    scale, unit = fwhm_display_unit(s, optics)
    ratio = s.fwhm_cell_value == FWHM_CELL_RATIO
    draw_fwhm_grid(g, w, h, evaluate_fwhm_grid(stars, w, h, FWHM_GRID_SIZE), {
        "px": not ratio and unit == "px", "arcsec_scale": scale if not ratio and unit != "px" else 0,
        "ratio": ratio, "details": s.fwhm_grid_details, "detail_scale": scale, "detail_unit": unit})


# ---------------------------------------------------------------------------
# Star shape

def draw_streamlines(g: Canvas, lines, grid) -> None:
    """White where the smoothed field is significant, gray where not."""
    def significant(pts, j):
        if grid is None:
            return True
        return shape_significant_at(grid, 0.5 * (pts[j - 1][0] + pts[j][0]), 0.5 * (pts[j - 1][1] + pts[j][1]))

    for argb, width, wanted in ((0x90000000, 4, False), (0xFF8C8C8C, 2, False),
                                (0xA0000000, 4, True), (0xFFFFFFFF, 2, True)):
        g.pen(argb, width)
        g.line_runs(lines, lambda pts, j, wanted=wanted: significant(pts, j) == wanted)


def draw_shape_cells(g: Canvas, w, h, ev: dict, vector_scale: float) -> None:
    """Per cell a headless bar along the mean axis, its length the mean
    elongation, with a fan of the axis uncertainty; weak cells thin and gray,
    uncertain ones a dot."""
    n = ev["grid_size"]
    cw, ch = w / n, h / n
    half = min(cw, ch) * 0.45
    chis = sorted(c["chi"] for c in ev["cells"] if c and not c["uncertain"] and c["z"] is not None
                  and c["z"] >= SHAPE_MIN_Z)
    ref = max(0.02, chis[int(math.floor(0.9 * (len(chis) - 1)))] if chis else 0.05)
    lw = max(2, round(min(cw, ch) * 0.02))
    for r in range(n):
        for c in range(n):
            e = ev["cells"][r * n + c]
            ccx, ccy = (c + 0.5) * cw, (r + 0.5) * ch
            if not e:
                continue
            if e["uncertain"]:
                g.fill_ellipse(ccx - lw, ccy - lw, ccx + lw, ccy + lw, 0xA0B0B0B0)
                continue
            length = min(1.2, e["chi"] / ref) * half * vector_scale
            dx, dy = math.cos(e["psi"]) * length, math.sin(e["psi"]) * length
            significant = e["z"] is not None and e["z"] >= SHAPE_MIN_Z
            if significant:
                spread = min(math.pi / 2, e["se"] / (2 * max(1e-6, e["chi"])))
                for side in (0.0, math.pi):
                    pts = [(ccx, ccy)]
                    for k in range(13):
                        a = e["psi"] + side - spread + 2 * spread * k / 12
                        pts.append((ccx + math.cos(a) * length, ccy + math.sin(a) * length))
                    g.fill_polygon(pts, 0x60FFFFFF)
                g.pen(0xC0000000, lw + 2)
                g.line(ccx - dx, ccy - dy, ccx + dx, ccy + dy)
                g.pen(0xFFFFFFFF, lw)
            else:
                g.pen(0xA0B0B0B0, max(1, lw / 2))
            g.line(ccx - dx, ccy - dy, ccx + dx, ccy + dy)


def draw_model_streamlines(g: Canvas, lines, w, h, fit, hide_weak: bool) -> None:
    core = max(3, round(min(w, h) / 1200))

    def significant(pts, j):
        return shape_model_significant_at(fit, 0.5 * (pts[j - 1][0] + pts[j][0]), 0.5 * (pts[j - 1][1] + pts[j][1]))

    passes = [] if hide_weak else [(0xF0000000, core + 4, False), (0xFF8A7A9E, core, False)]
    passes += [(0xF0000000, core + 4, True), (0xFFD08CFF, core, True)]
    for argb, width, wanted in passes:
        g.pen(argb, width)
        g.line_runs(lines, lambda pts, j, wanted=wanted: significant(pts, j) == wanted)


def draw_model_match_cells(g: Canvas, w, h, ev: dict) -> None:
    """Magenta frame: the cell's mean residual is 3 sigma or more off the
    model; green: it agrees within its noise."""
    n = ev["grid_size"]
    cw, ch = w / n, h / n
    lw = max(2, round(min(cw, ch) * 0.015))
    for b in ev["model"].judged_cells:
        x0, y0 = b["col"] * cw + lw, b["row"] * ch + lw
        g.pen(0xC0000000, lw + 2)
        g.rect(x0, y0, x0 + cw - 2 * lw, y0 + ch - 2 * lw)
        g.pen(0xFFFF4FD8 if b["z"] >= 3 else 0xFF4CFF6A, lw)
        g.rect(x0, y0, x0 + cw - 2 * lw, y0 + ch - 2 * lw)


def draw_shape_defects(g: Canvas, w, h, defects) -> None:
    size = max(10, round(min(w, h) / 60))
    g.set_font(font(round(size * 0.9), True))
    for d in defects:
        color = 0xFFFFE45C if d["index"] > 0 else 0xFF5CE1FF
        for argb, width in ((0xC0000000, 5), (color, 3)):
            g.pen(argb, width)
            g.ellipse(d["x"] - size, d["y"] - size, d["x"] + size, d["y"] + size)
        label = ("+" if d["index"] > 0 else "−") + ("½" if abs(d["index"]) == 0.5 else "1")
        g.text_with_halo(d["x"] + size * 1.2, d["y"] - size * 0.6, label, color, round(size * 0.9))


def draw_tracking_marker(g: Canvas, w, h, tracking: dict, subtracted: bool) -> None:
    """A double-headed bar along the common elongation in the lower left
    corner (full length = ellipticity 0.3), with a label."""
    color = 0xFF60FF60
    fs = round(max(28, max(w, h) * 0.009))
    g.set_font(font(fs, True))
    box_r = max(60, min(w, h) * 0.06)
    cx, cy = fs + box_r, h - 3 * fs - box_r
    g.pen(0x90000000, 5)
    g.ellipse(cx - box_r, cy - box_r, cx + box_r, cy + box_r)
    g.pen(0xA0FFFFFF, 2)
    g.ellipse(cx - box_r, cy - box_r, cx + box_r, cy + box_r)
    half = box_r * min(1.0, tracking["ellipticity"] / 0.3)
    a = math.radians(tracking["angle_deg"])
    dx, dy = math.cos(a) * half, math.sin(a) * half
    head = max(8, half * 0.25)
    for width, argb in ((11, 0xB0000000), (5, color)):
        g.pen(argb, width)
        g.line(cx - dx, cy - dy, cx + dx, cy + dy)
        if half > 2:
            for sgn in (-1, 1):
                ex, ey = cx + sgn * dx, cy + sgn * dy
                ang = math.atan2(sgn * dy, sgn * dx)
                g.line(ex, ey, ex - head * math.cos(ang - 0.45), ey - head * math.sin(ang - 0.45))
                g.line(ex, ey, ex - head * math.cos(ang + 0.45), ey - head * math.sin(ang + 0.45))
    g.text_with_halo(cx - box_r, cy + box_r + fs * 1.2,
                     f"Tracking: eps {tracking['ellipticity']:.3f} @ {tracking['angle_deg']:.0f}°", color, fs)
    g.text_with_halo(cx - box_r, cy + box_r + fs * 2.4,
                     "(subtracted from the map)" if subtracted else "(NOT subtracted - contained in the map)", color, fs)


def draw_shape_layers(g: Canvas, analysis: Analysis, stars: list[Star], s, log: Log) -> None:
    w, h = analysis.w, analysis.h
    ev = shape_evaluation_for(analysis, s)
    draw_cell_grid(g, w, h, ev["grid_size"])
    radius = math.hypot(w, h) * (s.streamline_radius_percent / 100)
    if s.show_streamlines:
        def compute():
            log.write("Computing streamlines ...")
            t0 = time.monotonic()
            gate = (lambda x, y: shape_significant_at(ev["grid"], x, y)) if s.shape_significant_only else None
            lines = generate_streamlines(stars, w, h, radius, gate)
            log.write(f"Streamlines finished after {time.monotonic() - t0:.1f} s ({len(lines)} lines).")
            return lines
        lines = cached_layer(analysis, "streamlines",
                             (s.subtract_tracking, s.streamline_radius_percent, s.shape_significant_only), compute)
        draw_streamlines(g, lines, ev["grid"])

    if not s.hide_ellipses:
        ecc_max = max((st.eccentricity for st in stars), default=0.0) or 1.0
        for st in stars:
            a = st.eccentricity * s.vector_scale * 100 / 2
            b = a * math.sqrt(max(0.0, 1 - st.eccentricity ** 2))
            g.pen(ecc_color(st.eccentricity, ecc_max), 2)
            g.oriented_ellipse(st.x, st.y, a, b, -st.rotation)
            g.pen(0xFFFFFFFF, 1)
            g.ellipse(st.x - 1.5, st.y - 1.5, st.x + 1.5, st.y + 1.5)

    if s.shape_show_model and ev["model"]:
        model = ev["model"]
        lines = cached_layer(analysis, "model_streamlines", (s.subtract_tracking, s.streamline_radius_percent),
                             lambda: trace_field(lambda x, y: shape_model_field_at(model, x, y), w, h, radius, None))
        draw_model_streamlines(g, lines, w, h, model, s.shape_model_significant_only)
    if s.shape_show_match and ev["model"]:
        draw_model_match_cells(g, w, h, ev)
    if s.shape_show_cells:
        draw_shape_cells(g, w, h, ev, s.vector_scale)
    if s.shape_show_defects:
        draw_shape_defects(g, w, h, ev["defects"])
    if analysis.tracking:
        draw_tracking_marker(g, w, h, analysis.tracking, s.subtract_tracking)


# ---------------------------------------------------------------------------
# Tilt

def draw_siril_tilt_polygon(g: Canvas, w, h, tilt: dict) -> None:
    """Siril's tilt quadrilateral, the corners fixed at the quadrant centers,
    the inner circle of the center value drawn as a circle."""
    cx, cy = w / 2, h / 2
    tl, tr = (cx - w / 4, cy - h / 4), (cx + w / 4, cy - h / 4)
    bl, br = (cx - w / 4, cy + h / 4), (cx + w / 4, cy + h / 4)
    inner = 0.25 * math.sqrt(cx * cx + cy * cy)
    color = 0xFFFFCCB2
    segments = [(tl, tr), (bl, br), (tl, bl), (br, tr), (tr, bl), (br, tl)]
    for argb, width in ((0xB0000000, 15), (color, 7)):
        g.pen(argb, width)
        for p0, p1 in segments:
            g.line(*p0, *p1)
    g.ellipse(cx - inner, cy - inner, cx + inner, cy + inner)
    fs = round(max(36, max(w, h) * 0.012))
    g.set_font(font(fs, True))
    g.text_with_halo(tl[0], tl[1] - fs, f"{tilt['m1']:.2f}", color, fs)
    g.text_with_halo(tr[0], tr[1] - fs, f"{tilt['m2']:.2f}", color, fs)
    g.text_with_halo(bl[0], bl[1] + fs, f"{tilt['m3']:.2f}", color, fs)
    g.text_with_halo(br[0], br[1] + fs, f"{tilt['m4']:.2f}", color, fs)
    g.text_with_halo(cx, cy + fs, f"{tilt['m_inner']:.2f}", color, fs)
    g.pen(color, 2)
    g.ellipse(cx - 3, cy - 3, cx + 3, cy + 3)


def draw_tilt_axis(g: Canvas, w, h, axis: dict) -> None:
    cx, cy = w / 2, h / 2
    rad = math.radians(axis["direction_deg"])
    dx, dy = math.cos(rad), math.sin(rad)
    tx = (w / 2) / abs(dx) if abs(dx) > 1e-9 else math.inf
    ty = (h / 2) / abs(dy) if abs(dy) > 1e-9 else math.inf
    t = min(tx, ty)
    p1, p2 = (cx + t * dx, cy + t * dy), (cx - t * dx, cy - t * dy)
    color = 0xFF66E0FF
    g.pen(0xB0000000, 9)
    g.line(*p1, *p2)
    g.pen(color, 4)
    g.line(*p1, *p2)
    label = (f"Tilt axis: {axis['angle_deg']:.3f}° (dir. {axis['direction_deg']:.0f}°)" if axis["angle_deg"] is not None
             else f"Tilt axis direction: {axis['direction_deg']:.0f}°")
    fs = round(max(28, max(w, h) * 0.009))
    g.set_font(font(fs, True))
    lx = max(fs, min(p1[0], w - fs - g.text_width(label)))
    ly = max(fs * 2, min(p1[1], h - fs))
    g.text_with_halo(lx, ly, label, color, fs)


def draw_tilt_layers(g: Canvas, w, h, stars: list[Star], s, optics: Optics) -> None:
    tilt = compute_siril_tilt(stars, w, h)
    if tilt is None:
        _note(g, w, h, "Not every quadrant and ring has stars - no tilt evaluation.")
        return
    draw_siril_tilt_polygon(g, w, h, tilt)
    if s.show_tilt_axis:
        draw_tilt_axis(g, w, h, compute_tilt_axis(tilt, w, h, optics.pixel_pitch_um, optics.focal_length_mm,
                                                  optics.aperture_mm))


def format_tilt_evaluation(tilt: dict | None, axis: dict | None, scale: float, unit: str) -> list[str]:
    if tilt is None:
        return ["Quadrants: not every quadrant and ring has stars - not evaluated."]

    def fw(v):
        return f"{v * scale:.2f}" + (" px" if unit == "px" else unit)
    q = [tilt["m1"], tilt["m2"], tilt["m3"], tilt["m4"]]
    mean = sum(q) / 4
    best, worst = min(q), max(q)
    lines = ["Quadrants (25%-trimmed mean FWHM):"]
    for i, name in enumerate(["Top left", "Top right", "Bottom left", "Bottom right"]):
        lines.append(f"   {name}: {fw(q[i])} ({100 * (q[i] - mean) / mean:+.0f}% of the mean)")
    lines.append(f"Sensor tilt (Siril): {fw(worst - best)} between the softest and the sharpest quadrant "
                 f"({100 * (worst - best) / mean:.0f}% of the mean).")
    lines.append(f"Off-axis aberration (Siril): {fw(tilt['m_outer'] - tilt['m_inner'])} (outer ring "
                 f"{fw(tilt['m_outer'])} against inner circle {fw(tilt['m_inner'])}).")
    if axis:
        lines.append(f"Tilt axis: direction {axis['direction_deg']:.0f}°" + (
            f", tilt angle {axis['angle_deg']:.3f}°" if axis["angle_deg"] is not None else
            " (the tilt angle needs pixel pitch, focal length and aperture)") + ".")
    return lines


# ---------------------------------------------------------------------------
# Coma

def draw_coma_streamlines(g: Canvas, w, h, lines) -> None:
    """Orange lines with arrowheads along the flow."""
    color = 0xFFFFB040
    head = max(12, max(w, h) * 0.006)
    spacing = max(6 * head, 0.05 * math.hypot(w, h))

    def draw_head(x, y, ang):
        g.line(x, y, x - head * math.cos(ang - 0.45), y - head * math.sin(ang - 0.45))
        g.line(x, y, x - head * math.cos(ang + 0.45), y - head * math.sin(ang + 0.45))

    for argb, width in ((0xA0000000, 5), (color, 2)):
        g.pen(argb, width)
        for pts in lines:
            g.polyline(pts)
            length = sum(math.hypot(pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]) for j in range(1, len(pts)))
            n = max(1, int(math.floor(length / spacing)))
            nxt = (length - (n - 1) * spacing) / 2
            run = 0.0
            for j in range(1, len(pts)):
                if n <= 0:
                    break
                dx, dy = pts[j][0] - pts[j - 1][0], pts[j][1] - pts[j - 1][1]
                seg = math.hypot(dx, dy)
                while n > 0 and seg > 0 and run + seg >= nxt:
                    t = (nxt - run) / seg
                    draw_head(pts[j - 1][0] + t * dx, pts[j - 1][1] + t * dy, math.atan2(dy, dx))
                    nxt += spacing
                    n -= 1
                run += seg


def draw_star_asymmetry_arrows(g: Canvas, w, h, stars: list[Star], vector_scale, min_sigma, by_strength,
                               log: Log) -> None:
    """One arrow per star toward its flare; below min_sigma x the per-star
    noise (from the inner field) skipped. Color: alignment with the ellipse
    axis (green parallel, red perpendicular, gray round) or strength."""
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    inner, mags = [], []
    for s in stars:
        if s.m3_x is None:
            continue
        mags.append(math.hypot(s.m3_x, s.m3_y))
        if math.hypot(s.x - cx, s.y - cy) < 0.25 * r_max:
            inner += [s.m3_x, s.m3_y]
    if not mags:
        return
    noise = 1.4826 * median_of([abs(v) for v in inner]) if len(inner) >= 20 else 0.0
    min_mag = min_sigma * noise
    srt = sorted(mags)
    mag_max = srt[int(math.floor(0.95 * (len(srt) - 1)))] or 1.0
    drawn = 0
    for s in stars:
        if s.m3_x is None:
            continue
        mag = math.hypot(s.m3_x, s.m3_y)
        if mag < min_mag or mag <= 0:
            continue
        length = min(150.0, mag * 35 * vector_scale)
        ux, uy = s.m3_x / mag, s.m3_y / mag
        x1, y1 = s.x + ux * length, s.y + uy * length
        if by_strength:
            col = ecc_color(mag, mag_max)
        elif ellipticity_from_ecc(s.eccentricity) < 0.05:
            col = 0xFFA0A0A0
        else:
            t = abs(math.cos(math.atan2(uy, ux) - star_image_angle(s)))
            r = round(255 * min(1.0, 2 * (1 - t)))
            gr = round(255 * min(1.0, 2 * t))
            col = 0xFF000000 | (r << 16) | (gr << 8) | 0x30
        g.pen(col, 2)
        g.arrow(s.x, s.y, x1, y1, max(4, length * 0.3))
        drawn += 1
    log.write(f"Per-star asymmetry arrows: {drawn} of {len(mags)} drawn (noise {noise:.3f} from the inner field, "
              f"threshold {min_mag:.3f} = {min_sigma:.1f} sigma; color: "
              f"{'strength' if by_strength else 'alignment with the ellipse axis'}).")


def draw_coma_overlay(g: Canvas, w, h, stars: list[Star], fit, vector_scale, show_arrows: bool) -> None:
    """Median asymmetry arrows on a coarse grid and the coma-free point with
    its bootstrap uncertainty."""
    nx, ny = 9, 6
    cw, ch = w / nx, h / ny
    scale = min(cw, ch) * 0.45 * vector_scale
    if show_arrows:
        for a in coma_grid_arrows(stars, w, h, nx, ny):
            x1, y1 = a["x"] + a["vx"] * scale, a["y"] + a["vy"] * scale
            head = max(8, math.hypot(x1 - a["x"], y1 - a["y"]) * 0.3)
            for argb, width in ((0xB0000000, 9), (0xFFFFA020, 4)):
                g.pen(argb, width)
                g.arrow(a["x"], a["y"], x1, y1, head)
    if fit is not None and fit["significant"] and -w < fit["x0"] < 2 * w and -h < fit["y0"] < 2 * h:
        color = 0xFFFF40FF
        rx = max(10, fit["x0_sd"] if math.isfinite(fit["x0_sd"]) else 10)
        ry = max(10, fit["y0_sd"] if math.isfinite(fit["y0_sd"]) else 10)
        arm = max(30, max(w, h) * 0.015)
        x0, y0 = fit["x0"], fit["y0"]
        for argb, width in ((0xB0000000, 9), (color, 4)):
            g.pen(argb, width)
            g.ellipse(x0 - rx, y0 - ry, x0 + rx, y0 + ry)
            g.line(x0 - arm, y0, x0 + arm, y0)
            g.line(x0, y0 - arm, x0, y0 + arm)
        fs = round(max(28, max(w, h) * 0.009))
        g.set_font(font(fs, True))
        label = "Coma-free point"
        lx = max(fs, min(x0 + arm + 6, w - fs - g.text_width(label)))
        ly = max(fs * 2, min(y0 - arm, h - fs))
        g.text_with_halo(lx, ly, label, color, fs)


def draw_coma_layers(g: Canvas, analysis: Analysis, stars: list[Star], s, show_coma: bool, log: Log) -> None:
    w, h = analysis.w, analysis.h
    if not show_coma:
        _note(g, w, h, "PSF asymmetry not measured - enable \"Measure PSF asymmetry\" on Maps > Coma.")
        log.warning("Coma map: needs \"Measure PSF asymmetry\" - only the background is drawn.")
        return
    if s.show_coma_streamlines:
        def compute():
            radius = math.hypot(w, h) * (s.coma_streamline_radius_percent / 100)
            log.write("Computing coma streamlines ...")
            t0 = time.monotonic()
            res = generate_coma_streamlines(stars, w, h, radius, s.coma_streamline_min_percent)
            log.write(f"Coma streamlines finished after {time.monotonic() - t0:.1f} s ({len(res['lines'])} lines, "
                      f"stop below |m3| = {res['min_mag']:.3f}).")
            return res["lines"]
        lines = cached_layer(analysis, "coma_streamlines",
                             (s.coma_streamline_radius_percent, s.coma_streamline_min_percent), compute)
        draw_coma_streamlines(g, w, h, lines)
    if s.show_star_asym_arrows:
        draw_star_asymmetry_arrows(g, w, h, stars, s.vector_scale, s.star_asym_min_sigma,
                                   s.star_asym_color_by_strength, log)
    draw_coma_overlay(g, w, h, stars, analysis.coma_fit, s.vector_scale, s.show_coma_arrows)
