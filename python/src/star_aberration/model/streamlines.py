"""Streamlines of an axis field: the smoothed large-scale trend of the PSF
orientation (or of the shape model), in addition to the noisy per-star
ellipses.

An axis is defined only modulo 180 degrees (a "director field"): the
smoothing works on the doubled angle (structure-tensor trick), and a line
picks at each step whichever of the two directions is closer to its last
step.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

from .star import Star

FieldFn = Callable[[float, float], "float | None"]  # (x, y) -> axis angle (radians, image coordinates) or None
GateFn = Callable[[float, float], bool]


class StarIndex:
    """The stars sorted into square buckets of a given size, so that the
    smoothing looks only at the 3 x 3 buckets around a point."""

    def __init__(self, stars: list[Star], bucket: float):
        self.bucket = bucket
        self.x = np.array([s.x for s in stars], dtype=float)
        self.y = np.array([s.y for s in stars], dtype=float)
        self.ecc = np.array([s.eccentricity for s in stars], dtype=float)
        self.rot = np.array([s.rotation for s in stars], dtype=float)
        bi = np.floor(self.x / bucket).astype(int)
        bj = np.floor(self.y / bucket).astype(int)
        self.buckets: dict[tuple[int, int], np.ndarray] = {}
        order = np.lexsort((bi, bj))
        keys = list(zip(bi[order].tolist(), bj[order].tolist()))
        start = 0
        for k in range(1, len(keys) + 1):
            if k == len(keys) or keys[k] != keys[start]:
                self.buckets[keys[start]] = order[start:k]
                start = k

    def near(self, x: float, y: float) -> np.ndarray:
        """The indices of the stars in the 3 x 3 buckets around (x, y)."""
        bi, bj = int(math.floor(x / self.bucket)), int(math.floor(y / self.bucket))
        parts = [self.buckets.get((bi + di, bj + dj)) for dj in (-1, 0, 1) for di in (-1, 0, 1)]
        parts = [p for p in parts if p is not None]
        if not parts:
            return np.empty(0, dtype=int)
        return np.concatenate(parts)


def orientation_field(stars: list[Star], radius: float) -> FieldFn:
    """The orientation field smoothed with a Gaussian kernel (sigma =
    radius / 2, cut off at the radius), each star weighted by
    eccentricity^2: nearly round stars barely define an axis.

    The fit's rotation is counterclockwise on the screen while y points
    down: the major axis points along (cos(-theta), sin(-theta)) in image
    coordinates."""
    index = StarIndex(stars, radius)
    r2 = radius * radius
    sigma2 = 2 * (radius * 0.5) ** 2

    def at(x: float, y: float):
        idx = index.near(x, y)
        if idx.size == 0:
            return None
        dx = index.x[idx] - x
        dy = index.y[idx] - y
        d2 = dx * dx + dy * dy
        sel = d2 <= r2
        if not sel.any():
            return None
        w = np.exp(-d2[sel] / sigma2) * index.ecc[idx][sel] ** 2
        pos = w > 0
        if not pos.any():
            return None
        w = w[pos]
        t2 = 2 * np.radians(index.rot[idx][sel][pos])
        sw = w.sum()
        if sw < 1e-6:
            return None
        return -math.atan2(float((w * np.sin(t2)).sum()), float((w * np.cos(t2)).sum())) / 2

    return at


def trace_streamline(field_at: FieldFn, x0: float, y0: float, dx0: float, dy0: float, step_len: float,
                     max_steps: int, w: float, h: float, gate: GateFn | None = None) -> list[tuple[float, float]]:
    points = []
    x, y = x0, y0
    pdx, pdy = dx0, dy0
    for _ in range(max_steps):
        if gate is not None and not gate(x, y):
            break
        angle = field_at(x, y)
        if angle is None:
            break
        dx, dy = math.cos(angle), math.sin(angle)
        if dx * pdx + dy * pdy < 0:
            dx, dy = -dx, -dy
        x += dx * step_len
        y += dy * step_len
        if x < 0 or x > w or y < 0 or y > h:
            break
        points.append((x, y))
        pdx, pdy = dx, dy
    return points


def trace_field(field_at: FieldFn, w: float, h: float, radius: float,
                gate: GateFn | None = None) -> list[list[tuple[float, float]]]:
    """Streamlines seeded on a regular grid (spacing 0.6 radius) and traced
    both ways from each seed."""
    spacing = radius * 0.6
    step_len = radius * 0.25
    max_steps = 200
    lines = []
    sy = spacing / 2
    while sy < h:
        sx = spacing / 2
        while sx < w:
            if gate is None or gate(sx, sy):
                a0 = field_at(sx, sy)
                if a0 is not None:
                    dx0, dy0 = math.cos(a0), math.sin(a0)
                    fwd = trace_streamline(field_at, sx, sy, dx0, dy0, step_len, max_steps, w, h, gate)
                    bwd = trace_streamline(field_at, sx, sy, -dx0, -dy0, step_len, max_steps, w, h, gate)
                    full = bwd[::-1] + [(sx, sy)] + fwd
                    if len(full) > 2:
                        lines.append(full)
            sx += spacing
        sy += spacing
    return lines


def generate_streamlines(stars: list[Star], w: float, h: float, radius: float,
                         gate: GateFn | None = None) -> list[list[tuple[float, float]]]:
    return trace_field(orientation_field(stars, radius), w, h, radius, gate)
