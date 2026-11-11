"""The FWHM grid (Star size map) and Siril's quadrant evaluation.

Each cell of an 11 x 11 grid is judged by its ratio to the image center,
R = FWHM_cell / FWHM_center - a ratio, so that seeing, focal length and
pixel size drop out:
    R <= 1.10 good, <= 1.25 slight, <= 1.50 clear, > 1.50 strong
but only where the difference is significant,
    z = (FWHM_cell - FWHM_center) / sqrt(se_cell^2 + se_center^2) >= 2;
a cell that cannot be told from the center (and is not sharp anyway) is
"not significant", an uncertain cell (fewer than FWHM_GRID_MIN_STARS stars
or a standard error above FWHM_GRID_MAX_REL_SE of its value) is not judged
at all. The thresholds are rules of thumb, not a standard. The center is
the inner 3 x 3 cells pooled. For the whole image:
 - corners: ratio of each corner (its 2 x 2 cell block pooled) to the
   center, and their median - the rotationally symmetric part, i.e. field
   curvature / spacing;
 - tilt: the diagonal (TL-BR or TR-BL) whose corners differ most,
   |a - b| / ((a + b) / 2), with its significance |a - b| / sqrt(se_a^2 + se_b^2)
   - the one-sided part, i.e. sensor tilt;
 - edge spread: (max - min) / median of the judged cells of the outer ring.
"""

from __future__ import annotations

import math

from .star import Star
from .stats import median_of, robust_spread, trimmed_mean25

FWHM_GRID_SIZE = 11
FWHM_GRID_MIN_STARS = 5
FWHM_GRID_MAX_REL_SE = 0.10
FWHM_GRID_MIN_Z = 2
FWHM_RATIO_LIMITS = [1.10, 1.25, 1.50]
FWHM_RATIO_NAMES = ["good", "slight", "clear", "strong"]
FWHM_TILT_LIMITS = [0.05, 0.10]
FWHM_TILT_NAMES = ["unremarkable", "slight", "worth correcting"]
FWHM_CORNER_NAMES = ["top left", "top right", "bottom left", "bottom right"]


# ---------------------------------------------------------------------------
# Siril's "Show tilt" (src/algos/ccd-inspector.c, compute_tilt_values())

def compute_siril_tilt(stars: list[Star], w: int, h: int) -> dict | None:
    """The trimmed mean FWHM per quadrant (m1 TL, m2 TR, m3 BL, m4 BR), of an
    inner circle (25% of the half diagonal, m_inner) and of an outer ring
    (> 75%, m_outer). None when one of them is empty (as Siril)."""
    cx, cy = w / 2, h / 2
    quadrants: list[list[float]] = [[], [], [], []]
    inner, outer = [], []
    r = math.sqrt(cx * cx + cy * cy)
    r1, r2 = 0.25 * r, 0.75 * r
    for s in stars:
        f = (s.fwhm_x + s.fwhm_y) * 0.5
        left, top = s.x < cx, s.y < cy
        quadrants[(0 if left else 1) if top else (2 if left else 3)].append(f)
        d2 = (s.x - cx) ** 2 + (s.y - cy) ** 2
        if d2 < r1 * r1:
            inner.append(f)
        elif d2 > r2 * r2:
            outer.append(f)
    if any(len(q) == 0 for q in quadrants) or not inner or not outer:
        return None
    return {
        "m1": trimmed_mean25(quadrants[0]), "m2": trimmed_mean25(quadrants[1]),
        "m3": trimmed_mean25(quadrants[2]), "m4": trimmed_mean25(quadrants[3]),
        "m_inner": trimmed_mean25(inner), "m_outer": trimmed_mean25(outer),
    }


# ---------------------------------------------------------------------------
# The grid

def fwhm_grid_buckets(stars: list[Star], w: int, h: int, grid_size: int) -> list[list[list[float]]]:
    """The mean FWHM of the stars per cell: grid_size x grid_size lists
    (row 0 = top, column 0 = left)."""
    cell_w, cell_h = w / grid_size, h / grid_size
    buckets = [[[] for _ in range(grid_size)] for _ in range(grid_size)]
    for s in stars:
        col = min(grid_size - 1, max(0, int(math.floor(s.x / cell_w))))
        row = min(grid_size - 1, max(0, int(math.floor(s.y / cell_h))))
        buckets[row][col].append((s.fwhm_x + s.fwhm_y) * 0.5)
    return buckets


def fwhm_cell_stats(values: list[float]) -> dict:
    """{value (25%-trimmed mean), n, se (1.4826 MAD / sqrt(n); None below 2)}."""
    n = len(values)
    if n == 0:
        return {"value": None, "n": 0, "se": None}
    return {"value": trimmed_mean25(values), "n": n, "se": None if n < 2 else robust_spread(values) / math.sqrt(n)}


def fwhm_ratio_class(ratio: float) -> int:
    """0 good ... 3 strong."""
    k = 0
    while k < len(FWHM_RATIO_LIMITS) and ratio > FWHM_RATIO_LIMITS[k]:
        k += 1
    return k


def evaluate_fwhm_grid(stars: list[Star], w: int, h: int, grid_size: int = FWHM_GRID_SIZE) -> dict:
    """Returns {grid_size, center, cells, corners, tilt, edge_spread,
    class_counts}: center {value, n, se} or None; cells grid_size x grid_size
    dicts {value, n, se, uncertain, ratio, z, cls} with cls -1 = not judged
    or 0..3; corners None or {stats[4], ratios[4] (TL, TR, BL, BR), ratio
    (median), cls}; tilt None or {rel, z, significant, cls, soft, sharp};
    edge_spread None or a fraction; class_counts {good, slight, clear,
    strong, not_significant, uncertain}."""
    buckets = fwhm_grid_buckets(stars, w, h, grid_size)

    def pool(r0, r1, c0, c1):
        out = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                out.extend(buckets[r][c])
        return fwhm_cell_stats(out)

    mid = grid_size >> 1
    center = pool(mid - 1, mid + 1, mid - 1, mid + 1)
    if center["value"] is None or center["se"] is None:
        center = None

    counts = {"good": 0, "slight": 0, "clear": 0, "strong": 0, "not_significant": 0, "uncertain": 0}
    cells = []
    for row in buckets:
        out_row = []
        for b in row:
            e = fwhm_cell_stats(b)
            e["uncertain"] = (e["n"] < FWHM_GRID_MIN_STARS or e["se"] is None
                              or e["se"] > FWHM_GRID_MAX_REL_SE * e["value"])
            e["ratio"] = None
            e["z"] = None
            e["cls"] = -1
            if e["value"] is not None:
                if center is not None:
                    e["ratio"] = e["value"] / center["value"]
                if e["uncertain"]:
                    counts["uncertain"] += 1
                elif center is not None:
                    e["z"] = (e["value"] - center["value"]) / math.sqrt(e["se"] ** 2 + center["se"] ** 2)
                    cls = fwhm_ratio_class(e["ratio"])
                    if cls == 0 or e["z"] >= FWHM_GRID_MIN_Z:
                        e["cls"] = cls
                        counts[FWHM_RATIO_NAMES[cls]] += 1
                    else:
                        counts["not_significant"] += 1
            out_row.append(e)
        cells.append(out_row)

    corners = tilt = None
    k = max(1, round(grid_size / 5))
    last = grid_size - 1
    cs = [pool(0, k - 1, 0, k - 1), pool(0, k - 1, last - k + 1, last),
          pool(last - k + 1, last, 0, k - 1), pool(last - k + 1, last, last - k + 1, last)]
    if all(s["value"] is not None and s["se"] is not None for s in cs):
        if center is not None:
            ratios = [s["value"] / center["value"] for s in cs]
            ratio = median_of(ratios)
            corners = {"stats": cs, "ratios": ratios, "ratio": ratio, "cls": fwhm_ratio_class(ratio)}
        for d0, d1 in ((0, 3), (1, 2)):
            a, b = cs[d0], cs[d1]
            rel = abs(a["value"] - b["value"]) / (0.5 * (a["value"] + b["value"]))
            if tilt is None or rel > tilt["rel"]:
                z = abs(a["value"] - b["value"]) / math.sqrt(a["se"] ** 2 + b["se"] ** 2)
                soft = d0 if a["value"] >= b["value"] else d1
                cls = 0 if rel < FWHM_TILT_LIMITS[0] else 1 if rel < FWHM_TILT_LIMITS[1] else 2
                tilt = {"rel": rel, "z": z, "significant": z >= FWHM_GRID_MIN_Z, "cls": cls,
                        "soft": soft, "sharp": d1 if soft == d0 else d0}

    edge = [cells[r][c]["value"] for r in range(grid_size) for c in range(grid_size)
            if (r in (0, last) or c in (0, last)) and cells[r][c]["value"] is not None
            and not cells[r][c]["uncertain"]]
    edge_spread = (max(edge) - min(edge)) / median_of(edge) if len(edge) >= 4 else None

    return {"grid_size": grid_size, "center": center, "cells": cells, "corners": corners, "tilt": tilt,
            "edge_spread": edge_spread, "class_counts": counts}


def format_fwhm_grid_evaluation(ev: dict, scale: float = 1.0, unit: str = "px") -> list[str]:
    """One line each: the center, corners, tilt and edge of
    evaluate_fwhm_grid()."""
    def fw(v):
        return f"{v * scale:.2f}" + (" px" if unit == "px" else unit)

    if ev["center"] is None:
        return ["Center: no stars - not judged."]
    c = ev["center"]
    lines = [f"Center (inner 3x3 cells): x1.00 ({fw(c['value'])} ± {fw(c['se'])}, n={c['n']})"]
    if ev["corners"] is not None:
        co = ev["corners"]
        lines.append(f"Corners: x{co['ratio']:.2f} the center (median) -> {FWHM_RATIO_NAMES[co['cls']]}.")
        for i, r in enumerate(co["ratios"]):
            st = co["stats"][i]
            name = FWHM_CORNER_NAMES[i]
            lines.append(f"   {name[0].upper() + name[1:]}: x{r:.2f} ({fw(st['value'])} ± {fw(st['se'])}, n={st['n']})")
    else:
        lines.append("Corners: too few stars - not judged.")
    t = ev["tilt"]
    if t is not None:
        lines.append(f"Tilt: {FWHM_CORNER_NAMES[t['soft']]} {100 * t['rel']:.0f}% softer than "
                     f"{FWHM_CORNER_NAMES[t['sharp']]} ({t['z']:.1f}σ) -> "
                     f"{FWHM_TILT_NAMES[t['cls']] if t['significant'] else 'not significant'}.")
    if ev["edge_spread"] is not None:
        lines.append(f"Edge spread: {100 * ev['edge_spread']:.0f}% between the softest and the sharpest edge cell.")
    n = ev["class_counts"]
    lines.append(f"Cells: {n['good']} good, {n['slight']} slight, {n['clear']} clear, {n['strong']} strong, "
                 f"{n['not_significant']} not significant, {n['uncertain']} uncertain "
                 f"(R = FWHM/center: <= {FWHM_RATIO_LIMITS[0]:.2f} / {FWHM_RATIO_LIMITS[1]:.2f} / "
                 f"{FWHM_RATIO_LIMITS[2]:.2f} / above; significant from {FWHM_GRID_MIN_Z:.0f}σ).")
    return lines
