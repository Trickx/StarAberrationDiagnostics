"""Robust statistics and least squares shared by the evaluations.

The functions follow the original script exactly (median of an even count
= the upper middle value, normal equations solved by Gaussian elimination),
so that the numbers agree with it.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def median_of(values: Iterable[float]) -> float:
    """The median; for an even count the upper of the two middle values."""
    v = sorted(values)
    if not v:
        return float("nan")
    return v[len(v) // 2]


def trimmed_mean25(values: Sequence[float]) -> float:
    """25%-trimmed mean (as Siril's siril_stats_trmean_from_sorted_data with
    p = 0.25): drops a quarter of the values at each end."""
    v = sorted(values)
    n = len(v)
    k = int(n * 0.25)
    t = v[k:n - k] or v
    return sum(t) / len(t)


def robust_spread(values: Sequence[float]) -> float:
    """1.4826 x MAD: the standard deviation of a normal distribution,
    insensitive to outliers; NaN below 2 values."""
    v = list(values)
    if len(v) < 2:
        return float("nan")
    m = median_of(v)
    return 1.4826 * median_of([abs(x - m) for x in v])


def scalar_mean(values: Sequence[float]) -> dict | None:
    """Mean of plain values with its standard error: {mean, se, n}."""
    v = list(values)
    n = len(v)
    if n == 0:
        return None
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v)
    return {"mean": m, "se": math.sqrt(var / (n - 1) / n) if n > 1 else None, "n": n}


def solve_linear_system(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solves M x = v by Gaussian elimination with partial pivoting; None
    when M is (numerically) singular."""
    n = len(v)
    a = [list(row) + [v[i]] for i, row in enumerate(m)]
    for col in range(n):
        max_row, max_val = col, abs(a[col][col])
        for r in range(col + 1, n):
            if abs(a[r][col]) > max_val:
                max_val, max_row = abs(a[r][col]), r
        if max_val < 1e-12:
            return None
        if max_row != col:
            a[col], a[max_row] = a[max_row], a[col]
        for r in range(col + 1, n):
            f = a[r][col] / a[col][col]
            for c in range(col, n + 1):
                a[r][c] -= f * a[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = a[r][n]
        for c in range(r + 1, n):
            s -= a[r][c] * x[c]
        x[r] = s / a[r][r]
    return x


def solve_least_squares(rows: Sequence[Sequence[float]], zs: Sequence[float],
                        mask: Sequence[bool] | None = None) -> list[float] | None:
    """Ordinary least squares z ~ rows[i] . coeffs over the active points
    (mask[i] true, or all without a mask), via the normal equations."""
    n = len(rows[0])
    m = [[0.0] * n for _ in range(n)]
    v = [0.0] * n
    count = 0
    for i, b in enumerate(rows):
        if mask is not None and not mask[i]:
            continue
        count += 1
        z = zs[i]
        for r in range(n):
            v[r] += b[r] * z
            br = b[r]
            row = m[r]
            for c in range(n):
                row[c] += br * b[c]
    if count < n:
        return None
    return solve_linear_system(m, v)


def solve_least_squares_clipped(rows, zs, iterations: int) -> dict | None:
    """Robust least squares: solve_least_squares() repeated with 3-sigma
    (MAD) clipping of the residuals. Returns {coeffs, mask} or None."""
    mask = None
    coeffs = None
    for _ in range(iterations):
        coeffs = solve_least_squares(rows, zs, mask)
        if coeffs is None:
            return None
        resid = [zs[i] - sum(b[k] * coeffs[k] for k in range(len(b))) for i, b in enumerate(rows)]
        sigma = 1.4826 * median_of([abs(r) for r in resid])
        if not (sigma > 0):
            break
        mask = [abs(r) < 3 * sigma for r in resid]
    return {"coeffs": coeffs, "mask": mask}


def poly_exponents(deg: int) -> list[tuple[int, int]]:
    """The (i, j) exponent pairs of a 2D polynomial: x^i y^j for every
    i + j <= deg, ordered by total degree, then by i."""
    return [(i, total - i) for total in range(deg + 1) for i in range(total + 1)]


def fit_poly2d(xs, ys, zs, mask, exps) -> list[float] | None:
    """Least-squares fit of z ~ sum c_k x^i_k y^j_k over the active points."""
    rows = [[x ** i * y ** j for i, j in exps] for x, y in zip(xs, ys)]
    if not rows:
        return None
    return solve_least_squares(rows, zs, mask)


def eval_poly2d(coeffs, exps, x, y):
    z = 0.0
    for c, (i, j) in zip(coeffs, exps):
        z += c * x ** i * y ** j
    return z
