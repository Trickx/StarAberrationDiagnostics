"""Drawing helpers on top of QPainter, in the terms of the original script:
ARGB colors as integers, rectangles and ellipses by their corners, text at
its baseline."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF

FONT_FAMILY = "Helvetica"


def qcolor(argb: int) -> QColor:
    return QColor((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF, (argb >> 24) & 0xFF)


def image_from_rgb(rgb: np.ndarray) -> QImage:
    """A QImage (RGB32) holding a copy of uint8 RGB pixels (h, w, 3)."""
    h, w = rgb.shape[:2]
    argb = np.empty((h, w, 4), dtype=np.uint8)
    argb[..., 0] = rgb[..., 2]
    argb[..., 1] = rgb[..., 1]
    argb[..., 2] = rgb[..., 0]
    argb[..., 3] = 255
    img = QImage(argb.data, w, h, 4 * w, QImage.Format.Format_RGB32)
    return img.copy()


def rgb_from_image(img: QImage) -> np.ndarray:
    """uint8 RGB (h, w, 3) of a QImage."""
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.bytesPerLine() * h).reshape(h, img.bytesPerLine())
    arr = arr[:, :4 * w].reshape(h, w, 4)
    return np.ascontiguousarray(arr[..., [2, 1, 0]])


def font(size: float, bold: bool = False) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(1, int(round(size))))
    f.setBold(bold)
    return f


class Canvas:
    """A QPainter on a QImage with the drawing calls of the script."""

    def __init__(self, image: QImage):
        self.image = image
        self.p = QPainter(image)
        self.p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._font = font(14)
        self.p.setFont(self._font)

    def end(self) -> None:
        self.p.end()

    # ---- state
    def pen(self, argb: int, width: float = 1.0) -> None:
        pen = QPen(qcolor(argb))
        pen.setWidthF(width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.p.setPen(pen)

    def set_font(self, f: QFont) -> None:
        self._font = f
        self.p.setFont(f)

    def text_width(self, text: str) -> float:
        return QFontMetricsF(self._font).horizontalAdvance(text)

    # ---- shapes
    def line(self, x0, y0, x1, y1) -> None:
        self.p.drawLine(QPointF(x0, y0), QPointF(x1, y1))

    def polyline(self, pts) -> None:
        if len(pts) > 1:
            self.p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))

    def rect(self, x0, y0, x1, y1) -> None:
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        self.p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))

    def ellipse(self, x0, y0, x1, y1) -> None:
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        self.p.drawEllipse(QRectF(x0, y0, x1 - x0, y1 - y0))

    def fill_rect(self, x0, y0, x1, y1, argb: int, style=Qt.BrushStyle.SolidPattern) -> None:
        self.p.fillRect(QRectF(x0, y0, x1 - x0, y1 - y0), QBrush(qcolor(argb), style))

    def fill_ellipse(self, x0, y0, x1, y1, argb: int) -> None:
        self.p.save()
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(QBrush(qcolor(argb)))
        self.p.drawEllipse(QRectF(x0, y0, x1 - x0, y1 - y0))
        self.p.restore()

    def fill_polygon(self, pts, argb: int) -> None:
        self.p.save()
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(QBrush(qcolor(argb)))
        self.p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        self.p.restore()

    def text(self, x, y, text: str) -> None:
        self.p.drawText(QPointF(x, y), text)

    def text_with_halo(self, x, y, text: str, argb: int, font_size: float) -> None:
        """Text with a dark halo (offset copies) so that it stays readable on
        any background."""
        offset = max(2, round(font_size * 0.06))
        self.pen(0xE0000000, 1)
        for dy in (-offset, 0, offset):
            for dx in (-offset, 0, offset):
                if dx or dy:
                    self.p.drawText(QPointF(x + dx, y + dy), text)
        self.pen(argb, 1)
        self.p.drawText(QPointF(x, y), text)

    def oriented_ellipse(self, cx, cy, a, b, theta_deg) -> None:
        """An ellipse whose major semi-axis a points along (cos t, sin t) in
        image coordinates (y down)."""
        n = max(12, min(48, round(a)))
        t = math.radians(theta_deg)
        ct, st = math.cos(t), math.sin(t)
        pts = []
        for i in range(n + 1):
            phi = 2 * math.pi * i / n
            u, v = a * math.cos(phi), b * math.sin(phi)
            pts.append((cx + u * ct - v * st, cy + u * st + v * ct))
        self.polyline(pts)

    def line_runs(self, lines, keep) -> None:
        """The parts of the lines whose segments satisfy keep(pts, j), each
        run as one polyline."""
        for pts in lines:
            run = []
            for j in range(1, len(pts)):
                if keep(pts, j):
                    if not run:
                        run.append(pts[j - 1])
                    run.append(pts[j])
                elif run:
                    self.polyline(run)
                    run = []
            if len(run) > 1:
                self.polyline(run)

    def arrow(self, x0, y0, x1, y1, head: float) -> None:
        ang = math.atan2(y1 - y0, x1 - x0)
        self.line(x0, y0, x1, y1)
        self.line(x1, y1, x1 - head * math.cos(ang - 0.45), y1 - head * math.sin(ang - 0.45))
        self.line(x1, y1, x1 - head * math.cos(ang + 0.45), y1 - head * math.sin(ang + 0.45))


def ecc_color(ecc: float, ecc_max: float) -> int:
    """Blue (round) -> red (strongly elongated)."""
    t = max(0.0, min(1.0, ecc / ecc_max))
    r = round(255 * t)
    b = round(255 * (1 - t))
    g = round(64 * (1 - abs(t - 0.5) * 2))
    return 0xFF000000 | (r << 16) | (g << 8) | b


def opaque_over(argb: int, bg: int) -> int:
    """An ARGB color laid opaquely over the background (for legends)."""
    a = ((argb >> 24) & 0xFF) / 255

    def mix(shift):
        return round(((argb >> shift) & 0xFF) * a + ((bg >> shift) & 0xFF) * (1 - a))
    return 0xFF000000 | (mix(16) << 16) | (mix(8) << 8) | mix(0)
