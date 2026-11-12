"""The Preferences dialog: what is measured and how (star detection, PSF
fit, debayering, tracking), the optics, mount and guiding, what is written,
and the AI review. It edits a copy of the settings; OK hands it back."""

from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QLineEdit, QComboBox, QTabWidget, QVBoxLayout,
                               QWidget)

from ..ai import AI_DETAIL_LEVELS
from ..model.assessment import OPTICS_TYPES
from ..model.optics import GUIDING_TYPES, MOUNT_TYPES
from ..model.settings import BAYER_PATTERNS, Settings
from .binding import Binder
from .widgets import check, dspin, field_label, field_row, group, indent_row, page, rich_label, spin, tip


class PreferencesDialog(QDialog):
    def __init__(self, parent, settings: Settings, api_key: str, key_from_env: bool, header_info: str):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.settings = settings.copy()
        self.binder = b = Binder(lambda: self.settings, self._changed)
        tabs = QTabWidget()

        # ---- Measurement
        self.sd_custom = b.check(check("sdCustomCheck", "Custom star detector parameters"), "sd_custom")
        self.sd_widgets = [
            b.number(spin("sdLayersSpin", 1, 8), "sd_structure_layers"),
            b.number(dspin("sdSensitivitySpin", 0, 1, 2, 0.05), "sd_sensitivity"),
            b.number(dspin("sdPeakResponseSpin", 0, 1, 2, 0.05), "sd_peak_response"),
            b.number(dspin("sdMaxDistortionSpin", 0, 1, 2, 0.05), "sd_max_distortion"),
            b.check(check("sdClusteredCheck"), "sd_allow_clustered"),
        ]
        self.debayer = b.check(check("debayerCheck"), "debayer")
        self.bayer = b.choice(tip(QComboBox(), "bayerPatternCombo"), "bayer_pattern", BAYER_PATTERNS)
        self.bayer.addItems(BAYER_PATTERNS)
        tabs.addTab(page(
            group("Star detection",
                  field_row(field_label("Threshold (sigma):"), b.number(dspin("thresholdSpin", 0.5, 10, 2, 0.1), "threshold")),
                  field_row(field_label("Max. stars (0 = all):"), b.number(spin("maxCandSpin", 0, 50000), "max_candidates")),
                  indent_row(self.sd_custom),
                  field_row(field_label("Structure layers:"), self.sd_widgets[0]),
                  field_row(field_label("Sensitivity:"), self.sd_widgets[1]),
                  field_row(field_label("Peak response:"), self.sd_widgets[2]),
                  field_row(field_label("Max. distortion:"), self.sd_widgets[3]),
                  indent_row(self.sd_widgets[4])),
            group("PSF fit",
                  indent_row(b.check(check("moffatCheck"), "use_moffat")),
                  field_row(field_label("Search radius (px):"), b.number(spin("radiusSpin", 3, 50), "radius")),
                  field_row(field_label("MAD outlier factor:"), b.number(dspin("madSpin", 0, 20, 1, 0.5), "mad_outlier_factor"))),
            group("Debayer", indent_row(self.debayer), field_row(field_label("Bayer pattern:"), self.bayer)),
            group("Common tracking error", indent_row(b.check(check("subtractTrackingCheck"), "subtract_tracking")))),
            "Measurement")

        # ---- Optics
        self.from_header = b.check(check("opticsFromHeaderCheck"), "optics_from_header")
        self.optics_fields = [
            b.number(dspin("pixelPitchSpin", 0, 50, 2), "pixel_pitch_um"),
            b.number(dspin("focalLengthSpin", 0, 10000, 0), "focal_length_mm"),
            b.number(dspin("apertureSpin", 0, 2000, 0), "aperture_mm"),
        ]
        optics_type = b.index(tip(QComboBox(), "opticsTypeCombo"), "optics_type")
        optics_type.addItems(OPTICS_TYPES)
        self.mount = b.index(tip(QComboBox(), "mountCombo"), "mount_type")
        self.mount.addItems(MOUNT_TYPES)
        guiding = b.index(tip(QComboBox(), "guidingCombo"), "guiding_type")
        guiding.addItems(GUIDING_TYPES)
        tabs.addTab(page(
            group("Optical system", field_row(field_label("Optics type:"), optics_type, stretch_last=True)),
            group("Optics",
                  indent_row(self.from_header),
                  field_row(field_label("Pixel pitch (µm):"), self.optics_fields[0]),
                  field_row(field_label("Focal length (mm):"), self.optics_fields[1]),
                  field_row(field_label("Aperture (mm):"), self.optics_fields[2])),
            group("Mount and guiding",
                  field_row(field_label("Mount:"), self.mount),
                  field_row(field_label("Guiding:"), guiding)),
            group("From the current image", rich_label(header_info or "<i>No image open.</i>"))),
            "Optics")

        # ---- Output
        tabs.addTab(page(
            group("Export maps", indent_row(b.check(check("tiltPlot3DCheck"), "show_3d_tilt_plot")),
                  indent_row(b.check(check("exportCheck"), "do_export"))),
            group("Series", indent_row(b.check(check("seriesShowMapsCheck"), "series_show_maps")),
                  indent_row(b.check(check("seriesSaveMapsCheck"), "series_save_maps")),
                  indent_row(b.check(check("seriesSaveMosaicCheck"), "series_save_mosaic")))),
            "Output")

        # ---- AI review
        self.key_edit = tip(QLineEdit(), "aiKeyEdit")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setText("" if key_from_env else api_key)
        self.key_edit.setEnabled(not key_from_env)
        if key_from_env:
            self.key_edit.setPlaceholderText("from the environment variable ANTHROPIC_API_KEY")
        detail = b.index(tip(QComboBox(), "aiDetailCombo"), "ai_detail")
        detail.addItems(AI_DETAIL_LEVELS)
        tabs.addTab(page(group("AI review (Claude)",
                               field_row(field_label("API key:"), self.key_edit, stretch_last=True),
                               field_row(field_label("Answer language:"),
                                         b.text(tip(QLineEdit(), "aiLanguageEdit"), "ai_language")),
                               field_row(field_label("Explanation:"), detail),
                               indent_row(b.check(check("aiSendMapCheck"), "ai_send_map")),
                               indent_row(b.check(check("aiSaveLogCheck"), "ai_save_log")))),
                     "AI review")

        b.load()
        self._changed("")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self.restore_defaults)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)
        self.resize(560, 620)

    def _changed(self, _field: str) -> None:
        for w in self.sd_widgets:
            w.setEnabled(self.settings.sd_custom)
        self.bayer.setEnabled(self.settings.debayer)
        header = self.settings.optics_from_header
        for w in self.optics_fields:
            w.setEnabled(not header)
        self.mount.setEnabled(not header)

    def restore_defaults(self) -> None:
        keep = {k: getattr(self.settings, k) for k in ("ai_language",)}
        self.settings = Settings(**keep)
        self.binder.load()
        self._changed("")

    @property
    def api_key(self) -> str:
        return self.key_edit.text().strip()
