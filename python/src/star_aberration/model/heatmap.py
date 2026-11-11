"""Orientation heatmap as in the Seti Astro Suite (compute_orientation_surface()).

The PSF rotation is only defined mod 180 degrees, so a double-angle
representation (sin 2 theta, cos 2 theta) is fitted per star as a 2D
polynomial of the given degree over x, y (least squares, iterative 3-sigma
clipping). Degree 1 (a plane) shows only a uniform gradient; degree 2 or
higher also follows curvature (coma), at the cost of noise near the edges.
The fitted orientation is encoded as the hue of an HSV color wheel.
"""

from __future__ import annotations

import math

import numpy as np

from .star import Star
from .stats import eval_poly2d, fit_poly2d, poly_exponents


def hsv_to_rgb(h: np.ndarray) -> np.ndarray:
    """Hue [0, 1) at full saturation and value -> RGB in [0, 1] (shape (..., 3))."""
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    q = 1 - f
    one, zero = np.ones_like(h), np.zeros_like(h)
    table = [
        (one, f, zero), (q, one, zero), (zero, one, f),
        (zero, q, one), (f, zero, one), (one, zero, q),
    ]
    out = np.zeros(h.shape + (3,))
    for k, (r, g, b) in enumerate(table):
        sel = i == k
        out[..., 0][sel] = r[sel]
        out[..., 1][sel] = g[sel]
        out[..., 2][sel] = b[sel]
    return out


def orientation_heatmap_rgb(stars: list[Star], w: int, h: int, degree: int) -> np.ndarray | None:
    """The heatmap as uint8 RGB (h, w, 3), evaluated in cells of about 1/200
    of the long side; None with too few stars or a singular fit."""
    exps = poly_exponents(degree)
    if len(stars) < 2 * len(exps):
        return None
    scale = max(w, h) / 2
    cx, cy = w / 2, h / 2
    xs = [(s.x - cx) / scale for s in stars]
    ys = [(s.y - cy) / scale for s in stars]
    th = [math.radians(s.rotation) for s in stars]
    s_arr = [math.sin(2 * t) for t in th]
    c_arr = [math.cos(2 * t) for t in th]
    mask = None
    sol_s = sol_c = None
    for _ in range(3):
        sol_s = fit_poly2d(xs, ys, s_arr, mask, exps)
        sol_c = fit_poly2d(xs, ys, c_arr, mask, exps)
        if sol_s is None or sol_c is None:
            return None
        resid = [math.hypot(s_arr[i] - eval_poly2d(sol_s, exps, xs[i], ys[i]),
                            c_arr[i] - eval_poly2d(sol_c, exps, xs[i], ys[i])) for i in range(len(xs))]
        active = [r for i, r in enumerate(resid) if mask is None or mask[i]]
        mean = sum(active) / len(active)
        std = math.sqrt(sum((r - mean) ** 2 for r in active) / len(active))
        new_mask = [std == 0 or r < 3.0 * std for r in resid]
        n_active = sum(new_mask)
        prev = sum(mask) if mask is not None else len(xs)
        if n_active == prev or n_active < 2 * len(exps):
            break
        mask = new_mask

    cell = max(4, int(math.floor(max(w, h) / 200 + 0.5)))
    xs0 = np.arange(0, w, cell)
    ys0 = np.arange(0, h, cell)
    xm = (np.minimum(xs0 + cell, w) + xs0) / 2
    ym = (np.minimum(ys0 + cell, h) + ys0) / 2
    gx, gy = np.meshgrid((xm - cx) / scale, (ym - cy) / scale)
    fs = eval_poly2d(sol_s, exps, gx, gy)
    fc = eval_poly2d(sol_c, exps, gx, gy)
    theta = 0.5 * np.arctan2(fs, fc)
    hue = (theta + math.pi / 2) / math.pi
    hue = hue - np.floor(hue)
    rgb = np.round(hsv_to_rgb(hue) * 255).astype(np.uint8)
    full = np.repeat(np.repeat(rgb, cell, axis=0), cell, axis=1)[:h, :w]
    return np.ascontiguousarray(full)
