"""Pseudo-3D comparison plot: a flat sensor plane against the tilted plane
fitted to the quadrant dz values (isometric projection).

dz follows only from the blur (a magnitude): whether a quadrant is nearer
or farther than the sensor center is unknown from one frame, so both
mirror-image planes are drawn (salmon: the computed sign, green: mirrored).
The tilt axis passes through the center at z = 0 for both.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QImage

from ..model.tilt import tilt_plane
from .paint import Canvas, font, image_from_rgb


def render_3d_tilt_plot(tilt: dict, sensor_w: int, sensor_h: int, pixel_pitch_um: float, focal_length_mm: float,
                        aperture_mm: float) -> QImage:
    pl = tilt_plane(tilt, sensor_w, sensor_h, pixel_pitch_um, focal_length_mm, aperture_mm)
    cw, ch = 1000, 750
    margin = 12
    img = image_from_rgb(np.full((ch, cw, 3), 0x1A, dtype=np.uint8))
    g = Canvas(img)
    wmm, hmm = pl["width_mm"], pl["height_mm"]
    origin_x, origin_y = cw / 2, ch * 0.58
    scale = (cw * 0.32) / (wmm / 2)
    cos30, sin30 = math.cos(math.radians(30)), math.sin(math.radians(30))
    zexag = (min(wmm, hmm) * scale * 0.22) / pl["max_dz_um"]

    def project(x, y, z):
        return origin_x + (x - y) * cos30 * scale, origin_y + (x + y) * sin30 * scale - z * zexag

    def text_clamped(x, y, text):
        tw = g.text_width(text)
        g.text(max(margin, min(x, cw - margin - tw)), y, text)

    def wrap(text, max_width):
        lines, cur = [], ""
        for word in text.split(" "):
            cand = f"{cur} {word}" if cur else word
            if cur and g.text_width(cand) > max_width:
                lines.append(cur)
                cur = word
            else:
                cur = cand
        if cur:
            lines.append(cur)
        return lines

    def block(x, y, text, lh):
        lines = wrap(text, cw - 2 * margin)
        for i, line in enumerate(lines):
            text_clamped(x, y + i * lh, line)
        return y + len(lines) * lh

    q = pl["quadrants"]
    order = [0, 1, 3, 2]
    flat_c, col_a, col_b, axis_c, conn_c, legend_c = 0xFF808080, 0xFFFFCCB2, 0xFF9CFFA0, 0xFF66E0FF, 0xFF555555, 0xFFCCCCCC
    flat = [project(p["x_mm"], p["y_mm"], 0) for p in q]
    g.pen(flat_c, 1)
    for i in range(4):
        g.line(*flat[order[i]], *flat[order[(i + 1) % 4]])
    fc = project(0, 0, 0)
    g.ellipse(fc[0] - 3, fc[1] - 3, fc[0] + 3, fc[1] + 3)
    ta = [project(p["x_mm"], p["y_mm"], p["plane_z_um"]) for p in q]
    tb = [project(p["x_mm"], p["y_mm"], -p["plane_z_um"]) for p in q]
    for pts, col in ((ta, col_a), (tb, col_b)):
        g.pen(col, 3)
        for i in range(4):
            g.line(*pts[order[i]], *pts[order[(i + 1) % 4]])
    a1, a2 = project(*pl["axis_p1"]), project(*pl["axis_p2"])
    g.pen(axis_c, 3)
    g.line(*a1, *a2)
    g.set_font(font(16))
    for i, p in enumerate(q):
        g.pen(conn_c, 1)
        g.line(*flat[i], *ta[i])
        g.line(*flat[i], *tb[i])
        g.pen(col_a, 2)
        g.ellipse(ta[i][0] - 4, ta[i][1] - 4, ta[i][0] + 4, ta[i][1] + 4)
        g.pen(col_b, 2)
        g.ellipse(tb[i][0] - 4, tb[i][1] - 4, tb[i][0] + 4, tb[i][1] + 4)
        label = f"{p['name']}: ±{abs(p['plane_z_um']):.1f} µm ({p['angle_deg']:.3f}°)"
        lx = ta[i][0] - 8 - g.text_width(label) if p["ux"] > 0 else ta[i][0] + 8
        g.pen(col_a, 1)
        text_clamped(lx, ta[i][1] - 8, label)
    y = 30
    g.pen(legend_c, 1)
    g.set_font(font(18))
    y = block(20, y, "Gray = flat sensor plane", 24)
    y = block(20, y, "Salmon/green = 2 mirror-image tilt variants", 24)
    y = block(20, y, "Light blue = tilt axis", 24) + 6
    g.set_font(font(15))
    y = block(20, y, f"f/{pl['f_ratio']:.2f}, {pl['pixel_pitch_um']:.2f} µm/px, reference: sensor center "
                     f"(FWHM={pl['center_fwhm']:.2f} px, assumed in focus)", 20)
    g.pen(axis_c, 1)
    y = block(20, y, f"Tilt axis: {pl['axis_angle_deg']:.3f}° (direction {pl['axis_direction_deg']:.0f}° from the "
                     "horizontal image axis)", 20) + 4
    g.pen(legend_c, 1)
    block(20, y, "Note: Δz follows only from the blur (a magnitude) - whether a quadrant is closer to or farther from "
                 "the corrector is unknown without a focus sweep; both sign variants are equally plausible.", 20)
    g.end()
    return img


def render_mosaic(background: np.ndarray, tile: int = 512, sep: int = 4) -> QImage:
    """A 3 x 3 mosaic of the stretched image as AberrationInspector draws it:
    corners, edge centers and center, each cropped 1:1."""
    h, w = background.shape[:2]
    t = min(tile, w // 3, h // 3)
    out = np.full((3 * t + 2 * sep, 3 * t + 2 * sep, 3), 0x80, dtype=np.uint8)
    xs = [0, (w - t) // 2, w - t]
    ys = [0, (h - t) // 2, h - t]
    for r in range(3):
        for c in range(3):
            out[r * (t + sep):r * (t + sep) + t, c * (t + sep):c * (t + sep) + t] = \
                background[ys[r]:ys[r] + t, xs[c]:xs[c] + t]
    return image_from_rgb(out)
