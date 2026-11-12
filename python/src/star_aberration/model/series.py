"""Series analysis: several unregistered light frames of one night, before
and after a meridian flip. After the flip, gravity pulls on the optical train
from the other side: a value that changes with the pier side is mechanical
(sag, play, mirror flop, balance), one that stays the same is fixed in the
optical train.

The pier side of a frame, in this order:
    manual      set by the user in the file list
    PIERSIDE    written by N.I.N.A. and most ASCOM/INDI capture programs
    angle       a camera angle keyword (ROTATOR, POSANGLE, ANGLE, CROTA2)
                turns by 180 degrees at the flip: the frames fall into two
                groups, named by the hour angles of their members
    hour angle  from RA, the time and the site longitude: before the
                meridian no flip yet, well after it one has; close after
                the meridian the side stays unknown
Sides are named as N.I.N.A. writes PIERSIDE: West before the meridian
(telescope pointing east), East after the flip.

Pass 2 pools the stars of all frames (and of each side) after taking out
each frame's dynamic tracking and scaling its FWHM to the median center
FWHM: the static optics with about sqrt(n) smaller errors.
"""

from __future__ import annotations

import datetime as _dt
import html
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .analysis import Analysis, Cancelled, Progress, analyze_image, report_analysis, stars_for
from .coma import fit_coma_field
from .fwhm_grid import FWHM_GRID_SIZE, evaluate_fwhm_grid
from .image import ImageData, load_image, read_keywords
from .log import Log, SILENT
from .optics import (GUIDING_UNKNOWN, Optics, gmst_deg, guiding_causes, keyword_number, keyword_values,
                     parse_fits_date, resolve_optics)
from .star import Star
from .stats import median_of, robust_spread
from .tracking import apply_tracking_correction, compute_tracking_component

SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B, SIDE_UNKNOWN = "West", "East", "Side A", "Side B", "?"
SERIES_HA_WEST_MAX = -0.05  # h: up to here, the mount cannot have flipped yet
SERIES_HA_EAST_MIN = 0.5  # h: from here on, it has flipped
SERIES_ANGLE_KEYS = ["ROTATOR", "POSANGLE", "ANGLE", "CROTA2"]
SIP_TERMS = ["A_2_0", "A_1_1", "A_0_2", "B_2_0", "B_1_1", "B_0_2"]


@dataclass
class SeriesFrame:
    path: str
    name: str
    time_ms: float | None = None
    ha_h: float | None = None
    alt_deg: float | None = None
    pier_side: str | None = None
    angle_deg: float | None = None
    angle_key: str | None = None
    focus_pos: float | None = None
    temp_c: float | None = None
    bayer: bool = False
    sip: dict | None = None
    pitch_um: float | None = None
    focal_mm: float | None = None
    side: str = SIDE_UNKNOWN
    side_source: str = ""
    manual_side: str | None = None
    header_error: str | None = None
    metrics: dict | None = None
    error: str | None = None
    bayer_warning: bool = False
    measurement: dict | None = None  # kept for pass 2
    map_image: Any = None  # a reduced copy of its map (QImage), set by the controller
    map_key: str = ""
    output_files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plate-solve distortion (SIP)

def sip_corner_shifts(frame: SeriesFrame) -> list[float] | None:
    """The quadratic SIP distortion as the shift (px) of the four corners of
    the solved image: dx, dy of TL, TR, BL, BR. Quadratic terms do not depend
    on the reference pixel."""
    if not frame.sip:
        return None
    t, u0, v0 = frame.sip["terms"], frame.sip["w"] / 2, frame.sip["h"] / 2
    out = []
    for cu, cv in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        u, v = cu * u0, cv * v0
        out.append(t["A_2_0"] * u * u + t["A_1_1"] * u * v + t["A_0_2"] * v * v)
        out.append(t["B_2_0"] * u * u + t["B_1_1"] * u * v + t["B_0_2"] * v * v)
    return out


def sip_max_shift(c: list[float]) -> float:
    return max((math.hypot(c[i], c[i + 1]) for i in range(0, len(c), 2)), default=0.0)


def sip_tilt_equivalent_deg(frame: SeriesFrame) -> float | None:
    """The sensor tilt a pure perspective would need for the perspective part
    of the quadratic terms. Real tilts stay below about 0.2 degrees."""
    if not frame.sip or not (frame.pitch_um or 0) > 0 or not (frame.focal_mm or 0) > 0:
        return None
    t = frame.sip["terms"]
    p = -0.5 * (t["A_2_0"] + t["B_1_1"])
    q = -0.5 * (t["A_1_1"] + t["B_0_2"])
    f_px = frame.focal_mm * 1000 / frame.pitch_um
    return math.degrees(math.atan(math.hypot(p, q) * f_px))


# ---------------------------------------------------------------------------
# Frames and sides

def read_series_frame(path: str) -> SeriesFrame:
    """What the header of a light frame tells about when and where it was
    taken."""
    f = SeriesFrame(path=path, name=Path(path).name)
    try:
        v = keyword_values(read_keywords(path))
    except Exception as x:  # any reader error is the user's information
        f.header_error = str(x)
        return f
    t = parse_fits_date(v.get("DATE-AVG"))
    if t is None:
        t = parse_fits_date(v.get("DATE-OBS"))
        exposure = keyword_number(v, ["EXPTIME", "EXPOSURE"])
        if t is not None and exposure:
            t += 500 * exposure["value"]
    f.time_ms = t
    ra = keyword_number(v, ["RA", "OBJCTRA"])
    ra_deg = (15 * ra["value"] if ra["sexagesimal"] else ra["value"]) if ra else None
    dec = keyword_number(v, ["DEC", "OBJCTDEC"])
    lon = keyword_number(v, ["SITELONG", "LONG-OBS", "OBSGEO-L"])
    lat = keyword_number(v, ["SITELAT", "LAT-OBS", "OBSGEO-B"])
    if t is not None and ra_deg is not None and lon:
        f.ha_h = ((((gmst_deg(t) + lon["value"] - ra_deg) % 360) + 540) % 360 - 180) / 15
    alt = keyword_number(v, ["CENTALT", "OBJCTALT"])
    if alt:
        f.alt_deg = alt["value"]
    elif f.ha_h is not None and dec and lat:
        r = math.pi / 180
        f.alt_deg = math.asin(math.sin(lat["value"] * r) * math.sin(dec["value"] * r) +
                              math.cos(lat["value"] * r) * math.cos(dec["value"] * r) * math.cos(15 * f.ha_h * r)) / r
    pier = v.get("PIERSIDE", "").upper()
    f.pier_side = SIDE_WEST if "WEST" in pier else SIDE_EAST if "EAST" in pier else None
    angle = keyword_number(v, SERIES_ANGLE_KEYS)
    if angle:
        f.angle_deg, f.angle_key = angle["value"], angle["name"]
    focus = keyword_number(v, ["FOCPOS", "FOCUSPOS"])
    f.focus_pos = focus["value"] if focus else None
    temp = keyword_number(v, ["FOCTEMP", "FOCUSTEM", "AMBTEMP"])
    f.temp_c = temp["value"] if temp else None
    f.bayer = len(v.get("BAYERPAT", "")) > 0
    pitch, focal = keyword_number(v, ["XPIXSZ"]), keyword_number(v, ["FOCALLEN"])
    f.pitch_um = pitch["value"] if pitch else None
    f.focal_mm = focal["value"] if focal else None
    order = keyword_number(v, ["A_ORDER"])
    if order and order["value"] >= 2:
        terms = {}
        complete = True
        for t_name in SIP_TERMS:
            k = keyword_number(v, [t_name])
            terms[t_name] = k["value"] if k else float("nan")
            complete = complete and k is not None
        w, h = keyword_number(v, ["IMAGEW", "NAXIS1"]), keyword_number(v, ["IMAGEH", "NAXIS2"])
        nw, nh = keyword_number(v, ["NAXIS1"]), keyword_number(v, ["NAXIS2"])
        if complete and w and h:
            f.sip = {"order": order["value"], "terms": terms, "w": w["value"], "h": h["value"],
                     "file_w": nw["value"] if nw else None, "file_h": nh["value"] if nh else None}
    return f


def assign_series_sides(frames: list[SeriesFrame]) -> None:
    def opposite(s):
        return SIDE_EAST if s == SIDE_WEST else SIDE_WEST

    def by_hour_angle(f):
        if f.ha_h is None:
            return SIDE_UNKNOWN
        return SIDE_WEST if f.ha_h <= SERIES_HA_WEST_MAX else SIDE_EAST if f.ha_h >= SERIES_HA_EAST_MIN else SIDE_UNKNOWN

    group: list[int | None] = [None] * len(frames)
    ref = None
    for i, f in enumerate(frames):
        if f.pier_side is not None or f.angle_deg is None:
            continue
        if ref is None:
            ref = f.angle_deg
        d = abs((((f.angle_deg - ref) % 360) + 540) % 360 - 180)
        group[i] = 0 if d < 45 else 1 if d > 135 else None
    group_side: list[str | None] = []
    for g in (0, 1):
        west = sum(1 for i, f in enumerate(frames) if group[i] == g and by_hour_angle(f) == SIDE_WEST)
        east = sum(1 for i, f in enumerate(frames) if group[i] == g and by_hour_angle(f) == SIDE_EAST)
        group_side.append(SIDE_WEST if west > east else SIDE_EAST if east > west else None)
    if group_side[0] is not None and group_side[0] == group_side[1]:
        group_side = [None, None]
    if group_side[0] is None and group_side[1] is not None:
        group_side[0] = opposite(group_side[1])
    if group_side[1] is None and group_side[0] is not None:
        group_side[1] = opposite(group_side[0])
    if group_side[0] is None:
        group_side = [SIDE_A, SIDE_B]
    for i, f in enumerate(frames):
        if f.manual_side:
            f.side, f.side_source = f.manual_side, "manual"
        elif f.pier_side:
            f.side, f.side_source = f.pier_side, "PIERSIDE"
        elif group[i] is not None:
            f.side, f.side_source = group_side[group[i]], f"{f.angle_key} angle"
        else:
            f.side = by_hour_angle(f)
            f.side_source = ("hour angle" if f.side != SIDE_UNKNOWN else
                             "near the meridian" if f.ha_h is not None else "no data")


def direction_name(deg: float) -> str:
    names = ["right", "lower right", "bottom", "lower left", "left", "upper left", "top", "upper right"]
    return names[int(math.floor(((deg % 360) + 360) % 360 / 45 + 0.5)) % 8]


# ---------------------------------------------------------------------------
# The compared values

@dataclass
class SeriesMetric:
    key: str
    label: str
    get: Callable[[dict, SeriesFrame | None], Any]
    show: Callable[[Any], str]
    changed: str
    vector: bool = False
    min_diff: float = 0.0
    rel_min_diff: float = 0.0
    stable: str = ""
    stable_min: float = 0.0
    spread: Callable[[float], str] | None = None
    show_diff: Callable[[Any, Any], str] | None = None


def _tilt_vector(m, f=None):
    s = m.get("surface")
    if s is None:
        return None
    rel = (s["f_bad"] - s["f_good"]) / max(1e-6, 0.5 * (s["f_bad"] + s["f_good"]))
    a = math.radians(s["tilt_dir_deg"])
    return [rel * math.cos(a), rel * math.sin(a)]


def _coma_point(m, f=None):
    c = m.get("coma")
    return [(c["x0"] - m["w"] / 2) / c["r_max"], (c["y0"] - m["h"] / 2) / c["r_max"]] if c else None


SERIES_METRICS = [
    SeriesMetric("fwhmCenter", "FWHM center", lambda m, f=None: m["surface"]["f_center"] if m.get("surface") else None,
                 lambda v: f"{v:.2f} px",
                 "The focus differs between the sides: refocusing, temperature, or focuser slip under gravity.",
                 rel_min_diff=0.05),
    SeriesMetric("fwhmEdge", "FWHM edge", lambda m, f=None: m["surface"]["f_edge"] if m.get("surface") else None,
                 lambda v: f"{v:.2f} px",
                 "The field edge changes with the side: play in the focuser or the corrector/flattener, or a "
                 "different focus.", rel_min_diff=0.05),
    SeriesMetric("tilt", "Tilt", _tilt_vector,
                 lambda v: f"{100 * math.hypot(v[0], v[1]):.0f}% softer toward the "
                           f"{direction_name(math.degrees(math.atan2(v[1], v[0])))}",
                 "The tilt changes with the side: the camera, the focuser or the adapter sags or has play under "
                 "gravity. Tighten the focuser lock, the camera connection and the adapter threads before adjusting "
                 "the tilt - an adjustment would only fit one side.",
                 vector=True, min_diff=0.03,
                 stable="The tilt is the same on both sides: it is fixed in the optical train, so adjusting it "
                        "(tilt plate, adapter) is worthwhile.", stable_min=0.05),
    SeriesMetric("gridCorners", "Corners / center (grid)",
                 lambda m, f=None: m["grid"]["corner_ratio"] if m.get("grid") else None, lambda v: f"x{v:.2f}",
                 "The corners soften differently relative to the center on the two sides: the field curvature or "
                 "the corrector/flattener spacing changes under gravity (play in the focuser or the drawtube), or "
                 "the focus differs.", min_diff=0.05),
    SeriesMetric("eRad", "Edge elongation eps_rad", lambda m, f=None: m["rings"]["e_rad"] if m.get("rings") else None,
                 lambda v: f"{v:+.3f}",
                 "The radial/tangential pattern at the edge changes with the side: play in the corrector/flattener "
                 "or the focuser drawtube, or a different focus. Fix that before changing the spacing.",
                 min_diff=0.02,
                 stable="The edge pattern is the same on both sides: the spacing verdict of a single frame holds.",
                 stable_min=0.04),
    SeriesMetric("tracking", "Tracking elongation",
                 lambda m, f=None: m["tracking"]["ellipticity"] if m.get("tracking") else None, lambda v: f"{v:.3f}",
                 "Tracking differs between the sides: balance (the mount is east- or west-heavy after the flip), "
                 "DEC backlash, cable drag, or wind from one side.", min_diff=0.02),
    SeriesMetric("comaK", "Coma k", lambda m, f=None: m["coma"]["k"] if m.get("coma") else None, lambda v: f"{v:+.3f}",
                 "The coma strength changes with the side: the corrector/flattener or its spacing has play."),
    SeriesMetric("comaPoint", "Coma-free point", _coma_point,
                 lambda v: f"{100 * math.hypot(v[0], v[1]):.0f}% of the half diagonal toward the "
                           f"{direction_name(math.degrees(math.atan2(v[1], v[0])))}",
                 "The collimation shifts with the side: mirror flop (in an SCT, Maksutov or RASA the primary mirror "
                 "moved by the focuser), a loose secondary or corrector, or a sagging focuser (in a refractor: the "
                 "focuser, the flattener or the lens cell). Secure the optics before collimating.",
                 vector=True, min_diff=0.1,
                 stable="The coma-free point is off center on both sides alike: the collimation is off, but stable "
                        "- collimating is worthwhile.", stable_min=0.15),
    SeriesMetric("sipDistortion", "Plate-solve distortion (SIP)",
                 lambda m, f=None: sip_corner_shifts(f) if f else None,
                 lambda v: f"corners shifted by up to {sip_max_shift(v):.1f} px",
                 "The geometry of the image changes with the side: a part of the optical train shifts or tilts "
                 "under gravity - in a Newtonian, SCT, Maksutov or RASA typically the primary mirror (mirror flop), "
                 "in a refractor the lens cell, or a coma corrector, flattener or the focuser moving in the drawtube. "
                 "If the coma-free point moves as well, that supports it. A change of sign can also come from the "
                 "plate solver (a solution in a frame that turns with the sky): to be sure, solve one frame per side "
                 "with PixInsight's ImageSolver and compare.",
                 vector=True, min_diff=1.0,
                 stable="The plate-solve distortion is the same on both sides: nothing in the optical train shifts "
                        "measurably under gravity.",
                 spread=lambda v: f"{v:.1f} px",
                 show_diff=lambda a, b: f"a corner moves by up to {sip_max_shift([y - x for x, y in zip(a, b)]):.1f} px "
                                        "between the sides"),
]


def series_metric(key: str) -> SeriesMetric:
    return next(m for m in SERIES_METRICS if m.key == key)


def _norm(v):
    return math.sqrt(sum(x * x for x in v))


def series_stats(values, vector: bool) -> dict | None:
    if not values:
        return None
    if not vector:
        return {"n": len(values), "median": median_of(values), "sd": robust_spread(values)}
    median, sum2 = [], 0.0
    for k in range(len(values[0])):
        c = [v[k] for v in values]
        median.append(median_of(c))
        sd = robust_spread(c)
        sum2 += sd * sd
    return {"n": len(values), "median": median, "sd": math.sqrt(sum2 / len(median))}


def _finite(v, vector):
    if v is None:
        return False
    return all(math.isfinite(x) for x in v) if vector else math.isfinite(v)


def compare_series(frames: list[SeriesFrame]) -> dict:
    """{sides, counts, rows, notes, measured, total}; each row: metric, stats
    per side, the difference and a verdict ("changes", "stable", "unclear",
    "few") and a drift over the session."""
    measured = [f for f in frames if f.metrics is not None]
    counts: dict[str, int] = {}
    for f in measured:
        counts[f.side] = counts.get(f.side, 0) + 1
    sides = [s for s in (SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B) if counts.get(s, 0) > 0][:2]
    notes = []
    solved = [f for f in measured if f.sip]
    sizes = sorted({f"{f.sip['w']:g}x{f.sip['h']:g}" for f in solved})
    sip_comparable = len(sizes) == 1
    if solved:
        if not sip_comparable:
            notes.append(f"The plate solves refer to images of different sizes ({', '.join(sizes)} px): their "
                         "distortion terms are not compared.")
        elif len(solved) < len(measured):
            notes.append(f"Only {len(solved)} of {len(measured)} measured frames carry a plate solve (SIP); the "
                         "distortion is compared for those.")
        f0 = solved[0]
        if sip_comparable and f0.sip["file_w"] is not None and (f0.sip["w"] != f0.sip["file_w"] or
                                                                 f0.sip["h"] != f0.sip["file_h"]):
            notes.append(f"The plate solves were made on a {f0.sip['w']:.0f} x {f0.sip['h']:.0f} px version of the "
                         f"frames (the files have {f0.sip['file_w']:.0f} x {f0.sip['file_h']:.0f} px), e.g. inside "
                         "the capture software; the distortion refers to that image.")

    rows = []
    for metric in SERIES_METRICS:
        def values_of(side):
            out = []
            for f in measured:
                if f.side != side or (metric.key == "sipDistortion" and not sip_comparable):
                    continue
                v = metric.get(f.metrics, f)
                if _finite(v, metric.vector):
                    out.append(v)
            return out
        row = {"metric": metric, "stats": [series_stats(values_of(s), metric.vector) for s in sides],
               "diff": None, "verdict": None, "drift": None}
        if len(sides) == 2 and row["stats"][0] and row["stats"][1]:
            a, b = row["stats"]
            if metric.vector:
                row["diff"] = _norm([y - x for x, y in zip(a["median"], b["median"])])
                size = 0.5 * (_norm(a["median"]) + _norm(b["median"]))
            else:
                row["diff"] = b["median"] - a["median"]
                size = 0.5 * (abs(a["median"]) + abs(b["median"]))
            min_diff = metric.rel_min_diff * size if metric.rel_min_diff else metric.min_diff
            se = 1.253 * math.sqrt(a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"])
            significant = math.isfinite(se) and abs(row["diff"]) > 3 * se
            if a["n"] < 3 or b["n"] < 3:
                row["verdict"] = "few"
            elif min_diff > 0 and abs(row["diff"]) < min_diff:
                row["verdict"] = "stable"
            elif significant:
                row["verdict"] = "changes"
            else:
                row["verdict"] = "unclear" if min_diff > 0 else "stable"
        if not metric.vector:
            t, r = [], []
            for k, s in enumerate(sides):
                if not row["stats"][k]:
                    continue
                for f in measured:
                    v = metric.get(f.metrics, f)
                    if f.side == s and f.time_ms is not None and _finite(v, False):
                        t.append(f.time_ms / 3600000)
                        r.append(v - row["stats"][k]["median"])
            if len(t) >= 6:
                mt, mr = sum(t) / len(t), sum(r) / len(r)
                stt = sum((x - mt) ** 2 for x in t)
                srr = sum((y - mr) ** 2 for y in r)
                st_r = sum((x - mt) * (y - mr) for x, y in zip(t, r))
                if stt > 0 and srr > 0:
                    corr = st_r / math.sqrt(stt * srr)
                    slope = st_r / stt
                    span = max(t) - min(t)
                    allst = [s for s in row["stats"] if s]
                    size = sum(abs(s["median"]) for s in allst) / len(allst)
                    min_diff = metric.rel_min_diff * size if metric.rel_min_diff else metric.min_diff
                    if abs(corr) >= 0.6 and abs(slope * span) >= max(min_diff, 1e-9):
                        row["drift"] = {"per_hour": slope, "corr": corr, "hours": span}
        rows.append(row)

    if len(sides) == 2:
        def focus_of(side):
            v = [f.focus_pos for f in measured if f.side == side and f.focus_pos is not None]
            return median_of(v) if v else None
        fa, fb = focus_of(sides[0]), focus_of(sides[1])
        if fa is not None and fb is not None and fa != fb:
            notes.append(f"The focuser position differs between the sides ({sides[0]} {fa:.0f}, {sides[1]} {fb:.0f} "
                         "steps): the frames were refocused, so differences in FWHM, edge pattern and coma may come "
                         "from that rather than from the flip. Tilt, collimation and tracking are hardly affected.")
    unknown = sum(1 for f in frames if f.metrics is not None and f.side == SIDE_UNKNOWN)
    if unknown:
        notes.append(f"{unknown} measured frame(s) have no known side (close after the meridian or without "
                     "time/position data) and are left out of the comparison. Set their side in the list if you "
                     "know it.")
    if len(sides) < 2:
        notes.append("All measured frames are on one side: there is no flip to compare, only the drift over the "
                     "session.")
    if any(f.metrics is not None and f.bayer_warning for f in frames):
        notes.append("Some frames are Bayer mosaics (BAYERPAT) measured without debayering: enable Debayer on "
                     "Setup > General, or the shapes are distorted by the color pattern.")
    return {"sides": sides, "counts": counts, "rows": rows, "notes": notes, "measured": len(measured),
            "total": len(frames), "stop": None, "static": []}


# ---------------------------------------------------------------------------
# Hints and run

def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{max(1.0, seconds):.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def series_hints_status(frames: list[SeriesFrame], timing: dict | None, s) -> str:
    """HTML: frames per side, what is missing for a comparison and the
    estimated run time (timing {seconds, source, maps} or None)."""
    out = []
    counts: dict[str, int] = {}
    for f in frames:
        counts[f.side] = counts.get(f.side, 0) + 1
    sides = [x for x in (SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B) if counts.get(x, 0) > 0]
    unknown = counts.get(SIDE_UNKNOWN, 0)
    if not frames:
        out.append("<b>No frames yet</b> - add them with Add files... on the right.")
    else:
        parts = [f"{x} {counts[x]}" for x in sides] + ([f"side unknown {unknown}"] if unknown else [])
        out.append(f"<b>{len(frames)} frame{'' if len(frames) == 1 else 's'}</b>: " + ", ".join(parts) + ".")
        if len(sides) < 2:
            out.append("<b>Only one side</b>: add frames from the other side of the flip, else there is nothing to "
                       "compare - only the static optics and the drift over the session.")
        few = [x for x in sides if counts[x] < 3]
        if len(sides) >= 2 and few:
            out.append("<b>Fewer than 3 frames</b> on " + " and ".join(few) + ": the sides are not compared until "
                       "each has at least 3.")
        if unknown:
            out.append(f"<b>{unknown} frame{'' if unknown == 1 else 's'} without a known side</b> - left out of the "
                       "comparison; set the side with West / East.")
    maps = s.series_show_maps or s.series_save_maps or s.series_save_mosaic
    if timing is None:
        out.append("<b>Run time</b>: click Calculate on one frame of the series first - each frame takes about as "
                   "long as its star measurement.")
    else:
        per = f"about {format_duration(timing['seconds'])} per frame"
        frm = "from the last series run" if timing["source"] == "series" else "from the last Calculate"
        extra = ""
        if maps and not (timing["source"] == "series" and timing["maps"]):
            extra = ", plus a few seconds per frame for the maps"
        elif not maps and timing["source"] == "series" and timing["maps"]:
            extra = ", a little less without the maps"
        out.append("<b>Run time</b>: " + (
            f"about {format_duration(timing['seconds'] * len(frames))} for {len(frames)} frames ({per}, {frm}{extra})."
            if frames else f"{per} ({frm}{extra})."))
        out.append("Stop ends the run after the current frame; the frames measured so far are compared, pass 2 is "
                   "skipped.")
    return "".join(f"<p style='margin-top:0px; margin-bottom:4px;'>{t}</p>" for t in out)


def measure_series(frames: list[SeriesFrame], s, log: Log, progress: Progress,
                   should_stop: Callable[[], bool], on_frame=None) -> dict:
    """Measures every frame with the settings. on_frame(frame, analysis,
    optics, index, report) after each measured frame (then the background is
    rendered as well). Returns {stopped, after}."""
    n = len(frames)
    for f in frames:
        f.metrics = f.error = f.measurement = None
        f.bayer_warning = False
        f.map_image = None
        f.output_files = []
    for i, f in enumerate(frames):
        if should_stop():
            log.warning(f"Stopped after frame {i} of {n}: {n - i} frame{'' if n - i == 1 else 's'} not measured.")
            return {"stopped": True, "after": i}
        if f.header_error:
            f.error = f.header_error
            log.warning(f"Series frame {i + 1} of {n}: {f.name}: cannot read the header ({f.header_error}) - skipped.")
            continue
        log.note(f"Series frame {i + 1} of {n}: {f.name}")
        sub = progress.nested(i, n, f"{i + 1}/{n}: ")
        try:
            sub.step("Opening the file", 0, 0)
            img = load_image(f.path)
            f.bayer_warning = f.bayer and not s.debayer and not img.is_color
            analysis = analyze_image(img, s, True, SILENT, sub, with_background=on_frame is not None)
            if analysis is None:
                raise ValueError("No stars could be measured.")
            optics = resolve_optics(img.keywords, s, analysis.debayer)
            sub.step("Evaluating", 0.85, 0.88)
            report = report_analysis(analysis, stars_for(analysis, s.subtract_tracking), s, optics, SILENT)
            f.metrics = report["metrics"]
            f.measurement = {"stars": analysis.stars, "w": analysis.w, "h": analysis.h, "tracking": analysis.tracking,
                             "has_asymmetry": analysis.has_asymmetry, "debayer": analysis.debayer, "optics": optics}
            if on_frame is not None:
                try:
                    on_frame(f, analysis, optics, i, report)
                except Exception as x:  # only the display; the frame stays measured
                    log.warning(f"Series frame {f.name}: cannot show the map: {x}")
        except Cancelled:
            raise
        except Exception as x:
            f.error = str(x)
            log.warning(f"Series frame {f.name}: {f.error}")
    return {"stopped": False}


def build_static_series(frames: list[SeriesFrame], log: Log) -> list[dict]:
    """Pass 2: [{label, analysis}] for all frames and for each side."""
    measured = [f for f in frames if f.measurement]
    if not measured:
        return []
    sizes: dict[str, int] = {}
    for f in measured:
        k = f"{f.measurement['w']}x{f.measurement['h']}"
        sizes[k] = sizes.get(k, 0) + 1
    size = max(sizes, key=lambda k: sizes[k])
    skipped = [f for f in measured if f"{f.measurement['w']}x{f.measurement['h']}" != size]
    if skipped:
        log.warning(f"Series static: {len(skipped)} frame(s) of another image size left out.")
    measured = [f for f in measured if f"{f.measurement['w']}x{f.measurement['h']}" == size]
    w, h = measured[0].measurement["w"], measured[0].measurement["h"]
    groups = [("all", measured)]
    for side in (SIDE_WEST, SIDE_EAST, SIDE_A, SIDE_B):
        fs = [f for f in measured if f.side == side]
        if 0 < len(fs) < len(measured):
            groups.append((side, fs))
    background = np.full((h, w, 3), 16, dtype=np.uint8)
    out = []
    for label, fs in groups:
        t1 = median_of([f.measurement["tracking"]["chi1"] for f in fs])
        t2 = median_of([f.measurement["tracking"]["chi2"] for f in fs])
        centers = []
        for f in fs:
            ev = evaluate_fwhm_grid(f.measurement["stars"], w, h, FWHM_GRID_SIZE)
            centers.append(ev["center"]["value"] if ev["center"] else None)
        ref = median_of([c for c in centers if c is not None])
        pooled: list[Star] = []
        dynamic = []
        for f, c in zip(fs, centers):
            m = f.measurement
            stars = [s.copy() for s in m["stars"]]
            d1, d2 = m["tracking"]["chi1"] - t1, m["tracking"]["chi2"] - t2
            apply_tracking_correction(stars, {"chi1": d1, "chi2": d2})
            k = ref / c if c and c > 0 and ref > 0 else 1.0
            for s in stars:
                s.fwhm_x *= k
                s.fwhm_y *= k
            pooled += stars
            dynamic.append({"name": f.name, "side": f.side, "d1": d1, "d2": d2, "chi": math.hypot(d1, d2),
                            "angle_deg": (math.degrees(0.5 * math.atan2(d2, d1)) % 180 + 180) % 180, "fwhm_scale": k})
        has_asym = all(f.measurement["has_asymmetry"] for f in fs)
        rms = math.sqrt(sum(x["chi"] ** 2 for x in dynamic) / len(dynamic))
        a = Analysis(
            key="series:" + label,
            target_name=f"Series static, {label} ({len(fs)} frame{'' if len(fs) == 1 else 's'})",
            debayer=fs[0].measurement["debayer"], w=w, h=h, stars=pooled,
            tracking=compute_tracking_component(pooled, w, h), has_asymmetry=has_asym,
            coma_fit=fit_coma_field(pooled, w, h, False) if has_asym else None,
            coma_fit_centroid=fit_coma_field(pooled, w, h, True) if has_asym else None,
            background=background, header={"name": f"Series static {label}", "path": "", "keywords": []})
        a.optics = fs[0].measurement["optics"]
        a.series = {"label": label, "frames": len(fs), "static_tracking": {"chi1": t1, "chi2": t2},
                    "dynamic": dynamic, "dynamic_rms": rms, "fwhm_center": ref}
        out.append({"label": label, "analysis": a})
    return out


def format_static_series(entry: dict) -> list[str]:
    a = entry["analysis"]
    s = a.series

    def deg(c1, c2):
        return int(math.floor((math.degrees(0.5 * math.atan2(c2, c1)) % 180 + 180) % 180 + 0.5)) % 180
    st = s["static_tracking"]
    lines = [f"Series static, {entry['label']}: {s['frames']} frame(s), {len(a.stars)} stars pooled, FWHM scaled to "
             f"the center {s['fwhm_center']:.2f} px.",
             f"   Static tracking (median over the frames): {math.hypot(st['chi1'], st['chi2']):.3f} along "
             f"{deg(st['chi1'], st['chi2'])}° - constant tracking or astigmatism on the axis (a single camera angle "
             f"cannot tell them apart); pooled model: {a.tracking['ellipticity']:.3f}"
             + (f" ± {a.tracking['se']:.3f}" if a.tracking.get("se") is not None else "") + ".",
             f"   Dynamic tracking (frame to frame): rms {s['dynamic_rms']:.3f}, largest "
             f"{max(x['chi'] for x in s['dynamic']):.3f}."]
    gd = a.optics.guiding if getattr(a, "optics", None) else None
    if gd and gd["type"] != GUIDING_UNKNOWN:
        lines.append("   " + guiding_causes(gd["type"]))
    return lines


VERDICTS = {"changes": "<b>changes at the flip</b>", "stable": "stable",
            "unclear": "unclear (difference within the scatter)", "few": "too few frames on one side for a verdict"}


def format_series_report(result: dict) -> str:
    """The comparison as HTML (Series report)."""
    e = html.escape
    sides = result["sides"]
    lines = [f"<b>{result['measured']} of {result['total']} frames measured</b> - " +
             (", ".join(f"{x}: {result['counts'][x]}" for x in sides) if sides else "no side known")]
    stop = result.get("stop")
    if stop:
        rest = result["total"] - stop["after"]
        lines.append(f"<b>Stopped after frame {stop['after']} of {result['total']}</b>: {rest} frame"
                     f"{'' if rest == 1 else 's'} not measured; pass 2 (Series static) was skipped. Run the series "
                     "again to compare all frames.")
    changes = [r for r in result["rows"] if r["verdict"] == "changes"]
    if len(sides) == 2:
        lines.append("<b>Nothing changes clearly at the flip</b> - the findings of a single frame hold for both sides."
                     if not changes else
                     "<b>Changes at the flip:</b> " + ", ".join(e(r["metric"].label) for r in changes) + ".")
    for row in result["rows"]:
        m = row["metric"]
        parts = []
        for k, side in enumerate(sides):
            st = row["stats"][k]
            if not st:
                continue
            sd = ""
            if math.isfinite(st["sd"]):
                sd = " (± " + (m.spread(st["sd"]) if m.spread else f"{100 * st['sd']:.0f}%" if m.vector
                               else m.show(st["sd"]).lstrip("+")) + ")"
            parts.append(f"{side} {m.show(st['median'])}{sd}")
        if not parts:
            continue
        lines.append("")
        text = f"<b>{e(m.label)}:</b> " + e("; ".join(parts))
        if m.vector and row["diff"] is not None:
            text += "; " + e(m.show_diff(row["stats"][0]["median"], row["stats"][1]["median"]) if m.show_diff
                             else f"difference {100 * row['diff']:.0f}%")
        if row["verdict"]:
            text += " → " + VERDICTS[row["verdict"]]
        lines.append(text)
        if row["verdict"] == "changes":
            lines.append("&nbsp;&nbsp;&nbsp;→ " + e(m.changed))
        elif row["verdict"] == "stable" and m.stable:
            sizes = [_norm(st["median"]) if m.vector else abs(st["median"]) for st in row["stats"]]
            if min(sizes) >= m.stable_min:
                lines.append("&nbsp;&nbsp;&nbsp;→ " + e(m.stable))
        if row["drift"]:
            d = row["drift"]
            lines.append(f"&nbsp;&nbsp;&nbsp;Drifts over the session: {e(m.show(d['per_hour']))} per hour over "
                         f"{d['hours']:.1f} h (r = {d['corr']:.2f}) - temperature, altitude or slow slip rather than "
                         "the flip.")
        if row["drift"] and row["verdict"] == "changes":
            lines.append("&nbsp;&nbsp;&nbsp;Caution: with that drift, part of the difference between the sides may "
                         "be the drift - compare frames taken shortly before and after the flip.")
    if result["notes"]:
        lines.append("")
        lines += [f"<i>Note:</i> {e(n)}" for n in result["notes"]]
    for entry in result.get("static", []):
        lines.append("")
        lines += [e(x).replace("   ", "&nbsp;&nbsp;&nbsp;") for x in format_static_series(entry)]
    return "<br>".join(lines)


def export_series_csv(frames: list[SeriesFrame], path: str) -> None:
    def num(v, d):
        return "" if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) else f"{v:.{d}f}"

    def quote(t):
        return '"' + t.replace('"', '""') + '"'
    lines = ["File,Side,SideSource,TimeUTC,HourAngle_h,Altitude_deg,FocusPos,Temp_C,Stars,FWHMCenter_px,FWHMEdge_px,"
             "TiltRel,TiltDir_deg,GridCornerRatio,GridTiltRel,GridTiltSigma,GridTiltSoftCorner,EpsRad,"
             "TrackingEllipticity,TrackingAngle_deg,ComaK,ComaX0_px,ComaY0_px,SIPMaxShift_px,SIPTiltEquiv_deg,"
             + ",".join(SIP_TERMS) + ",Error"]
    for fr in frames:
        m = fr.metrics or {}
        s = m.get("surface")
        tilt = _tilt_vector(m) if m else None
        g, rg, tr, c = m.get("grid"), m.get("rings"), m.get("tracking"), m.get("coma")
        t = (_dt.datetime.fromtimestamp(fr.time_ms / 1000, _dt.timezone.utc).isoformat(timespec="milliseconds")
             .replace("+00:00", "Z") if fr.time_ms is not None else "")
        sip_max = sip_max_shift(sip_corner_shifts(fr)) if fr.sip else None
        lines.append(",".join([
            quote(fr.name), fr.side, fr.side_source, t, num(fr.ha_h, 3), num(fr.alt_deg, 2), num(fr.focus_pos, 0),
            num(fr.temp_c, 2), str(m["n"]) if m else "",
            num(s["f_center"] if s else None, 3), num(s["f_edge"] if s else None, 3),
            num(math.hypot(*tilt) if tilt else None, 4), num(s["tilt_dir_deg"] if s else None, 1),
            num(g["corner_ratio"] if g else None, 3), num(g["tilt_rel"] if g else None, 4),
            num(g["tilt_sigma"] if g else None, 2), (g["tilt_soft_corner"] or "") if g else "",
            num(rg["e_rad"] if rg else None, 4), num(tr["ellipticity"] if tr else None, 4),
            num(tr["angle_deg"] if tr else None, 1), num(c["k"] if c else None, 4), num(c["x0"] if c else None, 1),
            num(c["y0"] if c else None, 1), num(sip_max, 2), num(sip_tilt_equivalent_deg(fr), 3),
            ",".join(f"{fr.sip['terms'][k]:.6e}" if fr.sip else "" for k in SIP_TERMS),
            quote(fr.error) if fr.error else ""]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def frame_output_path(frame: SeriesFrame, suffix: str) -> str:
    p = Path(frame.path)
    return str(p.with_name(p.stem + suffix))
