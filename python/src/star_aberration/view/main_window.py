"""The main window: the preview (or the assessments, the tips, the series
hints and tabs) on the left, the settings in tabs on the right - Setup
(General, Star detection), Maps (Size, Shape, Coma, Tilt), Assessment
(with the AI review) and Series - the buttons and the progress below, the
console as a dock.

The window only builds the widgets and translates between them and the
Settings; everything that happens is up to the controller.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDockWidget, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QStatusBar, QTabWidget, QTextBrowser, QTreeWidget, QVBoxLayout,
                               QWidget)

from .. import TITLE, VERSION
from ..ai import AI_DETAIL_LEVELS
from ..model.assessment import OPTICS_TYPES
from ..model.optics import GUIDING_TYPES, MOUNT_TYPES
from ..model.settings import BAYER_PATTERNS, Settings
from .widgets import (UI_TEXT, Console, FwhmLegend, MapPreview, button, check, dspin, field_label, field_row, group,
                      indent_row, page, radio, rich_label, spin, tip)

SERIES_COLUMNS = ["File", "Side", "From", "HA (h)", "Alt", "Focus", "Stars", "FWHM", "Tilt", "eps_rad", "Track",
                  "Coma k", "SIP"]
MAP_TAB_KEYS = ["size", "shape", "coma", "tilt"]
TIPS_ITEM = "Tips for the test frames"

TIPS_HTML = """<h3>Tips for the test frames</h3>
<p style='margin-top:10px; margin-bottom:2px;'><b>Exposure</b></p><ul style='margin-top:0px;'>
<li>Single subframes of about 10-60 s</li><li>Long enough for many stars with good signal</li>
<li>Short enough that seeing and tracking errors do not dominate</li><li>No saturated stars - they are skipped</li></ul>
<p style='margin-top:10px; margin-bottom:2px;'><b>Focus</b></p><ul style='margin-top:0px;'>
<li>Focus carefully on the image center (Bahtinov mask or FWHM)</li><li>Let the optics reach thermal equilibrium first</li></ul>
<p style='margin-top:10px; margin-bottom:2px;'><b>Star field</b></p><ul style='margin-top:0px;'>
<li>Rich and even up to the corners</li><li>Above 45° altitude</li><li>Avoid bright nebulae and the Milky Way's dust lanes</li></ul>
<p style='margin-top:10px; margin-bottom:2px;'><b>Unprocessed</b></p><ul style='margin-top:0px;'>
<li>Linear data</li><li>Not cropped, rotated, registered, drizzled or deconvolved - the image center must stay the
optical axis</li><li>Raw CFA frames: enable Debayer</li></ul>
<p style='margin-top:10px; margin-bottom:2px;'><b>Filter</b></p><ul style='margin-top:0px;'>
<li>Luminance, or a mono camera's broadband filter</li><li>Color camera: the combined channels</li></ul>
<p style='margin-top:10px; margin-bottom:2px;'><b>Compare</b></p><ul style='margin-top:0px;'>
<li>Several frames, and after every adjustment</li>
<li>A stack blurs tracking effects, but also mixes frames with different guiding</li></ul>"""


def _scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    return sa


def _browser() -> QTextBrowser:
    b = QTextBrowser()
    b.setOpenExternalLinks(True)
    return b


class MainWindow(QMainWindow):
    closing = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{TITLE} {VERSION}")
        self._build_left()
        self._build_right()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.left)
        split.addWidget(self.right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.splitter = split

        self.preview_button = button("previewButton")
        self.apply_button = button("applyButton")
        self.documentation_button = button("documentationButton", "Documentation")
        self.close_button = QPushButton("Close")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        self.cancel_run_button = QPushButton("Stop")
        self.cancel_run_button.setVisible(False)
        bottom = QHBoxLayout()
        bottom.addWidget(self.documentation_button)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.cancel_run_button)
        bottom.addWidget(self.preview_button)
        bottom.addWidget(self.apply_button)
        bottom.addWidget(self.close_button)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.setCentralWidget(central)

        self.console = Console()
        dock = QDockWidget("Console", self)
        dock.setObjectName("console")
        dock.setWidget(self.console)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.console_dock = dock
        self.setStatusBar(QStatusBar())
        self.resize(1500, 950)

        for w in (self.debayer_check, self.sd_custom_check, self.streamlines_check, self.shape_show_model_check,
                  self.orientation_heatmap_check, self.measure_asymmetry_check, self.coma_streamlines_check,
                  self.star_asym_arrows_check, self.optics_from_header_check):
            w.toggled.connect(self.update_controls)

    # ------------------------------------------------------------------
    # Left side
    def _build_left(self) -> None:
        self.left = QStackedWidget()
        self.preview = MapPreview()
        self.tips_label = rich_label(TIPS_HTML)
        tips = QWidget()
        QVBoxLayout(tips).addWidget(self.tips_label)
        self.assessment_box = _browser()
        self.ai_box = _browser()
        self.assessment_pane = QTabWidget()
        self.assessment_pane.addTab(self.assessment_box, "Script assessment")
        self.assessment_pane.addTab(self.ai_box, "AI review")
        self.series_hints_label = rich_label()
        hints = QWidget()
        QVBoxLayout(hints).addWidget(self.series_hints_label)
        self.series_preview = MapPreview()
        self.series_box = _browser()
        self.series_ai_box = _browser()
        self.series_pane = QTabWidget()
        self.series_pane.addTab(self.series_preview, "Map")
        self.series_pane.addTab(self.series_box, "Series report")
        self.series_pane.addTab(self.series_ai_box, "AI review")
        self.panes = {"map": self.preview, "tips": _scroll(tips), "assessment": self.assessment_pane,
                      "series_hints": _scroll(hints), "series": self.series_pane}
        for w in self.panes.values():
            self.left.addWidget(w)

    def show_left(self, pane: str) -> None:
        self.left.setCurrentWidget(self.panes[pane])

    # ------------------------------------------------------------------
    # Right side
    def _build_right(self) -> None:
        self.params_tabs = QTabWidget()
        self.setup_tabs = QTabWidget()
        self.setup_tabs.addTab(_scroll(self._general_page()), "General")
        self.setup_tabs.addTab(_scroll(self._detection_page()), "Star detection")
        self.params_tabs.addTab(self.setup_tabs, "Setup")

        self.map_source_combo = tip(QComboBox(), "mapSourceCombo")
        self.maps_tabs = QTabWidget()
        self.maps_tabs.addTab(_scroll(self._size_page()), "Size")
        self.maps_tabs.addTab(_scroll(self._shape_page()), "Shape")
        self.maps_tabs.addTab(_scroll(self._coma_page()), "Coma")
        self.maps_tabs.addTab(_scroll(self._tilt_page()), "Tilt")
        maps = QWidget()
        ml = QVBoxLayout(maps)
        ml.setContentsMargins(4, 4, 4, 4)
        ml.addLayout(field_row(QLabel("Show:"), self.map_source_combo, stretch_last=True))
        ml.addWidget(self.maps_tabs, 1)
        self.params_tabs.addTab(maps, "Maps")
        self.params_tabs.addTab(_scroll(self._assessment_page()), "Assessment")
        self.params_tabs.addTab(self._series_page(), "Series")
        self.right = self.params_tabs

    def current_map_key(self) -> str:
        return MAP_TAB_KEYS[self.maps_tabs.currentIndex()]

    def select_map_tab(self, key: str) -> None:
        self.maps_tabs.setCurrentIndex(MAP_TAB_KEYS.index(key))

    def _general_page(self) -> QWidget:
        self.target_combo = tip(QComboBox(), "targetCombo")
        self.open_button = QPushButton("Open image...")
        self.open_button.setToolTip("<p>Opens a FITS or XISF image as the target image.</p>")
        self.image_info_label = QLabel("No target image selected.")
        self.load_results_button = button("loadResultsButton")
        self.save_results_button = button("saveResultsButton")
        self.results_info_label = rich_label()
        self.debayer_check = check("debayerCheck")
        self.bayer_pattern_combo = tip(QComboBox(), "bayerPatternCombo")
        self.bayer_pattern_combo.addItems(BAYER_PATTERNS)
        self.optics_type_combo = tip(QComboBox(), "opticsTypeCombo")
        self.optics_type_combo.addItems(OPTICS_TYPES)
        self.optics_from_header_check = check("opticsFromHeaderCheck")
        self.pixel_pitch_spin = dspin("pixelPitchSpin", 0, 50, 2)
        self.focal_length_spin = dspin("focalLengthSpin", 0, 10000, 0)
        self.aperture_spin = dspin("apertureSpin", 0, 2000, 0)
        self.camera_value = tip(QLineEdit(), "cameraValue")
        self.camera_value.setReadOnly(True)
        self.mount_combo = tip(QComboBox(), "mountCombo")
        self.mount_combo.addItems(MOUNT_TYPES)
        self.mount_source_label = QLabel()
        self.guiding_combo = tip(QComboBox(), "guidingCombo")
        self.guiding_combo.addItems(GUIDING_TYPES)
        self.guiding_source_label = QLabel()
        self.tilt_plot_3d_check = check("tiltPlot3DCheck")
        self.export_check = check("exportCheck")
        return page(
            group("Target", field_row(field_label("Target image:"), self.target_combo, stretch_last=True),
                  indent_row(self.open_button), indent_row(self.image_info_label, stretch_last=True),
                  indent_row(self.load_results_button, self.save_results_button),
                  indent_row(self.results_info_label, stretch_last=True)),
            group("Debayer", indent_row(self.debayer_check),
                  field_row(field_label("Bayer pattern:"), self.bayer_pattern_combo)),
            group("Optics, camera and mount",
                  field_row(field_label("Optics type:"), self.optics_type_combo, stretch_last=True),
                  indent_row(self.optics_from_header_check),
                  field_row(field_label("Pixel pitch (µm):"), self.pixel_pitch_spin),
                  field_row(field_label("Focal length (mm):"), self.focal_length_spin),
                  field_row(field_label("Aperture (mm):"), self.aperture_spin),
                  field_row(field_label("Camera:"), self.camera_value, stretch_last=True),
                  field_row(field_label("Mount:"), self.mount_combo, self.mount_source_label),
                  field_row(field_label("Guiding:"), self.guiding_combo, self.guiding_source_label)),
            group("Output", indent_row(self.tilt_plot_3d_check), indent_row(self.export_check)))

    def _detection_page(self) -> QWidget:
        self.threshold_spin = dspin("thresholdSpin", 0.5, 10, 2, 0.1)
        self.max_cand_spin = spin("maxCandSpin", 0, 50000)
        self.sd_custom_check = check("sdCustomCheck", "Custom star detector parameters")
        self.sd_layers_spin = spin("sdLayersSpin", 1, 8)
        self.sd_sensitivity_spin = dspin("sdSensitivitySpin", 0, 1, 2, 0.05)
        self.sd_peak_response_spin = dspin("sdPeakResponseSpin", 0, 1, 2, 0.05)
        self.sd_max_distortion_spin = dspin("sdMaxDistortionSpin", 0, 1, 2, 0.05)
        self.sd_clustered_check = check("sdClusteredCheck")
        self.moffat_check = check("moffatCheck")
        self.radius_spin = spin("radiusSpin", 3, 50)
        self.mad_spin = dspin("madSpin", 0, 20, 1, 0.5)
        self.tracking_info_label = tip(rich_label("<i>Measured with Calculate.</i>"), "trackingInfoLabel")
        self.subtract_tracking_check = check("subtractTrackingCheck")
        self.sd_widgets = [self.sd_layers_spin, self.sd_sensitivity_spin, self.sd_peak_response_spin,
                           self.sd_max_distortion_spin, self.sd_clustered_check]
        return page(
            group("Star detection",
                  field_row(field_label("Threshold (sigma):"), self.threshold_spin),
                  field_row(field_label("Max. stars (0 = all):"), self.max_cand_spin),
                  indent_row(self.sd_custom_check),
                  field_row(field_label("Structure layers:"), self.sd_layers_spin),
                  field_row(field_label("Sensitivity:"), self.sd_sensitivity_spin),
                  field_row(field_label("Peak response:"), self.sd_peak_response_spin),
                  field_row(field_label("Max. distortion:"), self.sd_max_distortion_spin),
                  indent_row(self.sd_clustered_check)),
            group("PSF fit", indent_row(self.moffat_check),
                  field_row(field_label("Search radius (px):"), self.radius_spin),
                  field_row(field_label("MAD outlier factor:"), self.mad_spin)),
            group("Common tracking error", indent_row(self.tracking_info_label, stretch_last=True),
                  indent_row(self.subtract_tracking_check)))

    def _size_page(self) -> QWidget:
        self.fwhm_legend = FwhmLegend()
        self.fwhm_px_radio = radio("fwhmPxRadio")
        self.fwhm_arcsec_radio = radio("fwhmArcsecRadio")
        self.fwhm_ratio_radio = radio("fwhmRatioRadio")
        self.fwhm_cell_group = QButtonGroup(self)
        for i, r in enumerate((self.fwhm_px_radio, self.fwhm_arcsec_radio, self.fwhm_ratio_radio)):
            self.fwhm_cell_group.addButton(r, i)
        self.fwhm_grid_details_check = check("fwhmGridDetailsCheck")
        self.grid_eval_label = rich_label("<i>Click Calculate to evaluate the image.</i>")
        return page(group("Legend", self.fwhm_legend),
                    group("FWHM grid: each cell shows", indent_row(self.fwhm_px_radio), indent_row(self.fwhm_arcsec_radio),
                          indent_row(self.fwhm_ratio_radio), indent_row(self.fwhm_grid_details_check)),
                    group("Evaluation", self.grid_eval_label))

    def _shape_page(self) -> QWidget:
        self.show_ellipses_check = check("showEllipsesCheck")
        self.scale_spin = dspin("scaleSpin", 0.1, 10, 2, 0.1)
        self.shape_show_cells_check = check("shapeShowCellsCheck")
        self.streamlines_check = check("streamlinesCheck")
        self.streamline_radius_spin = dspin("streamlineRadiusSpin", 2, 50, 1, 0.5)
        self.shape_significant_only_check = check("shapeSignificantOnlyCheck")
        self.shape_show_model_check = check("shapeShowModelCheck")
        self.shape_model_significant_only_check = check("shapeModelSignificantOnlyCheck")
        self.shape_show_match_check = check("shapeShowMatchCheck")
        self.shape_show_defects_check = check("shapeShowDefectsCheck")
        self.orientation_heatmap_check = check("orientationHeatmapCheck")
        self.orientation_heatmap_degree_spin = spin("orientationHeatmapDegreeSpin", 1, 4)
        self.shape_eval_label = rich_label("<i>Click Calculate to evaluate the image.</i>")
        return page(
            group("Star ellipses", indent_row(self.show_ellipses_check),
                  field_row(field_label("Vector scale:"), self.scale_spin), indent_row(self.shape_show_cells_check)),
            group("Streamlines", indent_row(self.streamlines_check),
                  field_row(field_label("Smoothing radius (%):"), self.streamline_radius_spin),
                  indent_row(self.shape_significant_only_check)),
            group("Optics model", indent_row(self.shape_show_model_check),
                  indent_row(self.shape_model_significant_only_check), indent_row(self.shape_show_match_check),
                  indent_row(self.shape_show_defects_check)),
            group("Orientation heatmap", indent_row(self.orientation_heatmap_check),
                  field_row(field_label("Fit degree:"), self.orientation_heatmap_degree_spin)),
            group("Evaluation", self.shape_eval_label))

    def _coma_page(self) -> QWidget:
        self.measure_asymmetry_check = check("measureAsymmetryCheck")
        self.coma_streamlines_check = check("comaStreamlinesCheck")
        self.coma_streamline_radius_spin = dspin("comaStreamlineRadiusSpin", 5, 60, 1, 0.5)
        self.coma_streamline_min_spin = dspin("comaStreamlineMinSpin", 0, 90, 0, 1)
        self.coma_arrows_check = check("comaArrowsCheck")
        self.star_asym_arrows_check = check("starAsymArrowsCheck")
        self.star_asym_min_spin = dspin("starAsymMinSpin", 0, 10, 1, 0.5)
        self.star_asym_color_check = check("starAsymColorCheck")
        self.coma_eval_label = rich_label("<i>Click Calculate to evaluate the image.</i>")
        return page(
            group("Coma field", indent_row(self.measure_asymmetry_check), indent_row(self.coma_streamlines_check),
                  field_row(field_label("Smoothing radius (%):"), self.coma_streamline_radius_spin),
                  field_row(field_label("Stop below (%):"), self.coma_streamline_min_spin),
                  indent_row(self.coma_arrows_check)),
            group("Per-star arrows", indent_row(self.star_asym_arrows_check),
                  field_row(field_label("Min. strength (sigma):"), self.star_asym_min_spin),
                  indent_row(self.star_asym_color_check)),
            group("Evaluation", self.coma_eval_label))

    def _tilt_page(self) -> QWidget:
        self.tilt_axis_check = check("tiltAxisCheck")
        self.tilt_eval_label = rich_label("<i>Click Calculate to evaluate the image.</i>")
        return page(group("Tilt axis", indent_row(self.tilt_axis_check)), group("Evaluation", self.tilt_eval_label))

    def _assessment_page(self) -> QWidget:
        self.ai_key_edit = tip(QLineEdit(), "aiKeyEdit")
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_language_edit = tip(QLineEdit(), "aiLanguageEdit")
        self.ai_detail_combo = tip(QComboBox(), "aiDetailCombo")
        self.ai_detail_combo.addItems(AI_DETAIL_LEVELS)
        self.ai_send_map_check = check("aiSendMapCheck")
        self.ai_save_log_check = check("aiSaveLogCheck")
        self.ai_button = button("aiButton")
        self.ai_continue_button = button("aiContinueButton")
        self.ai_continue_button.setEnabled(False)
        return page(group("AI review (Claude)",
                          field_row(field_label("API key:"), self.ai_key_edit, stretch_last=True),
                          field_row(field_label("Answer language:"), self.ai_language_edit),
                          field_row(field_label("Explanation:"), self.ai_detail_combo),
                          indent_row(self.ai_send_map_check), indent_row(self.ai_save_log_check),
                          indent_row(self.ai_button, self.ai_continue_button)))

    def _series_page(self) -> QWidget:
        self.series_tree = tip(QTreeWidget(), "seriesTree")
        self.series_tree.setColumnCount(len(SERIES_COLUMNS))
        self.series_tree.setHeaderLabels(SERIES_COLUMNS)
        self.series_tree.setRootIsDecorated(False)
        self.series_tree.setAlternatingRowColors(True)
        self.series_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.series_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.series_add_button = button("seriesAddButton")
        self.series_remove_button = button("seriesRemoveButton")
        self.series_clear_button = button("seriesClearButton")
        self.series_west_button = QPushButton("West")
        self.series_east_button = QPushButton("East")
        self.series_auto_button = QPushButton("Auto")
        for b, t in ((self.series_west_button, "Sets the side of the selected frames to West (before the flip)."),
                     (self.series_east_button, "Sets the side of the selected frames to East (after the flip)."),
                     (self.series_auto_button, "The side of the selected frames from the header again.")):
            b.setToolTip(f"<p>{t}</p>")
        self.series_show_maps_check = check("seriesShowMapsCheck")
        self.series_save_maps_check = check("seriesSaveMapsCheck")
        self.series_save_mosaic_check = check("seriesSaveMosaicCheck")
        self.series_analyze_button = button("seriesAnalyzeButton")
        self.series_analyze_button.setMinimumWidth(self.series_analyze_button.sizeHint().width() + 24)
        self.series_ai_button = button("seriesAiButton")
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.series_tree, 1)
        for row in ((self.series_add_button, self.series_remove_button, self.series_clear_button),
                    (QLabel("Side:"), self.series_west_button, self.series_east_button, self.series_auto_button)):
            h = QHBoxLayout()
            for x in row:
                h.addWidget(x)
            h.addStretch(1)
            lay.addLayout(h)
        for c in (self.series_show_maps_check, self.series_save_maps_check, self.series_save_mosaic_check):
            lay.addWidget(c)
        h = QHBoxLayout()
        h.addWidget(self.series_analyze_button)
        h.addWidget(self.series_ai_button)
        h.addStretch(1)
        lay.addLayout(h)
        return w

    # ------------------------------------------------------------------
    # Settings <-> widgets
    def apply_settings(self, s: Settings) -> None:
        self.threshold_spin.setValue(s.threshold)
        self.radius_spin.setValue(s.radius)
        self.moffat_check.setChecked(s.use_moffat)
        self.mad_spin.setValue(s.mad_outlier_factor)
        self.max_cand_spin.setValue(s.max_candidates)
        self.sd_custom_check.setChecked(s.sd_custom)
        self.sd_layers_spin.setValue(s.sd_structure_layers)
        self.sd_sensitivity_spin.setValue(s.sd_sensitivity)
        self.sd_peak_response_spin.setValue(s.sd_peak_response)
        self.sd_max_distortion_spin.setValue(s.sd_max_distortion)
        self.sd_clustered_check.setChecked(s.sd_allow_clustered)
        self.debayer_check.setChecked(s.debayer)
        self.bayer_pattern_combo.setCurrentIndex(BAYER_PATTERNS.index(s.bayer_pattern))
        self.scale_spin.setValue(s.vector_scale)
        self.export_check.setChecked(s.do_export)
        self.optics_from_header_check.setChecked(s.optics_from_header)
        self.manual_optics = (s.pixel_pitch_um, s.focal_length_mm, s.aperture_mm)
        self.pixel_pitch_spin.setValue(s.pixel_pitch_um)
        self.focal_length_spin.setValue(s.focal_length_mm)
        self.aperture_spin.setValue(s.aperture_mm)
        self.optics_type_combo.setCurrentIndex(s.optics_type)
        self.mount_combo.setCurrentIndex(s.mount_type)
        self.guiding_combo.setCurrentIndex(s.guiding_type)
        self.subtract_tracking_check.setChecked(s.subtract_tracking)
        self.measure_asymmetry_check.setChecked(s.measure_asymmetry)
        self.streamlines_check.setChecked(s.show_streamlines)
        self.shape_significant_only_check.setChecked(s.shape_significant_only)
        self.shape_show_cells_check.setChecked(s.shape_show_cells)
        self.shape_show_model_check.setChecked(s.shape_show_model)
        self.shape_model_significant_only_check.setChecked(s.shape_model_significant_only)
        self.shape_show_match_check.setChecked(s.shape_show_match)
        self.shape_show_defects_check.setChecked(s.shape_show_defects)
        self.streamline_radius_spin.setValue(s.streamline_radius_percent)
        self.orientation_heatmap_check.setChecked(s.show_orientation_heatmap)
        self.orientation_heatmap_degree_spin.setValue(s.orientation_heatmap_degree)
        self.show_ellipses_check.setChecked(not s.hide_ellipses)
        self.fwhm_grid_details_check.setChecked(s.fwhm_grid_details)
        self.fwhm_cell_group.button(min(max(s.fwhm_cell_value, 0), 2)).setChecked(True)
        self.tilt_plot_3d_check.setChecked(s.show_3d_tilt_plot)
        self.tilt_axis_check.setChecked(s.show_tilt_axis)
        self.star_asym_arrows_check.setChecked(s.show_star_asym_arrows)
        self.star_asym_min_spin.setValue(s.star_asym_min_sigma)
        self.star_asym_color_check.setChecked(s.star_asym_color_by_strength)
        self.coma_streamlines_check.setChecked(s.show_coma_streamlines)
        self.coma_streamline_radius_spin.setValue(s.coma_streamline_radius_percent)
        self.coma_streamline_min_spin.setValue(s.coma_streamline_min_percent)
        self.coma_arrows_check.setChecked(s.show_coma_arrows)
        self.ai_send_map_check.setChecked(s.ai_send_map)
        self.ai_language_edit.setText(s.ai_language)
        self.ai_detail_combo.setCurrentIndex(min(max(s.ai_detail, 0), len(AI_DETAIL_LEVELS) - 1))
        self.ai_save_log_check.setChecked(s.ai_save_log)
        self.series_show_maps_check.setChecked(s.series_show_maps)
        self.series_save_maps_check.setChecked(s.series_save_maps)
        self.series_save_mosaic_check.setChecked(s.series_save_mosaic)
        self.update_controls()

    def collect_settings(self) -> Settings:
        s = Settings()
        s.threshold = self.threshold_spin.value()
        s.radius = self.radius_spin.value()
        s.use_moffat = self.moffat_check.isChecked()
        s.mad_outlier_factor = self.mad_spin.value()
        s.max_candidates = self.max_cand_spin.value()
        s.sd_custom = self.sd_custom_check.isChecked()
        s.sd_structure_layers = self.sd_layers_spin.value()
        s.sd_sensitivity = self.sd_sensitivity_spin.value()
        s.sd_peak_response = self.sd_peak_response_spin.value()
        s.sd_max_distortion = self.sd_max_distortion_spin.value()
        s.sd_allow_clustered = self.sd_clustered_check.isChecked()
        s.debayer = self.debayer_check.isChecked()
        s.bayer_pattern = BAYER_PATTERNS[self.bayer_pattern_combo.currentIndex()]
        s.vector_scale = self.scale_spin.value()
        s.do_export = self.export_check.isChecked()
        s.optics_from_header = self.optics_from_header_check.isChecked()
        # The fields show the header's values while that option is on; the
        # own values are kept aside meanwhile.
        if s.optics_from_header:
            s.pixel_pitch_um, s.focal_length_mm, s.aperture_mm = self.manual_optics
        else:
            s.pixel_pitch_um = self.pixel_pitch_spin.value()
            s.focal_length_mm = self.focal_length_spin.value()
            s.aperture_mm = self.aperture_spin.value()
        s.optics_type = self.optics_type_combo.currentIndex()
        s.mount_type = self.manual_mount if s.optics_from_header else self.mount_combo.currentIndex()
        s.guiding_type = self.guiding_combo.currentIndex()
        s.subtract_tracking = self.subtract_tracking_check.isChecked()
        s.measure_asymmetry = self.measure_asymmetry_check.isChecked()
        s.show_streamlines = self.streamlines_check.isChecked()
        s.shape_significant_only = self.shape_significant_only_check.isChecked()
        s.shape_show_cells = self.shape_show_cells_check.isChecked()
        s.shape_show_model = self.shape_show_model_check.isChecked()
        s.shape_model_significant_only = self.shape_model_significant_only_check.isChecked()
        s.shape_show_match = self.shape_show_match_check.isChecked()
        s.shape_show_defects = self.shape_show_defects_check.isChecked()
        s.streamline_radius_percent = self.streamline_radius_spin.value()
        s.show_orientation_heatmap = self.orientation_heatmap_check.isChecked()
        s.orientation_heatmap_degree = self.orientation_heatmap_degree_spin.value()
        s.hide_ellipses = not self.show_ellipses_check.isChecked()
        s.fwhm_grid_details = self.fwhm_grid_details_check.isChecked()
        s.fwhm_cell_value = max(0, self.fwhm_cell_group.checkedId())
        s.show_3d_tilt_plot = self.tilt_plot_3d_check.isChecked()
        s.show_tilt_axis = self.tilt_axis_check.isChecked()
        s.show_star_asym_arrows = self.star_asym_arrows_check.isChecked()
        s.star_asym_min_sigma = self.star_asym_min_spin.value()
        s.star_asym_color_by_strength = self.star_asym_color_check.isChecked()
        s.show_coma_streamlines = self.coma_streamlines_check.isChecked()
        s.coma_streamline_radius_percent = self.coma_streamline_radius_spin.value()
        s.coma_streamline_min_percent = self.coma_streamline_min_spin.value()
        s.show_coma_arrows = self.coma_arrows_check.isChecked()
        s.ai_send_map = self.ai_send_map_check.isChecked()
        s.ai_language = self.ai_language_edit.text()
        s.ai_detail = self.ai_detail_combo.currentIndex()
        s.ai_save_log = self.ai_save_log_check.isChecked()
        s.series_show_maps = self.series_show_maps_check.isChecked()
        s.series_save_maps = self.series_save_maps_check.isChecked()
        s.series_save_mosaic = self.series_save_mosaic_check.isChecked()
        return s

    manual_optics = (0.0, 0.0, 0.0)
    manual_mount = 0
    color_target = False
    series_has_frames = False

    def update_controls(self) -> None:
        """Options that only matter when another one is set are disabled
        otherwise."""
        self.debayer_check.setEnabled(not self.color_target or self.series_has_frames)
        debayer = self.debayer_check.isEnabled() and self.debayer_check.isChecked()
        self.bayer_pattern_combo.setEnabled(debayer)
        for w in self.sd_widgets:
            w.setEnabled(self.sd_custom_check.isChecked())
        sl = self.streamlines_check.isChecked()
        self.streamline_radius_spin.setEnabled(sl)
        self.shape_significant_only_check.setEnabled(sl)
        self.shape_model_significant_only_check.setEnabled(self.shape_show_model_check.isChecked())
        self.orientation_heatmap_degree_spin.setEnabled(self.orientation_heatmap_check.isChecked())
        asym = self.measure_asymmetry_check.isChecked()
        coma_lines = asym and self.coma_streamlines_check.isChecked()
        star_arrows = asym and self.star_asym_arrows_check.isChecked()
        self.coma_streamlines_check.setEnabled(asym)
        self.coma_streamline_radius_spin.setEnabled(coma_lines)
        self.coma_streamline_min_spin.setEnabled(coma_lines)
        self.coma_arrows_check.setEnabled(asym)
        self.star_asym_arrows_check.setEnabled(asym)
        self.star_asym_min_spin.setEnabled(star_arrows)
        self.star_asym_color_check.setEnabled(star_arrows)
        header = self.optics_from_header_check.isChecked()
        for w in (self.pixel_pitch_spin, self.focal_length_spin, self.aperture_spin):
            w.setReadOnly(header)
        self.mount_combo.setEnabled(not header)

    def setting_widgets(self) -> list:
        """The widgets whose change redraws the preview or the evaluations."""
        return [w for w in self.findChildren(QWidget)
                if w.objectName() in UI_TEXT and w.objectName() not in (
                    "targetCombo", "mapSourceCombo", "aiKeyEdit", "seriesTree")]

    def closeEvent(self, e) -> None:
        self.closing.emit()
        super().closeEvent(e)
