"""Stage 2: the elliptical PSF fit (Gaussian or Moffat) of each candidate.

Replaces PixInsight's DynamicPSF. The model, in a box of the search radius
around the candidate:

    Gaussian  f = B + A exp(-(u^2 / (2 sx^2) + v^2 / (2 sy^2)))
    Moffat    f = B + A (1 + u^2 / sx^2 + v^2 / sy^2)^-beta

with (u, v) the coordinates rotated by phi. As DynamicPSF reports it,
sx >= sy, and theta is the angle of the sx axis in degrees counterclockwise
on the screen (image y points down: theta = -phi), in [0, 180). For a
Moffat fit, sx/sy are the core width alpha, FWHM = 2 alpha sqrt(2^(1/beta) - 1).

A fit is accepted when it converged, the amplitude exceeds threshold x the
local noise, the center stays in the box and the widths are plausible. Its
MAD is the mean absolute deviation of the residuals relative to the
amplitude; fits with a MAD above mad_outlier_factor x the median MAD are
rejected as outliers (hot pixels, blends, faint candidates).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import least_squares

from .detection import Candidate
from .log import Log
from .star import SIGMA_TO_FWHM, Star, moffat_alpha_to_fwhm
from .stats import median_of


def _model(p, u_x, u_y, moffat: bool):
    b, a, x0, y0, sx, sy, phi = p[:7]
    dx, dy = u_x - x0, u_y - y0
    c, s = math.cos(phi), math.sin(phi)
    u = dx * c + dy * s
    v = -dx * s + dy * c
    if moffat:
        beta = p[7]
        return b + a * (1 + (u / sx) ** 2 + (v / sy) ** 2) ** (-beta)
    return b + a * np.exp(-0.5 * ((u / sx) ** 2 + (v / sy) ** 2))


def fit_one(data: np.ndarray, cand: Candidate, radius: int, threshold: float, noise: float,
            moffat: bool) -> dict | None:
    """The fit of one candidate: {x, y, sx, sy, theta, beta, a, b, mad} or None."""
    h, w = data.shape
    xi, yi = int(round(cand.x)), int(round(cand.y))
    x0, x1 = max(0, xi - radius), min(w, xi + radius + 1)
    y0, y1 = max(0, yi - radius), min(h, yi + radius + 1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None
    box = data[y0:y1, x0:x1].astype(np.float64)
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    b0 = float(np.median(np.concatenate([box[0], box[-1], box[:, 0], box[:, -1]])))
    a0 = float(box.max()) - b0
    if a0 <= threshold * noise:
        return None
    s0 = max(0.8, cand.size / 2.5) if cand.size > 0 else 1.5
    p0 = [b0, a0, cand.x, cand.y, s0, s0 * 0.95, 0.0]
    lo = [-np.inf, 0.0, x0, y0, 0.3, 0.3, -np.inf]
    hi = [np.inf, np.inf, x1 - 1, y1 - 1, 2.0 * radius, 2.0 * radius, np.inf]
    if moffat:
        p0[4] *= 1.5
        p0[5] *= 1.5
        p0.append(2.5)
        lo.append(1.0)
        hi.append(10.0)
    fx, fy, fz = gx.ravel(), gy.ravel(), box.ravel()

    def resid(p):
        return _model(p, fx, fy, moffat) - fz

    try:
        res = least_squares(resid, p0, bounds=(lo, hi), method="trf", max_nfev=200, x_scale="jac")
    except (ValueError, np.linalg.LinAlgError):
        return None
    if not res.success:
        return None
    p = res.x
    b, a, cx, cy, sx, sy, phi = p[:7]
    if a <= threshold * noise or not (x0 <= cx <= x1 - 1 and y0 <= cy <= y1 - 1):
        return None
    if sx < sy:
        sx, sy = sy, sx
        phi += math.pi / 2
    theta = (-math.degrees(phi)) % 180.0
    mad = float(np.mean(np.abs(res.fun))) / a if a > 0 else math.inf
    return {"x": float(cx), "y": float(cy), "sx": float(sx), "sy": float(sy), "theta": theta,
            "beta": float(p[7]) if moffat else None, "a": float(a), "b": float(b), "mad": mad}


def fit_stars(channel: np.ndarray, cands: list[Candidate], threshold: float, radius: int, use_moffat: bool,
              mad_outlier_factor: float, noise: float, log: Log, progress=None) -> list[Star]:
    """Fits every candidate and returns the accepted stars. (Serially: the
    fits are small, and threads would only fight over the GIL.)"""
    log.write(f"PSF fit running ({len(cands)} candidates) ...")
    data = channel.astype(np.float64)
    fits: list[dict | None] = []
    for i, c in enumerate(cands):
        fits.append(fit_one(data, c, radius, threshold, noise, use_moffat))
        if progress is not None and (i + 1) % 50 == 0:
            progress((i + 1) / len(cands))

    accepted = [f for f in fits if f is not None]
    # Two candidates can converge on the same star: keep the first (brighter).
    unique, seen = [], set()
    for f in accepted:
        key = (int(f["x"] // 1.5), int(f["y"] // 1.5))
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    accepted = unique

    mad_cutoff = math.inf
    mad_median = 0.0
    if mad_outlier_factor > 0 and accepted:
        mad_median = median_of([f["mad"] for f in accepted])
        if mad_median > 0:
            mad_cutoff = mad_median * mad_outlier_factor
    stars = []
    rejected = 0
    for f in accepted:
        if f["mad"] > mad_cutoff:
            rejected += 1
            continue
        to_fwhm = moffat_alpha_to_fwhm(f["beta"]) if use_moffat and f["beta"] else SIGMA_TO_FWHM
        fwhm_x, fwhm_y = f["sx"] * to_fwhm, f["sy"] * to_fwhm
        major, minor = max(fwhm_x, fwhm_y), min(fwhm_x, fwhm_y)
        ecc = math.sqrt(1 - minor * minor / (major * major)) if major > 0 else 0.0
        stars.append(Star(f["x"], f["y"], fwhm_x, fwhm_y, ecc, f["theta"], flux=f["a"]))
    if rejected:
        log.note(f"{rejected} fit(s) rejected as MAD outliers (factor {mad_outlier_factor} x median).")
    log.note(f"{len(stars)} / {len(cands)} stars successfully fitted.")
    dropped = len(cands) - len(accepted)
    if dropped:
        log.write(f"Dropped {dropped} of {len(cands)} candidate(s): no convergence, amplitude below the threshold, "
                  "center outside the search box, or a duplicate of a fitted star.")
    if not stars and accepted:
        log.error(f"{len(accepted)} successful fits present, but all rejected by the MAD outlier filter - "
                  f"mad_outlier_factor ({mad_outlier_factor}) is too strict.")
    return stars
