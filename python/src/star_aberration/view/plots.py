"""Diagrams with pyqtgraph: the radial profiles of an image (every star as a
point, the ring medians as a line) and the values of a series per frame
over the night, colored by the side of the meridian flip."""

from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .theme import colors

SIDE_COLORS = {"West": "#5b8def", "East": "#f5a524", "Side A": "#5b8def", "Side B": "#f5a524", "?": "#9aa3b2"}


def _plot(glw: pg.GraphicsLayoutWidget, title: str, x: str, y: str, row: int, col: int) -> pg.PlotItem:
    p = glw.addPlot(row=row, col=col, title=f"<span style='font-size:11pt'>{title}</span>")
    p.showGrid(x=True, y=True, alpha=0.15)
    p.setLabel("bottom", x)
    p.setLabel("left", y)
    p.setMenuEnabled(False)
    for side in ("bottom", "left"):
        p.getAxis(side).enableAutoSIPrefix(False)
    return p


class ProfilesView(QWidget):
    """FWHM, elongation and asymmetry against r/r_max, and the FWHM
    histogram."""

    def __init__(self):
        super().__init__()
        self.glw = pg.GraphicsLayoutWidget()
        self.p_fwhm = _plot(self.glw, "FWHM across the field", "r / r_max", "FWHM (px)", 0, 0)
        self.p_eps = _plot(self.glw, "Elongation: + radial, − tangential", "r / r_max", "eps_rad", 0, 1)
        self.p_asym = _plot(self.glw, "Coma flare: + outward, − inward", "r / r_max", "asym_rad (m3)", 1, 0)
        self.p_hist = _plot(self.glw, "FWHM distribution", "FWHM (px)", "stars", 1, 1)
        self.p_eps.addLegend(offset=(10, 10))
        self.hint = QLabel("<i>Calculate an image to see its radial profiles.</i>")
        self.hint.setObjectName("hint")
        lay = QVBoxLayout(self)
        lay.addWidget(self.hint)
        lay.addWidget(self.glw, 1)

    def clear(self) -> None:
        for p in (self.p_fwhm, self.p_eps, self.p_asym, self.p_hist):
            p.clear()
        self.hint.show()

    def set_data(self, stars, w: float, h: float, profile: list[dict]) -> None:
        self.clear()
        if not stars:
            return
        self.hint.hide()
        c = colors()
        cx, cy = w / 2, h / 2
        r_max = math.hypot(cx, cy)
        x = np.array([s.x for s in stars])
        y = np.array([s.y for s in stars])
        r = np.hypot(x - cx, y - cy) / r_max
        phi = np.arctan2(y - cy, x - cx)
        fwhm = np.array([s.fwhm for s in stars])
        ecc = np.array([s.eccentricity for s in stars])
        eps = 1 - np.sqrt(np.clip(1 - ecc ** 2, 0, None))
        psi = -np.radians([s.rotation for s in stars])
        eps_rad = eps * np.cos(2 * (psi - phi))
        dot = pg.mkBrush(QColorA(c.muted, 110))
        line = pg.mkPen(c.accent, width=3)
        self.p_fwhm.plot(r, fwhm, pen=None, symbol="o", symbolSize=4, symbolBrush=dot, symbolPen=None)
        self.p_eps.plot(r, eps_rad, pen=None, symbol="o", symbolSize=4, symbolBrush=dot, symbolPen=None,
                        name="stars")
        self.p_eps.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(c.border, width=1)))
        rm = np.array([(p["r0"] + p["r1"]) / 2 for p in profile])
        if len(rm):
            self.p_fwhm.plot(rm, [p["fwhm"] for p in profile], pen=line, symbol="o", symbolBrush=c.accent)
            self.p_eps.plot(rm, [p["eps_rad"] for p in profile], pen=line, symbol="o", symbolBrush=c.accent,
                            name="ring median")
        m3 = [(rr, s.m3_x * math.cos(ph) + s.m3_y * math.sin(ph)) for s, rr, ph in zip(stars, r, phi)
              if s.m3_x is not None]
        if m3:
            a = np.array(m3)
            self.p_asym.plot(a[:, 0], a[:, 1], pen=None, symbol="o", symbolSize=4, symbolBrush=dot, symbolPen=None)
            self.p_asym.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen(c.border, width=1)))
            ra = [((p["r0"] + p["r1"]) / 2, p["asym_rad"]) for p in profile if p["asym_rad"] is not None]
            if ra:
                self.p_asym.plot([q[0] for q in ra], [q[1] for q in ra], pen=pg.mkPen("#f5a524", width=3),
                                 symbol="o", symbolBrush="#f5a524")
            lo, hi = np.percentile(a[:, 1], [2, 98])
            self.p_asym.setYRange(lo, hi)
        hist, edges = np.histogram(fwhm, bins=40, range=tuple(np.percentile(fwhm, [0.5, 99.5])))
        self.p_hist.plot(edges, hist, stepMode="center", fillLevel=0, brush=QColorA(c.accent, 120),
                         pen=pg.mkPen(c.accent, width=1.5))
        lo, hi = np.percentile(eps_rad, [1, 99])
        self.p_eps.setYRange(lo, hi)


def QColorA(hex_color: str, alpha: int):
    from PySide6.QtGui import QColor
    q = QColor(hex_color)
    q.setAlpha(alpha)
    return q


class SeriesPlots(QWidget):
    """The values of each frame against the time of the night, one color
    per side."""

    METRICS = [("fwhmCenter", "FWHM center", "px"), ("fwhmEdge", "FWHM edge", "px"),
               ("tilt", "Tilt (one-sided blur)", "%"), ("eRad", "Edge elongation eps_rad", ""),
               ("tracking", "Tracking elongation", ""), ("comaK", "Coma k", "")]

    def __init__(self):
        super().__init__()
        self.glw = pg.GraphicsLayoutWidget()
        self.plots = {}
        for i, (key, title, unit) in enumerate(self.METRICS):
            p = _plot(self.glw, title, "hours since the first frame", unit, i // 2, i % 2)
            self.plots[key] = p
        self.hint = QLabel("<i>Analyze a series to see its values over the night.</i>")
        self.legend = QLabel()
        self.legend.setObjectName("hint")
        self.hint.setObjectName("hint")
        lay = QVBoxLayout(self)
        lay.addWidget(self.hint)
        lay.addWidget(self.legend)
        lay.addWidget(self.glw, 1)
        self.glw.setMinimumHeight(520)

    def set_frames(self, frames, metric_value) -> None:
        """metric_value(key, frame) -> number or None."""
        for p in self.plots.values():
            p.clear()
        times = [f.time_ms for f in frames if f.metrics and f.time_ms is not None]
        if not times:
            self.hint.show()
            return
        self.hint.hide()
        sides = sorted({f.side for f in frames if f.metrics})
        self.legend.setText("&nbsp;&nbsp;".join(f"<span style='color:{SIDE_COLORS.get(x, '#9aa3b2')}'>●</span> {x}"
                                                  for x in sides))
        t0 = min(times)
        for key, p in self.plots.items():
            by_side: dict[str, list] = {}
            for f in frames:
                if not f.metrics or f.time_ms is None:
                    continue
                v = metric_value(key, f)
                if v is not None and math.isfinite(v):
                    by_side.setdefault(f.side, []).append(((f.time_ms - t0) / 3600000, v))
            for side, pts in sorted(by_side.items()):
                pts.sort()
                col = SIDE_COLORS.get(side, "#9aa3b2")
                p.plot([q[0] for q in pts], [q[1] for q in pts], pen=pg.mkPen(col, width=1.5), symbol="o",
                       symbolSize=8, symbolBrush=col, symbolPen=None)
