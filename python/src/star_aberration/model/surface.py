"""Radial/tangential decomposition per ring and the FWHM^2 surface fit.

Rings around the image center:
    eps_rad = eps cos 2(psi - phi)   phi = azimuth of the star
    > 0: major axis radial, < 0: tangential (concentric)
plus the radial component of the asymmetry (third moment):
    > 0: flare points outward (under-corrected coma), < 0: inward.

FWHM^2 surface over all stars:
    FWHM^2 = c0 + c1 X + c2 Y + c3 (X^2 + Y^2),  X, Y = (x - cx, y - cy) / r_max
c3 is the rotationally symmetric part (field curvature), c1/c2 the
one-sided part (tilt). Physical conversion: dz = f-number x excess blur, a
rough single-frame estimate (the sign is not determinable).
"""

from __future__ import annotations

import math

from .log import Log
from .shape import ellipticity_from_ecc, star_image_angle
from .star import Star
from .stats import median_of, solve_least_squares_clipped


def analyze_radial_tangential(stars: list[Star], w: float, h: float, have_asymmetry: bool, log: Log) -> dict:
    """Writes the ring table and the verdict; returns {e_rad, outer_asym,
    defocus_suspect, profile}."""
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    nr = 6
    rings = [{"fwhm": [], "eps": [], "eps_rad": [], "m3_rad": [], "align_sum": 0.0, "align_w": 0.0}
             for _ in range(nr)]
    for s in stars:
        dx, dy = s.x - cx, s.y - cy
        r = math.hypot(dx, dy) / r_max
        k = min(nr - 1, int(math.floor(r * nr)))
        phi = math.atan2(dy, dx)
        eps = ellipticity_from_ecc(s.eccentricity)
        rg = rings[k]
        rg["fwhm"].append(0.5 * (s.fwhm_x + s.fwhm_y))
        rg["eps"].append(eps)
        rg["eps_rad"].append(eps * math.cos(2 * (star_image_angle(s) - phi)))
        if have_asymmetry and s.m3_x is not None:
            rg["m3_rad"].append(s.m3_x * math.cos(phi) + s.m3_y * math.sin(phi))
            wgt = eps * math.hypot(s.m3_x, s.m3_y)
            rg["align_sum"] += wgt * math.cos(2 * (math.atan2(s.m3_y, s.m3_x) - star_image_angle(s)))
            rg["align_w"] += wgt

    profile = []
    for k, rg in enumerate(rings):
        if len(rg["eps"]) < 5:
            continue
        profile.append({"r0": k / nr, "r1": (k + 1) / nr, "n": len(rg["eps"]), "fwhm": median_of(rg["fwhm"]),
                        "ellipticity": median_of(rg["eps"]), "eps_rad": median_of(rg["eps_rad"]),
                        "asym_rad": median_of(rg["m3_rad"]) if len(rg["m3_rad"]) >= 5 else None,
                        "align": rg["align_sum"] / rg["align_w"] if rg["align_w"] > 0 else None})

    log.note("--- Radial / tangential decomposition (rings around the image center) ---")
    log.write("r/rMax      n   FWHM[px]  ellipt.  eps_rad   asym_rad   align")
    log.write("            (eps_rad: + radial, - tangential/concentric; asym_rad: + flare outward, - inward;")
    log.write("             align: flare vs. ellipse axis, +1 parallel = coma shapes the star, -1 perpendicular)")
    for p in profile:
        asym = f"{p['asym_rad']:7.3f}" if p["asym_rad"] is not None else "   n/a"
        align = f"{p['align']:6.2f}" if p["align"] is not None else "  n/a"
        log.write(f"{p['r0']:.2f}-{p['r1']:.2f} {p['n']:5d}   {p['fwhm']:6.2f}    {p['ellipticity']:6.3f}   "
                  f"{p['eps_rad']:7.3f}   {asym}   {align}")

    outer_eps, outer_m3, inner_fwhm, outer_fwhm = [], [], [], []
    for k, rg in enumerate(rings):
        if k >= 4:
            outer_eps += rg["eps_rad"]
            outer_m3 += rg["m3_rad"]
            outer_fwhm += rg["fwhm"]
        elif k <= 1:
            inner_fwhm += rg["fwhm"]
    if len(outer_eps) < 10:
        log.write("Too few stars in the outer field for a verdict.")
        return {"e_rad": None, "outer_asym": None, "defocus_suspect": False, "profile": profile}
    e_rad = median_of(outer_eps)
    # 5% margin: on a flat field the two medians otherwise cross by chance.
    defocus = len(inner_fwhm) >= 5 and median_of(inner_fwhm) > 1.05 * median_of(outer_fwhm)
    if e_rad > 0.04:
        log.write(f"Outer field: stars elongated RADIALLY (eps_rad = {e_rad:.3f}, pattern \"B\"). With a coma "
                  "corrector/flattener this usually means under-correction - rule of thumb: INCREASE the "
                  "corrector-sensor distance.")
    elif e_rad < -0.04:
        log.write(f"Outer field: stars elongated TANGENTIALLY / concentric (eps_rad = {e_rad:.3f}, pattern \"A\"). "
                  "Rule of thumb: DECREASE the corrector-sensor distance - BUT the same pattern is produced by "
                  "defocus combined with field curvature/astigmatism.")
    else:
        log.write(f"Outer field: no significant radial/tangential pattern (eps_rad = {e_rad:.3f}).")
    outer_asym = None
    if len(outer_m3) >= 10:
        outer_asym = median_of(outer_m3)
        verdict = ("no clear comatic flare." if abs(outer_asym) < 0.1 else
                   "flare points OUTWARD (under-corrected coma)." if outer_asym > 0 else
                   "flare points INWARD (over-corrected coma).")
        log.write(f"Outer field asymmetry: asym_rad = {outer_asym:.3f} -> {verdict}")
    if defocus:
        log.warning("The center is SOFTER than the outer field - the frame is probably out of focus (or focused on "
                    "the field edge). Radial/tangential patterns are not reliable for judging corrector spacing on "
                    "such a frame; use a well-focused one.")
    return {"e_rad": e_rad, "outer_asym": outer_asym, "defocus_suspect": defocus, "profile": profile}


def fit_fwhm_surface(stars: list[Star], w: float, h: float, pixel_pitch_eff_um: float, focal_length_mm: float,
                     aperture_mm: float, log: Log) -> dict | None:
    cx, cy = w / 2, h / 2
    r_max = math.hypot(cx, cy)
    rows, zs = [], []
    for s in stars:
        x, y = (s.x - cx) / r_max, (s.y - cy) / r_max
        f = 0.5 * (s.fwhm_x + s.fwhm_y)
        rows.append([1, x, y, x * x + y * y])
        zs.append(f * f)
    log.note("--- FWHM² surface fit (tilt vs. field curvature) ---")
    res = solve_least_squares_clipped(rows, zs, 4) if len(rows) >= 20 else None
    if res is None or res["coeffs"] is None:
        log.warning("Too few stars / singular system - surface fit skipped.")
        return None
    c = res["coeffs"]
    physical = pixel_pitch_eff_um > 0 and focal_length_mm > 0 and aperture_mm > 0
    f_ratio = focal_length_mm / aperture_mm if physical else 0.0

    def signed_sqrt(v):
        return (-1 if v < 0 else 1) * math.sqrt(abs(v))

    f_center = math.sqrt(max(0.0, c[0]))
    f_edge = math.sqrt(max(0.0, c[0] + c[3]))
    log.write(f"FWHM center {f_center:.2f} px, FWHM at the field edge (rotationally symmetric part) {f_edge:.2f} px.")
    curv = signed_sqrt(c[3])
    sag = f_ratio * abs(curv) * pixel_pitch_eff_um if physical else None
    line = f"Symmetric off-axis blur at the edge: {curv:.2f} px"
    if physical:
        line += f" -> field curvature sag ≈ {sag:.0f} µm between center and corner (f/{f_ratio:.2f}; sign not determinable)"
    log.write(line + ".")
    if c[3] < 0:
        log.write("   (negative: the edge is SHARPER than the center - frame focused off-center or defocused with a "
                  "curved field)")
    g = math.hypot(c[1], c[2])
    dir_deg = (math.degrees(math.atan2(c[2], c[1])) + 360) % 360
    f_bad = math.sqrt(max(0.0, c[0] + g))
    f_good = math.sqrt(max(0.0, c[0] - g))
    dz = tilt = None
    line = (f"One-sided part (tilt): FWHM along the gradient {f_bad:.2f} px (edge at {dir_deg:.0f}°) vs. "
            f"{f_good:.2f} px (opposite edge)")
    if physical:
        dz = f_ratio * math.sqrt(g) * pixel_pitch_eff_um
        tilt = math.degrees(math.atan((dz / 1000) / (r_max * pixel_pitch_eff_um / 1000)))
        line += f" -> Δz ≈ ±{dz:.0f} µm at the edge, tilt ≈ {tilt:.3f}°"
    log.write(line + ".")
    log.write("   (direction in image coordinates: 0° = right, 90° = down; the softer side is at that angle)")
    x0 = y0 = None
    if c[3] > 0:
        x0 = cx - c[1] / (2 * c[3]) * r_max
        y0 = cy - c[2] / (2 * c[3]) * r_max
        log.write(f"Sharpest point of the fitted surface: ({x0:.0f}, {y0:.0f}) px, image center ({cx:.0f}, {cy:.0f}).")
    if not physical:
        log.write("Enter pixel pitch, focal length and aperture to get Δz in µm and the tilt angle.")
    return {"c": list(c), "x0": x0, "y0": y0, "f_center": f_center, "f_edge": f_edge, "sag_um": sag,
            "tilt_dir_deg": dir_deg, "f_bad": f_bad, "f_good": f_good, "dz_um": dz, "tilt_deg": tilt}
