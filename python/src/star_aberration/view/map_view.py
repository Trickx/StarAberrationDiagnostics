"""The map viewer (pyqtgraph): the rendered map as an image item with
smooth zoom (wheel) and pan (drag), y pointing down as in the image,
a caption, and a readout of the position under the mouse with the values of
the nearest star."""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from scipy.spatial import cKDTree

from ..render.paint import rgb_from_image


class MapView(QWidget):
    hovered = Signal(str)

    def __init__(self):
        super().__init__()
        self.glw = pg.GraphicsLayoutWidget()
        self.vb = self.glw.addViewBox()
        self.vb.setAspectLocked(True)
        self.vb.invertY(True)
        self.vb.setMenuEnabled(False)
        self.img = pg.ImageItem()
        self.vb.addItem(self.img)
        self.marker = pg.ScatterPlotItem(size=18, pen=pg.mkPen("#ffd84a", width=2), brush=None, symbol="o")
        self.vb.addItem(self.marker)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.glw)
        self.caption = QLabel(self)
        self.caption.setStyleSheet("QLabel { color: white; background: rgba(0,0,0,150); padding: 4px 10px; "
                                   "border-radius: 8px; }")
        self.caption.move(12, 12)
        self.readout = QLabel(self)
        self.readout.setStyleSheet("QLabel { color: white; background: rgba(0,0,0,150); padding: 4px 10px; "
                                   "border-radius: 8px; font-family: Menlo, monospace; }")
        self.readout.hide()
        self.size: tuple[int, int] | None = None
        self.tree: cKDTree | None = None
        self.stars = None
        self.glw.scene().sigMouseMoved.connect(self._moved)

    def set_image(self, image: QImage | None, caption: str = "") -> None:
        if image is None or image.isNull():
            self.img.clear()
            self.size = None
        else:
            new = (image.width(), image.height())
            self.img.setImage(rgb_from_image(image), autoLevels=False, levels=(0, 255))
            if new != self.size:
                self.size = new
                self.fit()
        self.caption.setText(caption)
        self.caption.adjustSize()
        self.caption.setVisible(bool(caption))

    def set_stars(self, stars) -> None:
        """The stars whose values the readout shows (or None)."""
        self.stars = stars
        self.tree = cKDTree(np.array([(s.x, s.y) for s in stars])) if stars else None
        self.marker.clear()

    def fit(self) -> None:
        if self.size:
            self.vb.setRange(xRange=(0, self.size[0]), yRange=(0, self.size[1]), padding=0.01)

    def zoom(self, factor: float) -> None:
        self.vb.scaleBy((1 / factor, 1 / factor))

    def mouseDoubleClickEvent(self, e) -> None:
        self.fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.readout.move(12, self.height() - self.readout.height() - 12)

    def _moved(self, pos) -> None:
        if not self.size or not self.vb.sceneBoundingRect().contains(pos):
            self.readout.hide()
            self.marker.clear()
            return
        p = self.vb.mapSceneToView(pos)
        x, y = p.x(), p.y()
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            self.readout.hide()
            self.marker.clear()
            return
        text = f"x {x:7.1f}   y {y:7.1f}"
        if self.tree is not None:
            d, i = self.tree.query((x, y))
            px_per_screen = self.vb.viewPixelSize()[0]
            if d < max(15.0, 25 * px_per_screen):
                s = self.stars[i]
                eps = 1 - math.sqrt(max(0.0, 1 - s.eccentricity ** 2))
                text += (f"   │  star FWHM {s.fwhm:.2f} px  ε {eps:.3f}  axis {(-s.rotation) % 180:.0f}°")
                if s.m3_x is not None:
                    text += f"  |m3| {math.hypot(s.m3_x, s.m3_y):.2f}"
                self.marker.setData([s.x], [s.y])
            else:
                self.marker.clear()
        self.readout.setText(text)
        self.readout.adjustSize()
        self.readout.move(12, self.height() - self.readout.height() - 12)
        self.readout.show()
        self.hovered.emit(text)

    def leaveEvent(self, e) -> None:
        self.readout.hide()
        self.marker.clear()
        super().leaveEvent(e)
