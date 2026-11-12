"""Stage 1: candidate star positions.

Replaces PixInsight's StarDetector: the background and its noise are
estimated on a grid of tiles, the image is smoothed with a small Gaussian,
and local maxima above the background by a multiple of the noise are
candidates. The detector parameters of the settings keep their meaning:

    structure layers   the largest star accepted is about 2^n px across
    sensitivity        0..1, higher = fainter stars (lower threshold)
    peak response      0..1, higher = more tolerant of flat profiles
    max distortion     0..1, higher = more elongated stars accepted
    allow clustered    keep candidates with a brighter neighbor close by

The candidates are sorted by flux, brightest first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .log import Log


@dataclass
class Candidate:
    x: float
    y: float
    flux: float
    size: float
    bkg: float


def estimate_background(channel: np.ndarray, tile: int = 64) -> tuple[np.ndarray, float]:
    """(background map, noise sigma): the median of each tile, smoothed and
    interpolated to full size, and 1.4826 MAD of the background-subtracted
    image."""
    h, w = channel.shape
    ny, nx = max(1, h // tile), max(1, w // tile)
    th, tw = h // ny, w // nx
    crop = channel[:ny * th, :nx * tw].reshape(ny, th, nx, tw)
    med = np.median(crop, axis=(1, 3))
    med = ndimage.median_filter(med, size=3, mode="nearest")
    zoom = (h / med.shape[0], w / med.shape[1])
    bkg = ndimage.zoom(med, zoom, order=1, mode="nearest")[:h, :w]
    if bkg.shape != channel.shape:
        bkg = np.pad(bkg, ((0, h - bkg.shape[0]), (0, w - bkg.shape[1])), mode="edge")
    resid = channel - bkg
    sample = resid[::max(1, h // 1024), ::max(1, w // 1024)]
    sigma = 1.4826 * float(np.median(np.abs(sample - np.median(sample))))
    return bkg.astype(np.float32), max(sigma, 1e-9)


def detect_star_candidates(channel: np.ndarray, settings, log: Log) -> list[Candidate]:
    """The candidates, brightest first, at most max_candidates."""
    if settings.sd_custom:
        layers = settings.sd_structure_layers
        sensitivity = settings.sd_sensitivity
        peak_response = settings.sd_peak_response
        max_distortion = settings.sd_max_distortion
        allow_clustered = settings.sd_allow_clustered
        log.write(f"Star detector parameters: structureLayers={layers}  sensitivity={sensitivity:.2f}  "
                  f"peakResponse={peak_response:.2f}  maxDistortion={max_distortion:.2f}  "
                  f"allowClusteredSources={'true' if allow_clustered else 'false'}")
    else:
        layers, sensitivity, peak_response, max_distortion, allow_clustered = 5, 0.5, 0.5, 0.6, False

    log.write("Star detection running ...")
    data = channel.astype(np.float32)
    bkg, sigma = estimate_background(data)
    resid = data - bkg
    smooth = ndimage.gaussian_filter(resid, 1.0)
    k = 1.5 + 5.0 * (1.0 - sensitivity)  # 0.5 -> 4 sigma
    thresh = k * sigma
    size = 5
    peaks = (smooth == ndimage.maximum_filter(smooth, size=size, mode="nearest")) & (smooth > thresh)
    ys, xs = np.nonzero(peaks)
    h, w = data.shape
    max_size = 2 ** layers
    cands: list[Candidate] = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        if x < 2 or y < 2 or x >= w - 2 or y >= h - 2:
            continue
        # Second moments in a small window: centroid, size and distortion.
        r = 5
        y0, y1 = max(0, y - r), min(h, y + r + 1)
        x0, x1 = max(0, x - r), min(w, x + r + 1)
        win = np.clip(resid[y0:y1, x0:x1], 0, None)
        tot = float(win.sum())
        if tot <= 0:
            continue
        gy, gx = np.mgrid[y0:y1, x0:x1]
        cx = float((win * gx).sum()) / tot
        cy = float((win * gy).sum()) / tot
        mxx = float((win * (gx - cx) ** 2).sum()) / tot
        myy = float((win * (gy - cy) ** 2).sum()) / tot
        mxy = float((win * (gx - cx) * (gy - cy)).sum()) / tot
        tr = mxx + myy
        det = mxx * myy - mxy * mxy
        if tr <= 0:
            continue
        disc = math.sqrt(max(0.0, tr * tr / 4 - det))
        l1, l2 = tr / 2 + disc, max(tr / 2 - disc, 1e-9)
        distortion = 1 - math.sqrt(l2 / l1)
        if distortion > max_distortion:
            continue
        # The size (FWHM of the major axis, from the moments): larger than
        # the largest structure the layers allow -> no star.
        size = 2.3548 * math.sqrt(l1)
        if size > max_size:
            continue
        # Peak response: the peak against the mean of the central 3 x 3 -
        # a flat profile (saturated blob, extended object) has a ratio near 1.
        peak = float(resid[y, x])
        core = resid[max(0, y - 1):y + 2, max(0, x - 1):x + 2]
        core_mean = float(core.mean())
        if peak > 0 and core_mean > 0 and peak / core_mean < 1.0 + 0.3 * (1 - peak_response) - 0.15:
            continue
        cands.append(Candidate(cx, cy, tot, size, float(bkg[y, x])))

    if not allow_clustered and cands:
        cands.sort(key=lambda c: -c.flux)
        kept: list[Candidate] = []
        grid: dict[tuple[int, int], list[Candidate]] = {}
        cell = 4.0
        for c in cands:
            key = (int(c.x // cell), int(c.y // cell))
            near = [o for dj in (-1, 0, 1) for di in (-1, 0, 1) for o in grid.get((key[0] + di, key[1] + dj), [])]
            if any(math.hypot(o.x - c.x, o.y - c.y) < 3.0 for o in near):
                continue
            kept.append(c)
            grid.setdefault(key, []).append(c)
        cands = kept

    cands.sort(key=lambda c: -c.flux)
    log.note(f"{len(cands)} candidate(s) found by the star detector.")
    if settings.max_candidates > 0 and len(cands) > settings.max_candidates:
        total = len(cands)
        cands = cands[:settings.max_candidates]
        log.note(f"Limited to the {settings.max_candidates} brightest candidates (of {total} found) - reduces the "
                 "runtime of the subsequent PSF fit.")
    return cands
