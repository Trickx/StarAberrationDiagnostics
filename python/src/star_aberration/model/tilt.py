"""Sensor tilt from Siril's quadrant FWHM values: the tilt axis, the tilt
angle estimate and the plane of the pseudo-3D plot.

The chain from blur to tilt: excess blur relative to the sensor center
(quadratic, since focus and defocus blur add in quadrature) -> physical
defocus via the circle-of-confusion approximation (dz = f-number x blur)
-> tilt angle via tan(theta) = dz / distance between the image center and
the quadrant center. The reference (dz = 0) is the sensor center
(m_inner). Only a rough estimate: geometric optics, no diffraction or
seeing deconvolution. The sign of dz (nearer or farther) is unknown from a
single frame; for the direction of the fitted plane only the relative
values matter.
"""

from __future__ import annotations

import math


def _signed_excess(fwhm: float, center: float) -> float:
    diff = fwhm * fwhm - center * center
    return (-1 if diff < 0 else 1) * math.sqrt(abs(diff))


def signed_defocus_um(fwhm: float, center_fwhm: float, f_ratio: float, pixel_pitch_um: float) -> float:
    """A signed dz estimate (um) from a quadrant's excess blur."""
    return f_ratio * _signed_excess(fwhm, center_fwhm) * pixel_pitch_um


def compute_tilt_axis(tilt: dict, w: int, h: int, pixel_pitch_um: float, focal_length_mm: float,
                      aperture_mm: float) -> dict:
    """The line through the sensor center along which the plane fitted to the
    four quadrant excesses rises most steeply: {direction_deg 0..180,
    angle_deg (None without pixel pitch, focal length and aperture)}."""
    e_tl = _signed_excess(tilt["m1"], tilt["m_inner"])
    e_tr = _signed_excess(tilt["m2"], tilt["m_inner"])
    e_bl = _signed_excess(tilt["m3"], tilt["m_inner"])
    e_br = _signed_excess(tilt["m4"], tilt["m_inner"])
    px, py = w / 4, h / 4
    grad_x = ((e_tr + e_br) - (e_tl + e_bl)) / (4 * px) if px > 0 else 0.0
    grad_y = ((e_bl + e_br) - (e_tl + e_tr)) / (4 * py) if py > 0 else 0.0
    mag = math.hypot(grad_x, grad_y)
    direction = (math.degrees(math.atan2(grad_y, grad_x)) % 180 + 180) % 180 if mag > 1e-9 else 0.0
    angle = None
    if pixel_pitch_um > 0 and focal_length_mm > 0 and aperture_mm > 0:
        angle = math.degrees(math.atan(focal_length_mm / aperture_mm * mag))
    return {"direction_deg": direction, "angle_deg": angle}


def tilt_angle_lines(tilt: dict, w: int, h: int, pixel_pitch_um: float, focal_length_mm: float,
                     aperture_mm: float) -> list[str]:
    """The tilt angle estimate per quadrant, as console lines."""
    f_ratio = focal_length_mm / aperture_mm
    center = tilt["m_inner"]
    quadrants = [("Top-left", tilt["m1"]), ("Top-right", tilt["m2"]),
                 ("Bottom-left", tilt["m3"]), ("Bottom-right", tilt["m4"])]
    wmm, hmm = w * pixel_pitch_um / 1000, h * pixel_pitch_um / 1000
    dist = math.sqrt((wmm / 4) ** 2 + (hmm / 4) ** 2)
    lines = [f"--- Tilt angle estimate (f/{f_ratio:.2f}, {pixel_pitch_um:.2f} µm/px, reference: sensor center, "
             f"FWHM={center:.2f} px) ---"]
    for name, fwhm in quadrants:
        if fwhm <= center:
            lines.append(f"{name}: no excess relative to sensor center (FWHM {fwhm:.2f} <= {center:.2f} px).")
            continue
        excess = math.sqrt(fwhm * fwhm - center * center)
        excess_um = excess * pixel_pitch_um
        defocus = f_ratio * excess_um
        angle = math.degrees(math.atan(defocus / 1000 / dist)) if dist > 0 else 0.0
        lines.append(f"{name}: FWHM={fwhm:.2f} px, excess={excess:.2f} px ({excess_um:.1f} µm), "
                     f"defocus≈{defocus:.1f} µm, distance={dist:.2f} mm -> tilt angle≈{angle:.3f}°")
    lines.append("--- end (rough estimate - single frame, no calibration) ---")
    return lines


def tilt_plane(tilt: dict, sensor_w: int, sensor_h: int, pixel_pitch_um: float, focal_length_mm: float,
               aperture_mm: float) -> dict:
    """The plane z = gx x + gy y fitted to the four signed dz values through
    the sensor center (z(0, 0) = 0), for the pseudo-3D plot: the quadrants
    (name, ux, uy, x_mm, y_mm, plane_z_um, angle_deg), the axis end points,
    its angle and direction, and the reference values."""
    f_ratio = focal_length_mm / aperture_mm
    center = tilt["m_inner"]
    wmm, hmm = sensor_w * pixel_pitch_um / 1000, sensor_h * pixel_pitch_um / 1000
    corner_dist = math.sqrt((wmm / 4) ** 2 + (hmm / 4) ** 2)
    quadrants = [
        {"name": "TL", "fwhm": tilt["m1"], "ux": -1, "uy": -1},
        {"name": "TR", "fwhm": tilt["m2"], "ux": 1, "uy": -1},
        {"name": "BL", "fwhm": tilt["m3"], "ux": -1, "uy": 1},
        {"name": "BR", "fwhm": tilt["m4"], "ux": 1, "uy": 1},
    ]
    for q in quadrants:
        q["dz_um"] = signed_defocus_um(q["fwhm"], center, f_ratio, pixel_pitch_um)
        q["x_mm"] = q["ux"] * wmm / 4
        q["y_mm"] = q["uy"] * hmm / 4
    px, py = wmm / 4, hmm / 4
    gx = ((quadrants[1]["dz_um"] + quadrants[3]["dz_um"]) - (quadrants[0]["dz_um"] + quadrants[2]["dz_um"])) / (4 * px) \
        if px > 0 else 0.0
    gy = ((quadrants[2]["dz_um"] + quadrants[3]["dz_um"]) - (quadrants[0]["dz_um"] + quadrants[1]["dz_um"])) / (4 * py) \
        if py > 0 else 0.0
    mag = math.hypot(gx, gy)
    axis_angle = math.degrees(math.atan(mag / 1000))
    axis_dir = (math.degrees(math.atan2(gy, gx)) % 180 + 180) % 180 if mag > 1e-9 else 0.0
    max_dz = 0.0
    for q in quadrants:
        q["plane_z_um"] = gx * q["x_mm"] + gy * q["y_mm"]
        q["angle_deg"] = math.degrees(math.atan(abs(q["plane_z_um"]) / 1000 / corner_dist)) if corner_dist > 0 else 0.0
        max_dz = max(max_dz, abs(q["plane_z_um"]))
    p1 = p2 = (0.0, 0.0, 0.0)
    if mag > 1e-9:
        dx, dy = gx / mag, gy / mag
        tx = (wmm / 2) / abs(dx) if abs(dx) > 1e-9 else math.inf
        ty = (hmm / 2) / abs(dy) if abs(dy) > 1e-9 else math.inf
        t = min(tx, ty)
        p1 = (t * dx, t * dy, t * mag)
        p2 = (-t * dx, -t * dy, -t * mag)
    max_dz = max(max_dz, abs(p1[2]), abs(p2[2]))
    if max_dz <= 0:
        max_dz = 1.0
    return {"quadrants": quadrants, "axis_p1": p1, "axis_p2": p2, "axis_angle_deg": axis_angle,
            "axis_direction_deg": axis_dir, "max_dz_um": max_dz, "f_ratio": f_ratio,
            "pixel_pitch_um": pixel_pitch_um, "center_fwhm": center, "width_mm": wmm, "height_mm": hmm}
