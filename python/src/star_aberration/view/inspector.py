"""The inspector beside the map: what the image is (size, camera, optics,
mount, guiding, the common tracking error) and, for the map shown, its
layers and its evaluation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from .binding import Binder
from .widgets import (FwhmLegend, check, dspin, field_label, field_row, group, indent_row, radio, rich_label, spin,
                      tip)

CALC_HINT = "<i>Run Analysis › Calculate to evaluate the image.</i>"


class Inspector(QScrollArea):
    MAP_KEYS = ["size", "shape", "coma", "tilt"]

    def __init__(self, binder: Binder):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        b = self.binder = binder
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)

        self.image_label = rich_label("<i>No image open - File › Open Image…</i>")
        self.tracking_label = tip(rich_label(""), "trackingInfoLabel")
        lay.addWidget(group("Image", self.image_label, self.tracking_label))

        self.pages = QStackedWidget()
        self.pages.addWidget(self._size(b))
        self.pages.addWidget(self._shape(b))
        self.pages.addWidget(self._coma(b))
        self.pages.addWidget(self._tilt(b))
        lay.addWidget(self.pages)
        lay.addStretch(1)
        self.setWidget(body)
        self.setMinimumWidth(380)

    def show_map(self, key: str) -> None:
        self.pages.setCurrentIndex(self.MAP_KEYS.index(key))

    def set_evaluations(self, html_by_map: dict[str, str] | None) -> None:
        for key, lab in self.eval_labels.items():
            lab.setText((html_by_map or {}).get(key) or CALC_HINT)

    # ------------------------------------------------------------------
    def _size(self, b: Binder) -> QWidget:
        g = QButtonGroup(self)
        rows = []
        for i, key in enumerate(("fwhmPxRadio", "fwhmArcsecRadio", "fwhmRatioRadio")):
            r = radio(key)
            g.addButton(r, i)
            rows.append(indent_row(r))
        b.group(g, "fwhm_cell_value")
        self.eval_labels = {"size": rich_label(CALC_HINT)}
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(group("Star size: each cell shows", *rows,
                            indent_row(b.check(check("fwhmGridDetailsCheck"), "fwhm_grid_details"))))
        lay.addWidget(group("Legend", FwhmLegend()))
        lay.addWidget(group("Evaluation", self.eval_labels["size"]))
        return w

    def _shape(self, b: Binder) -> QWidget:
        self.streamlines = b.check(check("streamlinesCheck"), "show_streamlines")
        self.streamline_dep = [b.number(dspin("streamlineRadiusSpin", 2, 50, 1, 0.5), "streamline_radius_percent"),
                               b.check(check("shapeSignificantOnlyCheck"), "shape_significant_only")]
        self.model = b.check(check("shapeShowModelCheck"), "shape_show_model")
        self.model_dep = b.check(check("shapeModelSignificantOnlyCheck"), "shape_model_significant_only")
        self.heatmap = b.check(check("orientationHeatmapCheck"), "show_orientation_heatmap")
        self.heatmap_dep = b.number(spin("orientationHeatmapDegreeSpin", 1, 4), "orientation_heatmap_degree")
        self.eval_labels["shape"] = rich_label(CALC_HINT)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(group("Star ellipses",
                            indent_row(b.check(check("showEllipsesCheck"), "hide_ellipses", invert=True)),
                            field_row(field_label("Vector scale:"), b.number(dspin("scaleSpin", 0.1, 10, 2, 0.1), "vector_scale")),
                            indent_row(b.check(check("shapeShowCellsCheck"), "shape_show_cells"))))
        lay.addWidget(group("Streamlines", indent_row(self.streamlines),
                            field_row(field_label("Smoothing radius (%):"), self.streamline_dep[0]),
                            indent_row(self.streamline_dep[1])))
        lay.addWidget(group("Optics model", indent_row(self.model), indent_row(self.model_dep),
                            indent_row(b.check(check("shapeShowMatchCheck"), "shape_show_match")),
                            indent_row(b.check(check("shapeShowDefectsCheck"), "shape_show_defects"))))
        lay.addWidget(group("Orientation heatmap", indent_row(self.heatmap),
                            field_row(field_label("Fit degree:"), self.heatmap_dep)))
        lay.addWidget(group("Evaluation", self.eval_labels["shape"]))
        return w

    def _coma(self, b: Binder) -> QWidget:
        self.asym = b.check(check("measureAsymmetryCheck"), "measure_asymmetry")
        self.coma_lines = b.check(check("comaStreamlinesCheck"), "show_coma_streamlines")
        self.coma_lines_dep = [b.number(dspin("comaStreamlineRadiusSpin", 5, 60, 1, 0.5), "coma_streamline_radius_percent"),
                               b.number(dspin("comaStreamlineMinSpin", 0, 90, 0, 1), "coma_streamline_min_percent")]
        self.coma_arrows = b.check(check("comaArrowsCheck"), "show_coma_arrows")
        self.star_arrows = b.check(check("starAsymArrowsCheck"), "show_star_asym_arrows")
        self.star_arrows_dep = [b.number(dspin("starAsymMinSpin", 0, 10, 1, 0.5), "star_asym_min_sigma"),
                                b.check(check("starAsymColorCheck"), "star_asym_color_by_strength")]
        self.eval_labels["coma"] = rich_label(CALC_HINT)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(group("Coma field", indent_row(self.asym), indent_row(self.coma_lines),
                            field_row(field_label("Smoothing radius (%):"), self.coma_lines_dep[0]),
                            field_row(field_label("Stop below (%):"), self.coma_lines_dep[1]),
                            indent_row(self.coma_arrows)))
        lay.addWidget(group("Per-star arrows", indent_row(self.star_arrows),
                            field_row(field_label("Min. strength (sigma):"), self.star_arrows_dep[0]),
                            indent_row(self.star_arrows_dep[1])))
        lay.addWidget(group("Evaluation", self.eval_labels["coma"]))
        return w

    def _tilt(self, b: Binder) -> QWidget:
        self.eval_labels["tilt"] = rich_label(CALC_HINT)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(group("Tilt", indent_row(b.check(check("tiltAxisCheck"), "show_tilt_axis"))))
        lay.addWidget(group("Evaluation", self.eval_labels["tilt"]))
        return w

    def update_enabled(self, s) -> None:
        for w in self.streamline_dep:
            w.setEnabled(s.show_streamlines)
        self.model_dep.setEnabled(s.shape_show_model)
        self.heatmap_dep.setEnabled(s.show_orientation_heatmap)
        asym = s.measure_asymmetry
        for w in (self.coma_lines, self.coma_arrows, self.star_arrows):
            w.setEnabled(asym)
        for w in self.coma_lines_dep:
            w.setEnabled(asym and s.show_coma_streamlines)
        for w in self.star_arrows_dep:
            w.setEnabled(asym and s.show_star_asym_arrows)
