"""Statistics of the star shapes (Star shape map), with uncertainties.

All work in the distortion components (c1, c2) = chi (cos 2 psi, sin 2 psi)
(psi = image angle of the major axis): they add approximately linearly and
turn the 180-degree ambiguity of an axis into a plain vector, so means,
scatter and standard errors behave as for any 2D quantity. For small values
chi is about the ellipticity 1 - b/a.

Shape measures:
    eccentricity  e   = sqrt(1 - b^2/a^2)        (what the fit and the map use)
    ellipticity   eps = 1 - b/a                  (used in the ring table)
    distortion    chi = (a^2 - b^2)/(a^2 + b^2)  (adds approximately linearly)
"""

from __future__ import annotations

import math

import numpy as np

from .fwhm_grid import FWHM_CORNER_NAMES, FWHM_GRID_MIN_STARS, FWHM_GRID_SIZE
from .star import Star
from .stats import scalar_mean, solve_least_squares_clipped, solve_linear_system

# The magnitude of a noisy 2D mean is never zero (about 1.25 se on average,
# Rayleigh distributed): significant from 2.45 se, which pure noise exceeds
# in 5% of the cases; a single signed value from 2 se.
SHAPE_MIN_Z = 2.45
SHAPE_MIN_Z_1D = 2.0


def distortion_from_ecc(e: float) -> float:
    return e * e / (2 - e * e)


def ecc_from_distortion(chi: float) -> float:
    chi = max(0.0, min(0.999999, chi))
    return math.sqrt(2 * chi / (1 + chi))


def ellipticity_from_ecc(e: float) -> float:
    return 1 - math.sqrt(max(0.0, 1 - e * e))


def star_image_angle(s: Star) -> float:
    """Image-coordinate angle (radians) of the PSF major axis."""
    return -s.rotation * math.pi / 180


def shape_components(s: Star) -> tuple[float, float]:
    chi = distortion_from_ecc(s.eccentricity)
    psi2 = 2 * star_image_angle(s)
    return chi * math.cos(psi2), chi * math.sin(psi2)


def shape_mean(c1: list[float], c2: list[float]) -> dict | None:
    """Mean, magnitude, axis angle and standard error of a set of components:
    {m1, m2, chi, n, se, z, psi}. The standard error is that of each
    component (pooled)."""
    n = len(c1)
    if n == 0:
        return None
    m1 = sum(c1) / n
    m2 = sum(c2) / n
    v = sum((a - m1) ** 2 + (b - m2) ** 2 for a, b in zip(c1, c2))
    se = math.sqrt(v / (2 * (n - 1)) / n) if n > 1 else None
    chi = math.hypot(m1, m2)
    return {"m1": m1, "m2": m2, "chi": chi, "n": n, "se": se,
            "z": chi / se if se is not None and se > 0 else None, "psi": 0.5 * math.atan2(m2, m1)}


# ---------------------------------------------------------------------------
# The smoothed field on a grid

def shape_field_grid(stars: list[Star], w: float, h: float, radius: float, step: float) -> dict:
    """The field smoothed as for the streamlines (Gaussian kernel, sigma =
    radius/2, cut off at the radius) on a grid of spacing step, with the
    standard error of each value (local scatter / effective number of
    stars). Returns {step, nx, ny, cells} with cells[j * nx + i] =
    {m1, m2, chi, se, z} or None."""
    xs = np.array([s.x for s in stars], dtype=float)
    ys = np.array([s.y for s in stars], dtype=float)
    comps = np.array([shape_components(s) for s in stars], dtype=float).reshape(-1, 2)
    c1, c2 = comps[:, 0], comps[:, 1]
    r2 = radius * radius
    sigma2 = 2 * (radius * 0.5) ** 2
    bw = max(1, math.ceil(w / radius))
    bh = max(1, math.ceil(h / radius))
    bi = np.clip(np.floor(xs / radius).astype(int), 0, bw - 1) if len(xs) else np.empty(0, int)
    bj = np.clip(np.floor(ys / radius).astype(int), 0, bh - 1) if len(ys) else np.empty(0, int)
    buckets: list[list[int]] = [[] for _ in range(bw * bh)]
    for k in range(len(xs)):
        buckets[bj[k] * bw + bi[k]].append(k)
    nx = math.ceil(w / step) + 1
    ny = math.ceil(h / step) + 1
    cells: list[dict | None] = [None] * (nx * ny)
    for j in range(ny):
        for i in range(nx):
            x, y = i * step, j * step
            bi0, bj0 = int(math.floor(x / radius)), int(math.floor(y / radius))
            near: list[int] = []
            for b2 in range(max(0, bj0 - 1), min(bh - 1, bj0 + 1) + 1):
                for b1 in range(max(0, bi0 - 1), min(bw - 1, bi0 + 1) + 1):
                    near.extend(buckets[b2 * bw + b1])
            if not near:
                continue
            idx = np.array(near)
            d2 = (xs[idx] - x) ** 2 + (ys[idx] - y) ** 2
            sel = d2 <= r2
            if not sel.any():
                continue
            wk = np.exp(-d2[sel] / sigma2)
            a1, a2 = c1[idx][sel], c2[idx][sel]
            sw = float(wk.sum())
            if sw < 1e-6:
                continue
            sw2 = float((wk * wk).sum())
            m1 = float((wk * a1).sum()) / sw
            m2 = float((wk * a2).sum()) / sw
            variance = max(0.0, 0.5 * (float((wk * a1 * a1).sum()) / sw - m1 * m1
                                       + float((wk * a2 * a2).sum()) / sw - m2 * m2))
            n_eff = sw * sw / sw2
            se = math.sqrt(variance / n_eff) if n_eff > 1 else None
            chi = math.hypot(m1, m2)
            cells[j * nx + i] = {"m1": m1, "m2": m2, "chi": chi, "se": se,
                                 "z": chi / se if se is not None and se > 0 else 0.0}
    return {"step": step, "nx": nx, "ny": ny, "cells": cells}


def shape_field_at(grid: dict, x: float, y: float) -> dict | None:
    i, j = _js_round(x / grid["step"]), _js_round(y / grid["step"])
    if i < 0 or j < 0 or i >= grid["nx"] or j >= grid["ny"]:
        return None
    return grid["cells"][j * grid["nx"] + i]


def shape_significant_at(grid: dict, x: float, y: float) -> bool:
    c = shape_field_at(grid, x, y)
    return c is not None and c["z"] >= SHAPE_MIN_Z


def shape_defects(grid: dict) -> list[dict]:
    """Singular points of the smoothed axis field: around each grid cell the
    winding of the doubled angle in units of 2 pi, halved - +-1/2 or +-1.
    Optics produce at most one +1 or two +1/2; more, and any -1/2, come from
    noise. Returns [{x, y, index}]."""
    def wrap(a):
        while a > math.pi:
            a -= 2 * math.pi
        while a <= -math.pi:
            a += 2 * math.pi
        return a

    nx, ny, cells = grid["nx"], grid["ny"], grid["cells"]
    defects = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            loop = [cells[j * nx + i], cells[j * nx + i + 1], cells[(j + 1) * nx + i + 1], cells[(j + 1) * nx + i]]
            if any(c is None for c in loop):
                continue
            a = [math.atan2(c["m2"], c["m1"]) for c in loop]
            turn = sum(wrap(a[(k + 1) % 4] - a[k]) for k in range(4))
            n = _js_round(turn / (2 * math.pi))
            if n != 0:
                defects.append({"x": (i + 0.5) * grid["step"], "y": (j + 0.5) * grid["step"], "index": n / 2})
    return defects


# ---------------------------------------------------------------------------
# The physical model of the shape field

class ShapeModel:
    """What optics can produce, as low-order terms in u = (x - cx)/r_max,
    v = (y - cy)/r_max:
        constant  (a0, b0)               tracking residue, astigmatism on axis
        linear    c1 = a1 u + a2 v, c2 = b1 u + b2 v
                                         a decentered radial pattern, binodal
                                         astigmatism of a misaligned system
        radial    k (u^2 - v^2, 2 u v)   field curvature / corrector spacing:
                                         k > 0 radial, k < 0 tangential, k = the
                                         value at the corner (r = r_max)
    fitted to all stars (least squares, 3-sigma clipping)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model(self, u: float, v: float) -> tuple[float, float]:
        q = self.coeffs
        return (q[0] + q[2] * u + q[3] * v + q[6] * (u * u - v * v),
                q[1] + q[4] * u + q[5] * v + q[6] * 2 * u * v)


def fit_shape_model(stars: list[Star], w: float, h: float, grid_size: int) -> ShapeModel | None:
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    if len(stars) < 30:
        return None
    comps = [shape_components(s) for s in stars]
    uv = [((s.x - cx) / r_max, (s.y - cy) / r_max) for s in stars]
    rows, zs = [], []
    for (u, v), c in zip(uv, comps):
        rows.append([1, 0, u, v, 0, 0, u * u - v * v])
        zs.append(c[0])
        rows.append([0, 1, 0, 0, u, v, 2 * u * v])
        zs.append(c[1])
    fit = solve_least_squares_clipped(rows, zs, 3)
    if fit is None or fit["coeffs"] is None:
        return None
    q, mask = fit["coeffs"], fit["mask"]

    def model(u, v):
        return (q[0] + q[2] * u + q[3] * v + q[6] * (u * u - v * v),
                q[1] + q[4] * u + q[5] * v + q[6] * 2 * u * v)

    n = 7
    a = np.asarray(rows, dtype=float)
    z = np.asarray(zs, dtype=float)
    keep = np.ones(len(rows), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    ak, zk = a[keep], z[keep]
    res = zk - ak @ np.asarray(q)
    m = (ak.T @ ak).tolist()
    count = int(keep.sum())
    sigma2 = float((res * res).sum()) / (count - n) if count > n else 0.0
    se, cov = [], []
    for r in range(n):
        e = [0.0] * n
        e[r] = 1.0
        col = solve_linear_system(m, e)
        se.append(math.sqrt(max(0.0, col[r] * sigma2)) if col else None)
        cov.append([v * sigma2 for v in col] if col else None)

    cell_w, cell_h = w / grid_size, h / grid_size
    cells = [{"r1": [], "r2": [], "d1": [], "d2": []} for _ in range(grid_size * grid_size)]
    for s, (u, v), c in zip(stars, uv, comps):
        col_i = min(grid_size - 1, max(0, int(math.floor(s.x / cell_w))))
        row_i = min(grid_size - 1, max(0, int(math.floor(s.y / cell_h))))
        mm = model(u, v)
        cell = cells[row_i * grid_size + col_i]
        cell["r1"].append(c[0] - mm[0])
        cell["r2"].append(c[1] - mm[1])
        cell["d1"].append(c[0])
        cell["d2"].append(c[1])
    chi2 = ss_res = ss_tot = 0.0
    used = 0
    bad, judged = [], []
    for k, c in enumerate(cells):
        if len(c["r1"]) < FWHM_GRID_MIN_STARS:
            continue
        rs, dat = shape_mean(c["r1"], c["r2"]), shape_mean(c["d1"], c["d2"])
        if not (rs["se"] is not None and rs["se"] > 0):
            continue
        chi2 += (rs["m1"] ** 2 + rs["m2"] ** 2) / rs["se"] ** 2
        ss_res += rs["m1"] ** 2 + rs["m2"] ** 2
        ss_tot += dat["m1"] ** 2 + dat["m2"] ** 2
        used += 1
        judged.append({"row": k // grid_size, "col": k % grid_size, "z": rs["z"]})
        if rs["z"] >= 3:
            bad.append({"row": k // grid_size, "col": k % grid_size, "z": rs["z"]})
    dof = 2 * used - n

    k_, k_se = q[6], se[6]
    axis = None
    uni1, uni2 = q[0], q[1]
    if k_se is not None and k_se > 0 and abs(k_) >= 3 * k_se:
        u0 = -(q[2] + q[5]) / (4 * k_)
        v0 = (q[3] - q[4]) / (4 * k_)
        axis = {"x": cx + u0 * r_max, "y": cy + v0 * r_max, "offset": math.hypot(u0, v0)}
        uni1 -= k_ * (u0 * u0 - v0 * v0)
        uni2 -= k_ * 2 * u0 * v0
    binodal = math.hypot(0.5 * (q[2] - q[5]), 0.5 * (q[3] + q[4]))
    binodal_se = 0.5 * math.hypot(se[2], se[3]) if (se[2] or 0) > 0 and (se[3] or 0) > 0 else None
    return ShapeModel(
        coeffs=q, se=se, cov=cov if all(c is not None for c in cov) else None,
        cx=cx, cy=cy, r_max=r_max,
        constant=math.hypot(uni1, uni2), uniform1=uni1, uniform2=uni2,
        constant_se=math.hypot(se[0], se[1]) / math.sqrt(2) if (se[0] or 0) > 0 else None,
        k=k_, k_se=k_se, axis=axis, binodal=binodal, binodal_se=binodal_se,
        chi2dof=chi2 / dof if dof > 0 else None, cells_used=used,
        explained=max(0.0, 1 - ss_res / ss_tot) if ss_tot > 0 else None, unexplained=bad,
        judged_cells=judged)


def shape_model_significant_at(fit: ShapeModel, x: float, y: float) -> bool:
    """Whether the model's field at (x, y) is at least SHAPE_MIN_Z times its
    standard error there (from the parameter covariance)."""
    if not fit.cov:
        return True
    u, v = (x - fit.cx) / fit.r_max, (y - fit.cy) / fit.r_max
    variance = 0.0
    for b in ([1, 0, u, v, 0, 0, u * u - v * v], [0, 1, 0, 0, u, v, 2 * u * v]):
        for i in range(7):
            for j in range(7):
                variance += b[i] * fit.cov[i][j] * b[j]
    se = math.sqrt(max(0.0, variance / 2))
    m = fit.model(u, v)
    return math.hypot(*m) >= SHAPE_MIN_Z * se if se > 0 else True


def shape_model_field_at(fit: ShapeModel, x: float, y: float):
    """The model's axis angle at (x, y), or None where it is too weak."""
    m = fit.model((x - fit.cx) / fit.r_max, (y - fit.cy) / fit.r_max)
    floor = max(0.005, 2 * (fit.constant_se or 0))
    if math.hypot(*m) < floor:
        return None
    return 0.5 * math.atan2(m[1], m[0])


# ---------------------------------------------------------------------------
# The evaluation of the Star shape map

def evaluate_shape_field(stars: list[Star], w: float, h: float, radius: float) -> dict:
    """cells (11 x 11 shape_mean() each, plus uncertain), center (inner 3 x 3),
    corners (per corner the mean radial component eps_rad = chi cos 2(psi - phi)
    with its se: > 0 radial, < 0 tangential), edge (the same for r > 2/3
    r_max), grid (shape_field_grid() at the streamline radius), defects,
    model (fit_shape_model()) and counts."""
    gs = FWHM_GRID_SIZE
    cell_w, cell_h = w / gs, h / gs
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    buckets = [{"c1": [], "c2": [], "rad": []} for _ in range(gs * gs)]
    edge = []
    for s in stars:
        col = min(gs - 1, max(0, int(math.floor(s.x / cell_w))))
        row = min(gs - 1, max(0, int(math.floor(s.y / cell_h))))
        c = shape_components(s)
        phi2 = 2 * math.atan2(s.y - cy, s.x - cx)
        rad = c[0] * math.cos(phi2) + c[1] * math.sin(phi2)
        b = buckets[row * gs + col]
        b["c1"].append(c[0])
        b["c2"].append(c[1])
        b["rad"].append(rad)
        if math.hypot(s.x - cx, s.y - cy) > r_max * 2 / 3:
            edge.append(rad)
    cells = []
    for b in buckets:
        m = shape_mean(b["c1"], b["c2"])
        if m:
            m["uncertain"] = m["n"] < FWHM_GRID_MIN_STARS or m["se"] is None
        cells.append(m)

    def pool(r0, r1, c0, c1):
        a1, a2, rad = [], [], []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                b = buckets[r * gs + c]
                a1 += b["c1"]
                a2 += b["c2"]
                rad += b["rad"]
        return shape_mean(a1, a2), scalar_mean(rad)

    mid, k, last = gs >> 1, max(1, round(gs / 5)), gs - 1
    center = pool(mid - 1, mid + 1, mid - 1, mid + 1)[0]
    corners = [pool(0, k - 1, 0, k - 1)[1], pool(0, k - 1, last - k + 1, last)[1],
               pool(last - k + 1, last, 0, k - 1)[1], pool(last - k + 1, last, last - k + 1, last)[1]]
    grid = shape_field_grid(stars, w, h, radius, max(8.0, radius * 0.25))
    counts = {"elongated": 0, "round": 0, "uncertain": 0}
    for c in cells:
        if not c:
            continue
        if c["uncertain"]:
            counts["uncertain"] += 1
        elif c["z"] is not None and c["z"] >= SHAPE_MIN_Z:
            counts["elongated"] += 1
        else:
            counts["round"] += 1
    return {"grid_size": gs, "cells": cells, "center": center, "corners": corners, "edge": scalar_mean(edge),
            "grid": grid, "defects": shape_defects(grid), "model": fit_shape_model(stars, w, h, gs),
            "counts": counts}


def format_shape_evaluation(ev: dict, tracking: dict | None, tracking_subtracted: bool) -> list[str]:
    """The evaluation of the Star shape map as text lines ("Topic: text";
    lines starting with spaces belong to the line above)."""
    lines = []

    def num(v, d):
        return f"{v:.{d}f}"

    def pm(v, se):
        return num(v, 3) + (f" ± {num(se, 3)}" if se is not None else "")

    def sig(v, se):
        return f" ({num(abs(v) / se, 1)}σ)" if se is not None and se > 0 else ""

    def deg(rad):
        return f"{_js_round((math.degrees(rad) % 180 + 180) % 180) % 180}°"

    if tracking:
        lines.append(f"Tracking: ellipticity {pm(tracking['ellipticity'], tracking['se'])} along "
                     f"{num(tracking['angle_deg'], 0)}°, "
                     f"{'subtracted from all stars' if tracking_subtracted else 'not subtracted'}.")
        if tracking.get("method") == "model":
            md = tracking["median"]
            lines.append(f"   From the optics model; the plain median would be {num(md['ellipticity'], 3)} "
                         f"along {num(md['angle_deg'], 0)}°")
    c = ev["center"]
    if c:
        lines.append(f"Center (inner 3x3 cells): elongation {pm(c['chi'], c['se'])} along {deg(c['psi'])}"
                     f"{sig(c['chi'], c['se'])}, n={c['n']} -> "
                     f"{'elongated' if c['z'] is not None and c['z'] >= SHAPE_MIN_Z else 'round'}.")
    corners = ev["corners"]
    if all(x is not None for x in corners):
        vals = [x["mean"] for x in corners]
        lines.append(f"Corners: eps_rad (+ radial, - tangential), spread {num(max(vals) - min(vals), 3)}.")
        for i, x in enumerate(corners):
            name = FWHM_CORNER_NAMES[i]
            lines.append(f"   {name[0].upper() + name[1:]}: {pm(x['mean'], x['se'])}{sig(x['mean'], x['se'])}, n={x['n']}")
    e = ev["edge"]
    if e:
        z = e["mean"] / e["se"] if e["se"] is not None and e["se"] > 0 else 0
        verdict = ("no significant pattern" if abs(z) < SHAPE_MIN_Z_1D else
                   "radial" if e["mean"] > 0 else "tangential")
        lines.append(f"Edge pattern (r > 2/3): eps_rad {pm(e['mean'], e['se'])}{sig(e['mean'], e['se'])} -> {verdict}.")
    m = ev["model"]
    if m:
        expl = f"{num(100 * m.explained, 0)}%" if m.explained is not None else "n/a"
        c2 = num(m.chi2dof, 2) if m.chi2dof is not None else "n/a"
        verdict = ("not judged" if m.chi2dof is None else "physically plausible" if m.chi2dof <= 1.5 else
                   "some structure the optics do not explain" if m.chi2dof <= 3 else
                   "much structure the optics do not explain")
        lines.append(f"Optics model: explains {expl} of the pattern; residual chi2/dof {c2} -> {verdict}.")
        kv = ("none" if not (m.k_se is not None and m.k_se > 0) or abs(m.k) < SHAPE_MIN_Z_1D * m.k_se else
              "radial" if m.k > 0 else "tangential")
        lines.append(f"   Curvature/spacing term: {pm(m.k, m.k_se)} at the corner{sig(m.k, m.k_se)} -> {kv}")
        lines.append(f"   Uniform term: {pm(m.constant, m.constant_se)}{sig(m.constant, m.constant_se)}")
        lines.append(f"   Linear astigmatism: {pm(m.binodal, m.binodal_se)} at the edge{sig(m.binodal, m.binodal_se)}")
        if m.axis:
            lines.append(f"   Axis of the pattern: ({num(m.axis['x'], 0)}, {num(m.axis['y'], 0)}) px, "
                         f"{num(100 * m.axis['offset'], 0)}% of the half diagonal off the center")
        if m.unexplained:
            lines.append(f"   Unexplained cells (> 3σ off the model): {len(m.unexplained)} (about "
                         f"{num(m.cells_used * math.exp(-4.5), 0)} expected by chance)")
    pos = sum(1 for d in ev["defects"] if d["index"] > 0)
    neg = len(ev["defects"]) - pos
    lines.append(f"Singular points: {len(ev['defects'])} (+: {pos}, -: {neg}) -> "
                 + ("as optics produce (at most two +, no -)" if neg == 0 and pos <= 2 else
                    "more than optics produce: noise in the smoothed field - raise the smoothing radius") + ".")
    n = ev["counts"]
    lines.append(f"Cells: {n['elongated']} elongated, {n['round']} round within the noise, {n['uncertain']} "
                 f"uncertain (significant from {num(SHAPE_MIN_Z, 2)}σ).")
    return lines


def _js_round(v: float) -> int:
    """Math.round of JavaScript: halves round up."""
    return int(math.floor(v + 0.5))
