"""The main window of the app.

    Menu bar   File, Analysis, Series, View, Settings, Help
    Toolbar    the most used actions
    Center     tabs: Map (the map of the image, with the map switcher),
               Assessment (the rule-based assessment and the AI review),
               Series (the frames, their report and maps)
    Docks      the inspector (image info, the layers and the evaluation of
               the map shown) and the console
    Status bar progress and Stop

The window only builds widgets and actions; what they do is up to the
controller.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDockWidget, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QMainWindow, QMenu, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QTextBrowser, QToolBar, QToolButton,
                               QTreeWidget, QVBoxLayout, QWidget)

from .. import TITLE, VERSION
from ..model.assessment import OPTICS_TYPES
from .binding import Binder
from .inspector import Inspector
from .map_view import MapView
from .plots import ProfilesView, SeriesPlots
from .theme import icon
from .widgets import Console, rich_label, tip

SERIES_COLUMNS = ["File", "Side", "From", "HA (h)", "Alt", "Focus", "Stars", "FWHM", "Tilt", "eps_rad", "Track",
                  "Coma k", "SIP"]
MAP_KEYS = ["size", "shape", "coma", "tilt"]
MAP_LABELS = ["Star Size", "Star Shape", "Coma", "Tilt"]
TAB_MAP, TAB_PROFILES, TAB_ASSESSMENT, TAB_SERIES = 0, 1, 2, 3
SERIES_HINTS, SERIES_FRAME_MAP, SERIES_REPORT, SERIES_PLOTS, SERIES_AI = range(5)

WELCOME_HTML = """<p>Diagnoses the optical system from the shapes of the stars: sensor tilt, corrector spacing, field curvature,
coma, collimation and tracking errors.</p>
<p><b>File › Open Image…</b> (or drop a FITS/XISF file here), then <b>Analysis › Calculate</b>.</p>
<h3>Tips for the test frames</h3>
<p style='margin-bottom:2px;'><b>Exposure</b></p><ul style='margin-top:0px;'>
<li>Single subframes of about 10-60 s</li><li>Long enough for many stars with good signal</li>
<li>Short enough that seeing and tracking errors do not dominate</li><li>No saturated stars - they are skipped</li></ul>
<p style='margin-bottom:2px;'><b>Focus</b></p><ul style='margin-top:0px;'>
<li>Focus carefully on the image center (Bahtinov mask or FWHM)</li><li>Let the optics reach thermal equilibrium first</li></ul>
<p style='margin-bottom:2px;'><b>Star field</b></p><ul style='margin-top:0px;'>
<li>Rich and even up to the corners</li><li>Above 45° altitude</li><li>Avoid bright nebulae and the Milky Way's dust lanes</li></ul>
<p style='margin-bottom:2px;'><b>Unprocessed</b></p><ul style='margin-top:0px;'>
<li>Linear data</li><li>Not cropped, rotated, registered, drizzled or deconvolved - the image center must stay the
optical axis</li><li>Raw CFA frames: enable Debayer (Settings › Preferences › Measurement)</li></ul>
<p style='margin-bottom:2px;'><b>Filter</b></p><ul style='margin-top:0px;'>
<li>Luminance, or a mono camera's broadband filter</li><li>Color camera: the combined channels</li></ul>
<p style='margin-bottom:2px;'><b>Compare</b></p><ul style='margin-top:0px;'>
<li>Several frames, and after every adjustment</li>
<li>A stack blurs tracking effects, but also mixes frames with different guiding</li></ul>"""


def _browser() -> QTextBrowser:
    b = QTextBrowser()
    b.setOpenExternalLinks(True)
    return b


def _scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    return sa


class MainWindow(QMainWindow):
    closing = Signal()
    files_dropped = Signal(list)

    def __init__(self, binder: Binder):
        super().__init__()
        self.setWindowTitle(f"{TITLE} {VERSION}")
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)
        self.binder = binder
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_center()
        self._build_docks()
        self._build_status()
        self.resize(1550, 980)

    # ------------------------------------------------------------------
    # Actions
    def _action(self, text: str, shortcut=None, tooltip: str = "", icon=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        if tooltip:
            a.setToolTip(tooltip)
            a.setStatusTip(tooltip)
        if icon is not None:
            a.setIcon(globals()["icon"](icon))
        a.setCheckable(checkable)
        return a

    def _build_actions(self) -> None:
        K = QKeySequence.StandardKey
        self.act_open = self._action("&Open Image…", K.Open, "Open a FITS or XISF image", "mdi6.folder-open-outline")
        self.act_close_image = self._action("&Close Image", K.Close, "Close the current image")
        self.act_load_results = self._action("&Load Results…", None, "Load a saved measurement")
        self.act_save_results = self._action("&Save Results…", K.Save, "Save the measurement of the last Calculate",
                                             "mdi6.content-save-outline")
        self.act_export_maps = self._action("&Export Maps…", "Ctrl+E",
                                            "Save the four maps, the 3D tilt plot and the star table as files",
                                            "mdi6.export-variant")
        self.act_export_csv = self._action("Export Star &Table (CSV)…", None, "Save one line per star as CSV")
        self.act_quit = self._action("&Quit", K.Quit)
        self.act_quit.setMenuRole(QAction.MenuRole.QuitRole)

        self.act_calculate = self._action("&Calculate", "Ctrl+R", "Measure the stars and draw the map",
                                          "mdi6.play")
        self.act_ask = self._action("Ask &Claude", "Ctrl+Shift+A", "A second opinion on the results by Claude",
                                    "mdi6.creation")
        self.act_continue = self._action("C&ontinue the Answer", None, "Get the rest of an answer cut off at the "
                                         "token limit")
        self.act_continue.setEnabled(False)
        self.act_plot3d = self._action("3D &Tilt Plot…", None, "The tilt plane fitted to the quadrants, as a pseudo-"
                                       "3D plot", "mdi6.cube-outline")
        self.act_stop = self._action("&Stop", "Esc", "Stop the running calculation", "mdi6.stop")
        self.act_stop.setEnabled(False)

        self.act_series_add = self._action("&Add Frames…", "Ctrl+Shift+O", "Add light frames to the series",
                                           "mdi6.image-multiple-outline")
        self.act_series_remove = self._action("&Remove Selected Frames", None)
        self.act_series_clear = self._action("C&lear Series", None)
        self.act_side_west = self._action("&West (before the flip)", None)
        self.act_side_east = self._action("&East (after the flip)", None)
        self.act_side_auto = self._action("From the &Header", None)
        self.act_series_run = self._action("Analyze &Series", "Ctrl+Shift+R",
                                           "Measure all frames and compare the sides of the flip")

        self.map_actions = []
        self.map_group = QActionGroup(self)
        for i, label in enumerate(MAP_LABELS):
            a = self._action(label, f"Ctrl+{i + 1}", checkable=True)
            self.map_group.addAction(a)
            self.map_actions.append(a)
        self.map_actions[0].setChecked(True)
        self.act_fit = self._action("Zoom to &Fit", "Ctrl+0")
        self.act_zoom_in = self._action("Zoom &In", K.ZoomIn)
        self.act_zoom_out = self._action("Zoom &Out", K.ZoomOut)

        self.act_preferences = self._action("&Preferences…", "Ctrl+,", "Measurement, optics, output and AI settings",
                                            "mdi6.cog-outline")
        self.act_preferences.setMenuRole(QAction.MenuRole.NoRole)
        self.act_reset = self._action("&Reset All Settings…", None)
        self.optics_actions = []
        self.optics_group = QActionGroup(self)
        for i, name in enumerate(OPTICS_TYPES):
            a = self._action(name, checkable=True)
            a.setData(i)
            self.optics_group.addAction(a)
            self.optics_actions.append(a)
        self.act_subtract_tracking = self._action("&Subtract the Common Tracking Error", checkable=True)
        self.act_measure_asym = self._action("Measure PSF &Asymmetry (Coma)", checkable=True)

        self.act_handbook = self._action("&Handbook", K.HelpContents, "", "mdi6.book-open-variant")
        self.act_reference = self._action("Dialog &Reference (website)", None)
        self.act_about = self._action("&About", None)
        self.act_about.setMenuRole(QAction.MenuRole.AboutRole)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_open)
        self.recent_menu = m.addMenu("Open &Recent")
        m.addAction(self.act_close_image)
        m.addSeparator()
        m.addAction(self.act_load_results)
        m.addAction(self.act_save_results)
        m.addSeparator()
        m.addAction(self.act_export_maps)
        m.addAction(self.act_export_csv)
        m.addSeparator()
        m.addAction(self.act_quit)

        m = mb.addMenu("&Analysis")
        m.addAction(self.act_calculate)
        m.addAction(self.act_stop)
        m.addSeparator()
        m.addAction(self.act_ask)
        m.addAction(self.act_continue)
        m.addSeparator()
        m.addAction(self.act_plot3d)

        m = mb.addMenu("&Series")
        m.addAction(self.act_series_add)
        m.addAction(self.act_series_remove)
        m.addAction(self.act_series_clear)
        side = m.addMenu("Set &Side of the Selected Frames")
        for a in (self.act_side_west, self.act_side_east, self.act_side_auto):
            side.addAction(a)
        m.addSeparator()
        m.addAction(self.act_series_run)

        m = mb.addMenu("&View")
        for a in self.map_actions:
            m.addAction(a)
        self.source_menu = m.addMenu("Show &Data Of")
        m.addSeparator()
        m.addAction(self.act_fit)
        m.addAction(self.act_zoom_in)
        m.addAction(self.act_zoom_out)
        m.addSeparator()
        self.view_menu = m

        m = mb.addMenu("Se&ttings")
        m.addAction(self.act_preferences)
        om = m.addMenu("&Optics Type")
        for a in self.optics_actions:
            om.addAction(a)
        m.addAction(self.act_subtract_tracking)
        m.addAction(self.act_measure_asym)
        m.addSeparator()
        m.addAction(self.act_reset)

        m = mb.addMenu("&Help")
        m.addAction(self.act_handbook)
        m.addAction(self.act_reference)
        m.addSeparator()
        m.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.addAction(self.act_open)
        calc = QToolButton()
        calc.setDefaultAction(self.act_calculate)
        calc.setObjectName("primary")
        calc.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.act_calculate.setIcon(icon("mdi6.play", "#ffffff"))
        tb.addWidget(calc)
        tb.addAction(self.act_stop)
        spacer = QWidget()
        spacer.setFixedWidth(16)
        tb.addWidget(spacer)
        seg = QFrame()
        seg.setObjectName("segmented")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(2)
        for i, label in enumerate(MAP_LABELS):
            b = QToolButton()
            b.setDefaultAction(self.map_actions[i])
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            sl.addWidget(b)
        tb.addWidget(seg)
        spacer = QWidget()
        spacer.setFixedWidth(12)
        tb.addWidget(spacer)
        self.source_combo = tip(QComboBox(), "mapSourceCombo")
        self.source_combo.setMinimumWidth(230)
        tb.addWidget(self.source_combo)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(stretch)
        tb.addAction(self.act_export_maps)
        tb.addAction(self.act_ask)
        tb.addAction(self.act_preferences)
        self.addToolBar(tb)

    def _build_center(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Map
        self.preview = MapView()
        self.welcome = rich_label(WELCOME_HTML)
        welcome = QWidget()
        wl = QVBoxLayout(welcome)
        wl.setContentsMargins(40, 32, 40, 24)
        title = QLabel("Star Aberration Diagnostics")
        title.setObjectName("title")
        wl.addWidget(title)
        open_btn = QPushButton(icon("mdi6.folder-open-outline", "#ffffff"), "  Open Image…")
        open_btn.setObjectName("primary")
        open_btn.clicked.connect(self.act_open.trigger)
        row = QHBoxLayout()
        row.addWidget(open_btn)
        add_btn = QPushButton(icon("mdi6.image-multiple-outline"), "  Add Series Frames…")
        add_btn.clicked.connect(self.act_series_add.trigger)
        row.addWidget(add_btn)
        row.addStretch(1)
        wl.addLayout(row)
        wl.addWidget(self.welcome)
        wl.addStretch(1)
        self.map_stack = QStackedWidget()
        self.map_stack.addWidget(_scroll(welcome))
        self.map_stack.addWidget(self.preview)
        self.tabs.addTab(self.map_stack, icon("mdi6.image-filter-center-focus-weak"), "Map")
        self.profiles = ProfilesView()
        self.tabs.addTab(self.profiles, icon("mdi6.chart-scatter-plot"), "Profiles")

        # Assessment
        self.assessment_box = _browser()
        self.ai_box = _browser()
        ai = QWidget()
        al = QVBoxLayout(ai)
        al.setContentsMargins(0, 0, 0, 0)
        hl = QHBoxLayout()
        hl.addWidget(QLabel("<b>AI review (Claude)</b>"))
        hl.addStretch(1)
        self.ai_button = QPushButton(icon("mdi6.creation", "#ffffff"), "  Ask Claude")
        self.ai_button.setObjectName("primary")
        self.ai_button.clicked.connect(self.act_ask.trigger)
        self.ai_continue_button = QPushButton("Continue")
        self.ai_continue_button.clicked.connect(self.act_continue.trigger)
        self.ai_continue_button.setEnabled(False)
        hl.addWidget(self.ai_button)
        hl.addWidget(self.ai_continue_button)
        al.addLayout(hl)
        al.addWidget(self.ai_box, 1)
        script = QWidget()
        sl = QVBoxLayout(script)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("<b>Script assessment</b>"))
        sl.addWidget(self.assessment_box, 1)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(script)
        split.addWidget(ai)
        self.tabs.addTab(split, icon("mdi6.clipboard-check-outline"), "Assessment")

        # Series
        self.series_tree = tip(QTreeWidget(), "seriesTree")
        self.series_tree.setColumnCount(len(SERIES_COLUMNS))
        self.series_tree.setHeaderLabels(SERIES_COLUMNS)
        self.series_tree.setRootIsDecorated(False)
        self.series_tree.setAlternatingRowColors(True)
        self.series_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.series_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.series_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        for a in (self.act_series_add, self.act_series_remove, self.act_side_west, self.act_side_east,
                  self.act_side_auto):
            self.series_tree.addAction(a)
        self.series_run_button = QPushButton(icon("mdi6.play", "#ffffff"), "  Analyze Series")
        self.series_run_button.clicked.connect(self.act_series_run.trigger)
        tip(self.series_run_button, "seriesAnalyzeButton")
        self.series_run_button.setObjectName("primary")
        add = QPushButton(icon("mdi6.image-multiple-outline"), "  Add Frames…")
        add.clicked.connect(self.act_series_add.trigger)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.series_tree, 1)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch(1)
        row.addWidget(self.series_run_button)
        ll.addLayout(row)
        self.series_hints = rich_label()
        self.series_preview = MapView()
        self.series_box = _browser()
        self.series_ai_box = _browser()
        self.series_plots = SeriesPlots()
        self.series_tabs = QTabWidget()
        self.series_tabs.addTab(_scroll(self.series_hints), "Before You Start")
        self.series_tabs.addTab(self.series_preview, "Frame Map")
        self.series_tabs.addTab(self.series_box, "Report")
        self.series_tabs.addTab(self.series_plots, "Over the Night")
        self.series_tabs.addTab(self.series_ai_box, "AI Review")
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(left)
        split.addWidget(self.series_tabs)
        split.setSizes([300, 600])
        self.tabs.addTab(split, icon("mdi6.chart-timeline-variant"), "Series")
        self.setCentralWidget(self.tabs)

    def _build_docks(self) -> None:
        self.inspector = Inspector(self.binder)
        d = QDockWidget("Inspector", self)
        d.setObjectName("inspector")
        d.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d)
        self.inspector_dock = d
        self.console = Console()
        c = QDockWidget("Console", self)
        c.setObjectName("console")
        c.setWidget(self.console)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, c)
        self.console_dock = c
        c.hide()
        self.resizeDocks([d], [520], Qt.Orientation.Horizontal)
        self.view_menu.addAction(d.toggleViewAction())
        self.view_menu.addAction(c.toggleViewAction())
        d.toggleViewAction().setShortcut(QKeySequence("Ctrl+I"))
        c.toggleViewAction().setShortcut(QKeySequence("Ctrl+L"))

    def _build_status(self) -> None:
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setMaximumWidth(360)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        self.stop_button = QToolButton()
        self.stop_button.setDefaultAction(self.act_stop)
        self.stop_button.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.stop_button)

    # ------------------------------------------------------------------
    def current_map_key(self) -> str:
        for i, a in enumerate(self.map_actions):
            if a.isChecked():
                return MAP_KEYS[i]
        return MAP_KEYS[0]

    def show_map_view(self, has_image: bool) -> None:
        self.map_stack.setCurrentIndex(1 if has_image else 0)

    def set_busy(self, busy: bool, label: str = "") -> None:
        self.progress.setVisible(busy)
        self.stop_button.setVisible(busy)
        self.act_stop.setEnabled(busy)
        self.progress.setValue(0)
        self.progress.setFormat(label)
        for a in (self.act_open, self.act_calculate, self.act_export_maps, self.act_export_csv, self.act_ask,
                  self.act_load_results, self.act_save_results, self.act_plot3d):
            a.setEnabled(not busy)
        self.ai_button.setEnabled(not busy)

    def set_progress(self, fraction: float, label: str) -> None:
        self.progress.setValue(int(1000 * min(1.0, max(0.0, fraction))))
        self.progress.setFormat(label)

    # ---- drag and drop
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)

    def closeEvent(self, e) -> None:
        self.closing.emit()
        super().closeEvent(e)
