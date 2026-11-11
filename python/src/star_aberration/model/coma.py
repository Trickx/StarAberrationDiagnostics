"""PSF asymmetry and the coma field.

Gaussian and Moffat models are point-symmetric, so the fit alone cannot
tell on which side of the core a comatic flare sits. It is measured
directly on the pixels, relative to the fit center:
    asym_x/y = flux centroid inside the aperture minus fit center (px)
    m3_x/y   = normalized third moment sum w rho^2 rho / sum w, rho = d/sigma
               (dimensionless; weights the outer profile more strongly)
Both point toward the flare. Measured on channel 0, the channel the PSF
fits use: in a color image, lateral color shifts the other channels
radially and would show a coma the optics do not have.

The coma field model v(P) = k (P - P0) / r_max: third-order coma grows
linearly with the field distance and points radially away from the optical
axis; miscollimation shifts that axis (a constant term). The coma-free point
is where v = 0. Uncertainty via bootstrap resampling of the stars.
"""

from __future__ import annotations

import math

import numpy as np

from .log import Log
from .star import SIGMA_TO_FWHM, Star
from .stats import median_of, solve_least_squares_clipped
from .streamlines import StarIndex


def measure_star_asymmetry(channel: np.ndarray, stars: list[Star], progress=None) -> int:
    """Sets asym_x/y and m3_x/y of each star (None when saturated, too close
    to the border or without signal). Returns the number measured."""
    h, w = channel.shape
    measured = 0
    for i, s in enumerate(stars):
        s.asym_x = s.asym_y = s.m3_x = s.m3_y = None
        if progress is not None and i % 200 == 0:
            progress(i / max(1, len(stars)))
        fwhm = 0.5 * (s.fwhm_x + s.fwhm_y)
        sigma = fwhm / SIGMA_TO_FWHM
        r_ap = min(25.0, max(3.0, 4 * sigma + 1))
        r_in, r_out = r_ap + 2, r_ap + 6
        x0, x1 = int(math.floor(s.x - r_out)), int(math.ceil(s.x + r_out))
        y0, y1 = int(math.floor(s.y - r_out)), int(math.ceil(s.y + r_out))
        if x0 < 0 or y0 < 0 or x1 >= w or y1 >= h or not (sigma > 0):
            continue
        box = channel[y0:y1 + 1, x0:x1 + 1].astype(np.float64)
        dx = np.arange(x0, x1 + 1) - s.x
        dy = (np.arange(y0, y1 + 1) - s.y)[:, None]
        r = np.sqrt(dx * dx + dy * dy)
        ap = r <= r_ap
        ring = (r >= r_in) & (r <= r_out)
        peak = float(box[ap].max()) if ap.any() else 0.0
        bg_vals = box[ring]
        if peak >= 0.95 or bg_vals.size < 10:
            continue
        bg = median_of(bg_vals.tolist())
        sel = (dx * dx + dy * dy <= r_ap * r_ap)
        wgt = box - bg
        sel &= wgt > 0
        if not sel.any():
            continue
        wv = wgt[sel]
        ddx = np.broadcast_to(dx, box.shape)[sel]
        ddy = np.broadcast_to(dy, box.shape)[sel]
        rx, ry = ddx / sigma, ddy / sigma
        rho2 = rx * rx + ry * ry
        sw = float(wv.sum())
        if sw > 0:
            s.asym_x = float((wv * ddx).sum()) / sw
            s.asym_y = float((wv * ddy).sum()) / sw
            s.m3_x = float((wv * rho2 * rx).sum()) / sw
            s.m3_y = float((wv * rho2 * ry).sum()) / sw
            measured += 1
    if progress is not None:
        progress(1.0)
    return measured


def fit_coma_field(stars: list[Star], w: float, h: float, use_centroid: bool, rng=None) -> dict | None:
    """{k, ox, oy, x0, y0, n, r_max, k_sd, x0_sd, y0_sd, significant} or None
    (fewer than 30 stars with an asymmetry, or no fit)."""
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    pts = []
    for s in stars:
        vx = s.asym_x if use_centroid else s.m3_x
        vy = s.asym_y if use_centroid else s.m3_y
        if vx is None:
            continue
        pts.append(((s.x - cx) / r_max, (s.y - cy) / r_max, vx, vy))
    if len(pts) < 30:
        return None

    def fit(sample):
        rows, zs = [], []
        for p in sample:
            rows.append([p[0], 1, 0])
            zs.append(p[2])
            rows.append([p[1], 0, 1])
            zs.append(p[3])
        res = solve_least_squares_clipped(rows, zs, 3)
        if res is None or res["coeffs"] is None or abs(res["coeffs"][0]) < 1e-12:
            return None
        k, ox, oy = res["coeffs"]
        return {"k": k, "ox": ox, "oy": oy, "x0": cx - ox / k * r_max, "y0": cy - oy / k * r_max}

    main = fit(pts)
    if main is None:
        return None
    rng = rng or np.random.default_rng()
    bx, by, bk = [], [], []
    n = len(pts)
    for _ in range(100):
        idx = rng.integers(0, n, n)
        r = fit([pts[i] for i in idx])
        if r is not None:
            bx.append(r["x0"])
            by.append(r["y0"])
            bk.append(r["k"])

    def sd(a):
        if len(a) < 2:
            return float("nan")
        m = sum(a) / len(a)
        return math.sqrt(sum((q - m) ** 2 for q in a) / (len(a) - 1))

    def robust_sd(a):
        # A few bootstrap runs with k near 0 can throw P0 arbitrarily far.
        if len(a) < 2:
            return float("nan")
        m = median_of(a)
        return 1.4826 * median_of([abs(q - m) for q in a])

    main.update(n=n, r_max=r_max, k_sd=sd(bk), x0_sd=robust_sd(bx), y0_sd=robust_sd(by))
    main["significant"] = bool(abs(main["k"]) > 3 * main["k_sd"])
    return main


def log_coma_field(fit_m3: dict | None, fit_centroid: dict | None, w: float, h: float, pixel_pitch_eff_um: float,
                   log: Log) -> None:
    log.note("--- Coma field (PSF asymmetry) ---")
    if fit_m3 is None:
        log.warning("Too few stars with a valid asymmetry measurement - coma field skipped.")
        return
    cx, cy = w / 2, h / 2
    log.write(f"Stars used: {fit_m3['n']}. Coma strength at the field edge k = {fit_m3['k']:.3f} ± "
              f"{fit_m3['k_sd']:.3f} (third moment, dimensionless; positive = flare points AWAY from the coma-free "
              "point, negative = toward it, i.e. over-corrected).")
    if fit_centroid is not None:
        log.write(f"Cross-check with the centroid offset: k = {fit_centroid['k']:.3f} ± {fit_centroid['k_sd']:.3f} px, "
                  f"coma-free point ({fit_centroid['x0']:.0f}, {fit_centroid['y0']:.0f}).")
    if not fit_m3["significant"]:
        log.write("-> No significant field-dependent coma (|k| < 3σ): either well corrected, or the signal is too "
                  "weak for a single frame. The coma-free point is NOT determinable.")
        return
    dx, dy = fit_m3["x0"] - cx, fit_m3["y0"] - cy
    dist = math.hypot(dx, dy)
    line = (f"Coma-free point: ({fit_m3['x0']:.0f} ± {fit_m3['x0_sd']:.0f}, {fit_m3['y0']:.0f} ± {fit_m3['y0_sd']:.0f}) "
            f"px, image center ({cx:.0f}, {cy:.0f}) -> offset {dist:.0f} px = {100 * dist / fit_m3['r_max']:.0f}% of "
            "the half diagonal")
    if pixel_pitch_eff_um > 0:
        line += f" = {dist * pixel_pitch_eff_um / 1000:.2f} mm on the sensor"
    log.write(line + ".")
    log.write("Caveats: an uneven tracking drift during the exposure also adds a constant asymmetry and shifts this "
              "point; with a coma corrector or in a refractor k is small, which amplifies every error. Compare "
              "several short exposures before touching the collimation screws.")


# ---------------------------------------------------------------------------
# Coma arrows on a coarse grid and streamlines of the smoothed field

def coma_grid_arrows(stars: list[Star], w: float, h: float, nx: int = 9, ny: int = 6) -> list[dict]:
    """The median third moment per cell of a coarse grid (cells with at least
    5 stars): [{x, y, vx, vy}] at the cell centers."""
    cell_w, cell_h = w / nx, h / ny
    cells = [([], []) for _ in range(nx * ny)]
    for s in stars:
        if s.m3_x is None:
            continue
        i = min(nx - 1, max(0, int(math.floor(s.x / cell_w))))
        j = min(ny - 1, max(0, int(math.floor(s.y / cell_h))))
        cells[j * nx + i][0].append(s.m3_x)
        cells[j * nx + i][1].append(s.m3_y)
    out = []
    for j in range(ny):
        for i in range(nx):
            vx, vy = cells[j * nx + i]
            if len(vx) < 5:
                continue
            out.append({"x": (i + 0.5) * cell_w, "y": (j + 0.5) * cell_h, "vx": median_of(vx), "vy": median_of(vy)})
    return out


class ComaField:
    """The asymmetry (third moment) smoothed with a Gaussian kernel (sigma =
    radius/2, cut off at the radius); None with fewer than 8 stars near."""

    def __init__(self, stars: list[Star], radius: float):
        with_m3 = [s for s in stars if s.m3_x is not None]
        self.index = StarIndex(with_m3, radius)
        self.m3x = np.array([s.m3_x for s in with_m3], dtype=float)
        self.m3y = np.array([s.m3_y for s in with_m3], dtype=float)
        self.r2 = radius * radius
        self.sigma2 = 2 * (radius * 0.5) ** 2

    def at(self, x: float, y: float):
        idx = self.index.near(x, y)
        if idx.size == 0:
            return None
        d2 = (self.index.x[idx] - x) ** 2 + (self.index.y[idx] - y) ** 2
        sel = d2 <= self.r2
        n = int(sel.sum())
        if n < 8:
            return None
        wgt = np.exp(-d2[sel] / self.sigma2)
        sw = float(wgt.sum())
        if sw < 1e-6:
            return None
        vx = float((wgt * self.m3x[idx][sel]).sum()) / sw
        vy = float((wgt * self.m3y[idx][sel]).sum()) / sw
        return vx, vy, math.hypot(vx, vy)


def _trace_coma(field: ComaField, x0, y0, direction, step_len, max_steps, w, h, min_mag):
    points = []
    x, y = x0, y0
    pdx = pdy = 0.0
    for step in range(max_steps):
        f = field.at(x, y)
        if f is None or f[2] < min_mag:
            break
        dx, dy = direction * f[0] / f[2], direction * f[1] / f[2]
        if step > 0 and dx * pdx + dy * pdy < 0:
            break
        x += dx * step_len
        y += dy * step_len
        if x < 0 or x > w or y < 0 or y > h:
            break
        points.append((x, y))
        pdx, pdy = dx, dy
    return points


def generate_coma_streamlines(stars: list[Star], w: float, h: float, radius: float, min_percent: float) -> dict:
    """Lines through the smoothed asymmetry field, each ordered along the
    flow; they stop below min_percent % of the 90th percentile of the field
    magnitude at the seeds. Returns {lines, min_mag}."""
    field = ComaField(stars, radius)
    spacing, step_len, max_steps = radius * 0.6, radius * 0.2, 200
    seeds = []
    sy = spacing / 2
    while sy < h:
        sx = spacing / 2
        while sx < w:
            f = field.at(sx, sy)
            if f is not None:
                seeds.append((sx, sy, f[2]))
            sx += spacing
        sy += spacing
    if not seeds:
        return {"lines": [], "min_mag": 0.0}
    mags = sorted(s[2] for s in seeds)
    p90 = mags[min(len(mags) - 1, int(math.floor(0.9 * len(mags))))]
    min_mag = p90 * min_percent / 100
    lines = []
    for sx, sy, mag in seeds:
        if mag < min_mag:
            continue
        fwd = _trace_coma(field, sx, sy, 1, step_len, max_steps, w, h, min_mag)
        bwd = _trace_coma(field, sx, sy, -1, step_len, max_steps, w, h, min_mag)
        full = bwd[::-1] + [(sx, sy)] + fwd
        if len(full) > 2:
            lines.append(full)
    return {"lines": lines, "min_mag": min_mag}
