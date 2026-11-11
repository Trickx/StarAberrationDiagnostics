"""The tracking component: the elongation shared by all stars (tracking,
guiding, wind, flexure).

It is estimated as the uniform term of the optics model (fit_shape_model()),
fitted together with the radial and linear terms. The plain median over the
field is biased by the optics (a radial pattern averages to an elongation
along the long side of a non-square sensor, an off-center axis shows mostly
one side); the model takes both out. The median is kept for comparison and
is the fallback when the model cannot be fitted. On a single frame,
astigmatism on the axis still looks like tracking; a series tells them
apart.
"""

from __future__ import annotations

import math

from .fwhm_grid import FWHM_GRID_SIZE
from .log import Log
from .shape import (distortion_from_ecc, ecc_from_distortion, ellipticity_from_ecc, fit_shape_model,
                    star_image_angle)
from .star import Star
from .stats import median_of


def _component(t1: float, t2: float) -> dict:
    chi = math.hypot(t1, t2)
    ecc = ecc_from_distortion(chi)
    return {"chi1": t1, "chi2": t2, "chi": chi, "eccentricity": ecc, "ellipticity": ellipticity_from_ecc(ecc),
            "angle_deg": (math.degrees(0.5 * math.atan2(t2, t1)) % 180 + 180) % 180}


def compute_tracking_component(stars: list[Star], w: float, h: float) -> dict:
    """{chi1, chi2, chi, eccentricity, ellipticity, angle_deg, method
    ("model" or "median"), se, median}."""
    c1, c2 = [], []
    for s in stars:
        chi = distortion_from_ecc(s.eccentricity)
        psi = star_image_angle(s)
        c1.append(chi * math.cos(2 * psi))
        c2.append(chi * math.sin(2 * psi))
    median = _component(median_of(c1), median_of(c2))
    fit = fit_shape_model(stars, w, h, FWHM_GRID_SIZE) if w > 0 and h > 0 else None
    if fit is None:
        median["method"] = "median"
        median["se"] = None
        median["median"] = dict(median)
        return median
    result = _component(fit.uniform1, fit.uniform2)
    result["method"] = "model"
    result["se"] = fit.constant_se
    result["median"] = median
    return result


def apply_tracking_correction(stars: list[Star], tracking: dict) -> None:
    """Subtracts the component from every star (in the distortion
    components); fwhm_x/fwhm_y stay, they feed the size statistics."""
    for s in stars:
        chi = distortion_from_ecc(s.eccentricity)
        psi = star_image_angle(s)
        c1 = chi * math.cos(2 * psi) - tracking["chi1"]
        c2 = chi * math.sin(2 * psi) - tracking["chi2"]
        s.eccentricity = ecc_from_distortion(math.hypot(c1, c2))
        s.rotation = -math.degrees(0.5 * math.atan2(c2, c1))


def log_tracking_component(tracking: dict, subtracted: bool, log: Log) -> None:
    log.note("--- Tracking / global elongation ---")
    if tracking["method"] == "model":
        log.write(f"Uniform term of the optics model: ellipticity {tracking['ellipticity']:.3f} ± "
                  f"{tracking['se'] or 0:.3f} (eccentricity {tracking['eccentricity']:.2f}), major axis at "
                  f"{tracking['angle_deg']:.0f}° (image coordinates: 0° = +x/right, 90° = +y/down).")
        md = tracking["median"]
        log.write(f"For comparison, the plain median over the field: ellipticity {md['ellipticity']:.3f} at "
                  f"{md['angle_deg']:.0f}°.")
        diff = math.hypot(md["chi1"] - tracking["chi1"], md["chi2"] - tracking["chi2"])
        if diff > 0.01 and diff > 0.3 * md["chi"]:
            log.write(f"-> the median differs by {diff:.3f}: that part of it comes from the optics (a radial "
                      "pattern on the non-square sensor, or an off-center axis), not from tracking.")
    else:
        log.write(f"Median elongation over the whole field: ellipticity {tracking['ellipticity']:.3f} "
                  f"(eccentricity {tracking['eccentricity']:.2f}), major axis at {tracking['angle_deg']:.0f}° "
                  "(image coordinates: 0° = +x/right, 90° = +y/down).")
    if tracking["ellipticity"] < 0.03:
        log.write("-> negligible (< 0.03): tracking/guiding looks clean.")
    else:
        log.write("-> a uniform elongation shared by all stars comes from tracking/guiding, wind or flexure, "
                  "not from the optics.")
    log.write("This component has been SUBTRACTED from every star before drawing and analysis." if subtracted else
              "Not subtracted (option disabled) - it is contained in the vector map and all shape statistics.")
