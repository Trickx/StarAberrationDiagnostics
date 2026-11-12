"""The controller: owns the settings and the state (image, analysis,
series, pending AI answer), reacts to the actions of the window and runs the
work in background threads."""

from __future__ import annotations

import base64
import datetime as _dt
import html
import math
import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QTreeWidgetItem, QVBoxLayout, QDialogButtonBox

from .. import TITLE, VERSION
from ..ai import AI_DETAIL_LEVELS, AI_MAP_MAX_SIDE
from ..ai.client import Review, ReviewError, request_review
from ..ai.format import format_review_html, format_review_text
from ..ai.log import save_ai_log
from ..ai.prompt import build_message
from ..model.analysis import (Analysis, analysis_key, analyze_image, export_csv, fwhm_display_unit,
                              report_analysis, shape_evaluation_for, stars_for)
from ..model.assessment import OPTICS_TYPES, assess_image, format_assessment_html
from ..model.coma import log_coma_field
from ..model.fwhm_grid import FWHM_GRID_SIZE, compute_siril_tilt, evaluate_fwhm_grid, format_fwhm_grid_evaluation
from ..model.image import SUPPORTED_EXTENSIONS, ImageData, load_image, stretched_rgb8
from ..model.log import SILENT, Log
from ..model.optics import (GUIDING_TYPES, GUIDING_UNKNOWN, MOUNT_TYPES, effective_guiding, estimate_mount,
                            keyword_values, read_camera_from_header, read_optics_from_header, resolve_optics)
from ..model.results_io import load_results, results_default_path, save_results
from ..model.series import (SIDE_EAST, SIDE_WEST, assign_series_sides, build_static_series, compare_series,
                            direction_name, export_series_csv, format_series_report, format_static_series,
                            frame_output_path, measure_series, read_series_frame, series_hints_status, series_metric,
                            sip_corner_shifts, sip_max_shift)
from ..model.settings import Settings, SettingsStore
from ..model.shape import format_shape_evaluation
from ..model.tilt import compute_tilt_axis
from ..render.export import png_bytes, save_png, scaled_to_max_side
from ..render.maps import MAP_VIEWS, format_tilt_evaluation, map_view_by_key, render_map
from ..render.paint import image_from_rgb
from ..render.plot3d import render_3d_tilt_plot, render_mosaic
from ..view.binding import Binder
from ..view.main_window import MAP_KEYS, TAB_ASSESSMENT, TAB_SERIES, MainWindow
from ..view.preferences import PreferencesDialog
from ..view.widgets import MapPreview, evaluation_html
from .worker import Worker, start

HANDBOOK_URL = "https://github.com/Trickx/StarAberrationDiagnostics/wiki"
REFERENCE_URL = "https://tricx.de/pi-scripts/StarAberrationDiagnostics/dialog/"
IMAGE_FILTER = "Images (*.fits *.fit *.fts *.xisf);;All files (*)"
MAX_RECENT = 10
# Fields whose change makes the measurement of the last Calculate stale.
MEASUREMENT_FIELDS = {"threshold", "radius", "use_moffat", "mad_outlier_factor", "max_candidates", "debayer",
                      "bayer_pattern", "sd_custom", "sd_structure_layers", "sd_sensitivity", "sd_peak_response",
                      "sd_max_distortion", "sd_allow_clustered"}


class MainController:
    def __init__(self, store: SettingsStore):
        self.store = store
        self.s: Settings = store.load()
        self.binder = Binder(lambda: self.s, self.field_changed)
        self.v = MainWindow(self.binder)
        self.binder.load()

        self.target: ImageData | None = None
        self.target_bitmap: QImage | None = None
        self.analysis: Analysis | None = None
        self.preview_stale = False
        self.map_source = 0
        self.series_frames = []
        self.series_result = None
        self.series_static: list[dict] = []
        self.series_running = False
        self.frame_timing = None
        self.ai_pending: tuple[Review, dict, dict] | None = None
        self.busy: Worker | None = None
        self.render_gen = 0
        self.workers: set[Worker] = set()

        state = store.load_ui_state()
        self.recent: list[str] = [p for p in state.get("recent", []) if isinstance(p, str)]
        for key, fn in (("geometry", self.v.restoreGeometry), ("window_state", self.v.restoreState)):
            if key in state:
                try:
                    fn(QByteArray(base64.b64decode(state[key])))
                except (ValueError, TypeError):
                    pass

        self.redraw_timer = QTimer()
        self.redraw_timer.setSingleShot(True)
        self.redraw_timer.setInterval(150)
        self.redraw_timer.timeout.connect(self.refresh_preview)
        self._connect()
        self._sync_settings_menu()
        self._fill_recent_menu()
        self._fill_sources()
        self.clear_ai_review()
        self.update_series_hints()
        self.update_image_info()
        self.v.inspector.update_enabled(self.s)
        self.refresh_preview()

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        v = self.v
        v.act_open.triggered.connect(self.open_image_dialog)
        v.act_close_image.triggered.connect(self.close_image)
        v.act_load_results.triggered.connect(self.load_results)
        v.act_save_results.triggered.connect(self.save_results)
        v.act_export_maps.triggered.connect(self.export_maps)
        v.act_export_csv.triggered.connect(self.export_star_table)
        v.act_quit.triggered.connect(v.close)
        v.act_calculate.triggered.connect(lambda: self.calculate())
        v.act_stop.triggered.connect(self.stop_running)
        v.act_ask.triggered.connect(self.ask_claude)
        v.act_continue.triggered.connect(self.continue_claude)
        v.act_plot3d.triggered.connect(self.show_3d_plot)
        v.act_series_add.triggered.connect(self.series_add_dialog)
        v.act_series_remove.triggered.connect(self.series_remove)
        v.act_series_clear.triggered.connect(self.series_clear)
        v.act_side_west.triggered.connect(lambda: self.series_set_side(SIDE_WEST))
        v.act_side_east.triggered.connect(lambda: self.series_set_side(SIDE_EAST))
        v.act_side_auto.triggered.connect(lambda: self.series_set_side(None))
        v.act_series_run.triggered.connect(self.analyze_series)
        v.series_tree.itemClicked.connect(self.series_item_clicked)
        v.map_group.triggered.connect(lambda _: self.map_changed())
        v.source_combo.activated.connect(self.select_map_source)
        v.act_fit.triggered.connect(lambda: self._preview().fit())
        v.act_zoom_in.triggered.connect(lambda: self._zoom(1.25))
        v.act_zoom_out.triggered.connect(lambda: self._zoom(0.8))
        v.act_preferences.triggered.connect(self.preferences)
        v.act_reset.triggered.connect(self.reset_settings)
        v.optics_group.triggered.connect(lambda a: self.set_field("optics_type", a.data()))
        v.act_subtract_tracking.toggled.connect(lambda on: self.set_field("subtract_tracking", on))
        v.act_measure_asym.toggled.connect(lambda on: self.set_field("measure_asymmetry", on))
        v.act_handbook.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(HANDBOOK_URL)))
        v.act_reference.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(REFERENCE_URL)))
        v.act_about.triggered.connect(self.about)
        v.tabs.currentChanged.connect(lambda _: self.update_tab())
        v.files_dropped.connect(self.files_dropped)
        v.closing.connect(self.on_close)

    # ------------------------------------------------------------------
    # Helpers
    def settings(self) -> Settings:
        return self.s.copy()

    def log(self, level: str, text: str) -> None:
        self.v.console.append_line(level, text)

    def message(self, text: str, icon=QMessageBox.Icon.Information) -> None:
        QMessageBox(icon, TITLE, text, QMessageBox.StandardButton.Ok, self.v).exec()

    def status(self, text: str) -> None:
        self.v.statusBar().showMessage(text, 8000)

    def _preview(self) -> MapPreview:
        return self.v.series_preview if self.v.tabs.currentIndex() == TAB_SERIES else self.v.preview

    def _zoom(self, f: float) -> None:
        p = self._preview()
        p.scale(f, f)
        p.fitting = False

    def run(self, fn, done, label: str, exclusive: bool = True, on_message=None, failed=None) -> Worker | None:
        """Runs fn(log, progress, emit) in the background; done(result) in the
        GUI thread."""
        if exclusive and self.busy is not None:
            self.status("Please wait - another run is still going on (or Stop it).")
            return None
        w = Worker(fn)
        w.signals.log.connect(self.log)
        if exclusive:
            self.busy = w
            self.v.set_busy(True, label)
            w.signals.progress.connect(self.v.set_progress)
        if on_message:
            w.signals.message.connect(on_message)

        def finish(result=None, ok=True):
            self.workers.discard(w)
            if exclusive and self.busy is w:
                self.busy = None
                self.v.set_busy(False)
            if ok:
                done(result)

        def fail(text):
            finish(ok=False)
            (failed or (lambda t: self.message(t, QMessageBox.Icon.Critical)))(text)

        def cancelled():
            finish(ok=False)
            self.log("warning", "Stopped.")

        w.signals.finished.connect(lambda r: finish(r))
        w.signals.failed.connect(fail)
        w.signals.cancelled.connect(cancelled)
        self.workers.add(w)
        start(w)
        return w

    def stop_running(self) -> None:
        if self.busy is not None:
            self.busy.cancel()

    def on_close(self) -> None:
        for w in list(self.workers):
            w.cancel()
        self.store.save(self.s)
        self.store.save_ui_state({
            "recent": self.recent,
            "geometry": base64.b64encode(bytes(self.v.saveGeometry())).decode("ascii"),
            "window_state": base64.b64encode(bytes(self.v.saveState())).decode("ascii")})

    def about(self) -> None:
        QMessageBox.about(self.v, f"About {TITLE}",
                          f"<h3>{TITLE} {VERSION}</h3><p>Diagnostics of the optical system from the shapes of the "
                          "stars: sensor tilt, corrector spacing, field curvature, coma, collimation and tracking "
                          "errors.</p><p>A Python port of the PixInsight script StarAberrationDiagnostics.<br>"
                          f"<a href='{HANDBOOK_URL}'>Handbook</a></p><p>© 2026 Sven Kopetzki - GNU GPL v3 or "
                          "later</p>")

    # ------------------------------------------------------------------
    # Settings
    def field_changed(self, field: str) -> None:
        """A field was changed in a widget of the window or by a menu."""
        self.v.inspector.update_enabled(self.s)
        self._sync_settings_menu()
        if field in MEASUREMENT_FIELDS and self.analysis is not None and not self.analysis.file:
            self.preview_stale = not self._analysis_current()
        self.update_series_hints()
        self.redraw_timer.start()

    def set_field(self, field: str, value) -> None:
        if getattr(self.s, field) == value:
            return
        setattr(self.s, field, value)
        self.binder.load()
        self.field_changed(field)

    def _sync_settings_menu(self) -> None:
        v = self.v
        for a in v.optics_actions:
            a.setChecked(a.data() == self.s.optics_type)
        for a, field in ((v.act_subtract_tracking, "subtract_tracking"), (v.act_measure_asym, "measure_asymmetry")):
            a.blockSignals(True)
            a.setChecked(getattr(self.s, field))
            a.blockSignals(False)

    def preferences(self) -> None:
        k = self.store.api_key()
        dlg = PreferencesDialog(self.v, self.s, k[0] if k else "", bool(k and k[1] == "environment"),
                                self._header_info_html())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.key_edit.isEnabled():
            self.store.store_api_key(dlg.api_key)
        old = self.s
        self.s = dlg.settings
        self.store.save(self.s)
        self.binder.load()
        changed = [f for f in vars(self.s) if getattr(self.s, f) != getattr(old, f)]
        if any(f in MEASUREMENT_FIELDS for f in changed):
            self.preview_stale = self.analysis is not None and not self._analysis_current()
        self.update_image_info()
        self.field_changed("")

    def reset_settings(self) -> None:
        if QMessageBox.question(self.v, TITLE, "Reset all settings to their defaults?") != \
                QMessageBox.StandardButton.Yes:
            return
        self.s = Settings()
        self.store.save(self.s)
        self.binder.load()
        self.preview_stale = self.analysis is not None and not self._analysis_current()
        self.update_image_info()
        self.field_changed("")

    # ------------------------------------------------------------------
    # Images
    def _fill_recent_menu(self) -> None:
        m = self.v.recent_menu
        m.clear()
        for p in self.recent:
            a = m.addAction(Path(p).name)
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, p=p: self.open_image(p))
        m.addSeparator()
        clear = m.addAction("Clear Menu")
        clear.triggered.connect(lambda: (self.recent.clear(), self._fill_recent_menu()))
        m.setEnabled(bool(self.recent))

    def _add_recent(self, path: str) -> None:
        self.recent = [path] + [p for p in self.recent if p != path]
        del self.recent[MAX_RECENT:]
        self._fill_recent_menu()

    def open_image_dialog(self) -> None:
        start_dir = os.path.dirname(self.recent[0]) if self.recent else ""
        path, _ = QFileDialog.getOpenFileName(self.v, "Open Image", start_dir, IMAGE_FILTER)
        if path:
            self.open_image(path)

    def open_image(self, path: str) -> None:
        def work(log, progress, emit):
            progress.step("Loading the image", 0, 0.6)
            img = load_image(path)
            progress.step("Stretching for display", 0.6, 1)
            return img, stretched_rgb8(img)

        def done(res):
            img, rgb = res
            self.target = img
            self.target_bitmap = image_from_rgb(rgb)
            self._add_recent(path)
            self.log("text", f"Opened: {path} ({img.width} x {img.height}, {'color' if img.is_color else 'mono'})")
            self.v.setWindowFilePath(path)
            self.v.setWindowTitle(f"{img.name} - {TITLE}")
            self.preview_stale = self.analysis is not None and not self._analysis_current()
            self.map_source = 0
            self._fill_sources()
            self.update_image_info()
            self.v.tabs.setCurrentIndex(0)
            self.refresh_preview()

        def failed(text):
            if not os.path.exists(path):
                self.recent = [p for p in self.recent if p != path]
                self._fill_recent_menu()
            self.message(f"Cannot open {Path(path).name}: {text}", QMessageBox.Icon.Critical)

        self.run(work, done, "Loading the image", failed=failed)

    def close_image(self) -> None:
        self.target = None
        self.target_bitmap = None
        self.analysis = None
        self.preview_stale = False
        self.v.setWindowTitle(f"{TITLE} {VERSION}")
        self.clear_ai_review()
        self.update_image_info()
        self.refresh_preview()

    def files_dropped(self, paths: list[str]) -> None:
        images = [p for p in paths if Path(p).suffix.lower() in SUPPORTED_EXTENSIONS]
        results = [p for p in paths if p.lower().endswith(".json")]
        if results:
            self.load_results(results[0])
        elif len(images) > 1 or (images and self.v.tabs.currentIndex() == TAB_SERIES):
            self.series_add(images)
            self.v.tabs.setCurrentIndex(TAB_SERIES)
        elif images:
            self.open_image(images[0])

    def _analysis_current(self) -> bool:
        a = self.analysis
        if a is None:
            return False
        if a.file:
            return True
        if self.target is None:
            return False
        return a.key == analysis_key(self.target.name, self.target.path, self.s)

    def _keywords(self) -> list:
        if self.target is not None:
            return self.target.keywords
        if self.analysis is not None:
            return self.analysis.header.get("keywords", [])
        return []

    def _header_info_html(self) -> str:
        kw = self._keywords()
        if not kw and self.target is None:
            return ""
        hdr = read_optics_from_header(kw)
        values = keyword_values(kw)
        mount = estimate_mount(values)
        lines = [f"Pixel pitch: {hdr['pixel_pitch_um']:.2f} µm" if hdr["pixel_pitch_um"] else "Pixel pitch: -",
                 f"Focal length: {hdr['focal_length_mm']:.0f} mm" if hdr["focal_length_mm"] else "Focal length: -",
                 f"Aperture: {hdr['aperture_mm']:.0f} mm ({hdr['sources']['aperture_mm']})" if hdr["aperture_mm"]
                 else "Aperture: -",
                 f"Camera: {html.escape(read_camera_from_header(kw)) or '-'}",
                 f"Mount: {MOUNT_TYPES[mount['type']]}" + (f" (from {html.escape(mount['source'])})"
                                                           if mount["source"] else "")]
        return "<br>".join(lines)

    def update_image_info(self) -> None:
        v = self.v
        img = self.target
        a = self.analysis
        if img is None and a is None:
            v.inspector.image_label.setText("<i>No image open - File › Open Image…</i>")
            v.inspector.tracking_label.setText("")
            return
        lines = []
        if img is not None:
            lines.append(f"<b>{html.escape(img.name)}</b> - {img.width} x {img.height} px, "
                         f"{'color' if img.is_color else 'mono'}")
        if a is not None and a.file:
            lines.append(f"<i>Loaded results: {html.escape(Path(a.file).name)} ({len(a.stars)} stars)</i>")
        optics = resolve_optics(self._keywords(), self.s, a.debayer if a else False)
        o = []
        if optics.pixel_pitch_um:
            o.append(f"{optics.pixel_pitch_um:.2f} µm")
        if optics.focal_length_mm:
            o.append(f"{optics.focal_length_mm:.0f} mm")
        if optics.f_ratio:
            o.append(f"f/{optics.f_ratio:.1f}")
        src = "header" if self.s.optics_from_header else "settings"
        lines.append("Optics: " + (", ".join(o) + f" ({src})" if o else f"not set ({src})"))
        cam = read_camera_from_header(self._keywords()) if self.s.optics_from_header else ""
        if cam:
            lines.append(f"Camera: {html.escape(cam)}")
        m = optics.mount
        lines.append(f"Mount: {MOUNT_TYPES[m['type']]}" + (f" (from {html.escape(m['source'])})"
                                                           if m["source"] and m["source"] != "dialog" else ""))
        g = optics.guiding
        lines.append(f"Guiding: {GUIDING_TYPES[g['type']]}" + (f" (from {html.escape(g['source'])})"
                                                               if g["source"] and g["source"] != "dialog" else ""))
        lines.append(f"Optics type: {html.escape(OPTICS_TYPES[self.s.optics_type])}")
        if self.preview_stale:
            lines.append("<span style='color:#d18616'>Measurement settings changed - Calculate again.</span>")
        v.inspector.image_label.setText("<br>".join(lines))

    def optics_for(self, a: Analysis, s: Settings):
        if getattr(a, "optics", None) is not None:
            return a.optics
        return resolve_optics(a.header.get("keywords", []), s, a.debayer)

    # ------------------------------------------------------------------
    # Maps, evaluations, assessment
    def map_changed(self) -> None:
        self.v.inspector.show_map(self.v.current_map_key())
        self.refresh_preview()

    def update_tab(self) -> None:
        tab = self.v.tabs.currentIndex()
        if tab == TAB_SERIES and self.series_result is None:
            self.v.series_tabs.setCurrentIndex(0)

    def _fill_sources(self) -> None:
        labels = ["Target image (Calculate)"] + [
            f"Series static: {e['label']} ({e['analysis'].series['frames']} frames)" for e in self.series_static]
        self.map_source = min(self.map_source, len(self.series_static))
        c = self.v.source_combo
        c.blockSignals(True)
        c.clear()
        c.addItems(labels)
        c.setCurrentIndex(self.map_source)
        c.blockSignals(False)
        m = self.v.source_menu
        m.clear()
        for i, label in enumerate(labels):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(i == self.map_source)
            a.triggered.connect(lambda _=False, i=i: self.select_map_source(i))

    def select_map_source(self, index: int) -> None:
        self.map_source = index
        self._fill_sources()
        self.v.tabs.setCurrentIndex(0)
        self.refresh_preview()

    def current_analysis(self) -> Analysis | None:
        if self.map_source > 0 and self.map_source - 1 < len(self.series_static):
            return self.series_static[self.map_source - 1]["analysis"]
        if self.analysis is not None and not self.preview_stale:
            return self.analysis
        return None

    def refresh_preview(self) -> None:
        a = self.current_analysis()
        self.update_evaluations()
        self.update_assessment()
        self.update_image_info()
        if a is None:
            self.v.show_map_view(self.target_bitmap is not None)
            caption = ""
            if self.target is not None:
                caption = self.target.name + (" - settings changed: Calculate again" if self.preview_stale
                                              else " - not measured yet: Analysis › Calculate")
            self.v.preview.set_image(self.target_bitmap, caption)
            return
        self.v.show_map_view(True)
        s = self.settings()
        key = self.v.current_map_key()
        self.render_gen += 1
        gen = self.render_gen
        optics = self.optics_for(a, s)

        def done(img):
            if gen == self.render_gen:
                self.v.preview.set_image(img, f"{a.target_name} - {map_view_by_key(key).label}")
        self.run(lambda log, progress, emit: render_map(a, s, optics, key), done, "", exclusive=False,
                 failed=lambda t: self.log("error", "Drawing the map failed: " + t))

    def update_evaluations(self) -> None:
        a = self.current_analysis()
        insp = self.v.inspector
        if a is None:
            insp.set_evaluations(None)
            insp.tracking_label.setText("")
            return
        s = self.s
        optics = self.optics_for(a, s)
        stars = stars_for(a, s.subtract_tracking)
        scale, unit = fwhm_display_unit(s, optics)
        ev = {"size": evaluation_html(format_fwhm_grid_evaluation(evaluate_fwhm_grid(stars, a.w, a.h, FWHM_GRID_SIZE),
                                                                  scale, unit))}
        static = format_static_series(self.series_static[self.map_source - 1]) if self.map_source > 0 else []
        ev["shape"] = evaluation_html(static + format_shape_evaluation(shape_evaluation_for(a, s), a.tracking,
                                                                       s.subtract_tracking))
        tilt = compute_siril_tilt(stars, a.w, a.h)
        axis = compute_tilt_axis(tilt, a.w, a.h, optics.pixel_pitch_um, optics.focal_length_mm,
                                 optics.aperture_mm) if tilt else None
        ev["tilt"] = evaluation_html(format_tilt_evaluation(tilt, axis, scale, unit))
        if a.has_asymmetry:
            lines: list[str] = []
            log_coma_field(a.coma_fit, a.coma_fit_centroid, a.w, a.h, optics.pixel_pitch_um,
                           Log(lambda level, t: lines.append(t)))
            ev["coma"] = evaluation_html([l for l in lines if not l.startswith("---")])
        else:
            ev["coma"] = "<i>PSF asymmetry not measured.</i>"
        insp.set_evaluations(ev)
        t = a.tracking
        insp.tracking_label.setText(
            f"Common elongation: <b>{t['ellipticity']:.3f}</b> along {t['angle_deg']:.0f}° - " +
            ("negligible, tracking/guiding looks clean." if t["ellipticity"] < 0.03
             else "shared by all stars: tracking/guiding, wind or flexure."))

    def update_assessment(self) -> None:
        a = self.analysis if not self.preview_stale else None
        if a is None:
            self.v.assessment_box.setHtml("<i>Analysis › Calculate assesses the image.</i>")
            return
        s = self.s
        rep = report_analysis(a, stars_for(a, s.subtract_tracking), s, self.optics_for(a, s), SILENT)
        self.v.assessment_box.setHtml(format_assessment_html(assess_image(rep["metrics"], s.optics_type)))

    # ------------------------------------------------------------------
    # Calculate and export
    def calculate(self, then=None) -> None:
        if self.target is None:
            if self.analysis is not None and self.analysis.file:
                self.refresh_preview()
                return
            self.message("Open an image first (File › Open Image…).")
            return
        s = self.settings()
        self.store.save(s)
        img = self.target
        reuse = self.analysis if self._analysis_current() and not self.analysis.file else None
        key = self.v.current_map_key()
        self.log("text", f"Calculate: {img.name}")

        def work(log, progress, emit):
            t0 = time.monotonic()
            a = reuse
            seconds = None
            if a is None:
                a = analyze_image(img, s, True, log, progress)
                if a is None:
                    raise ValueError("No stars could be measured (see the console).")
                seconds = time.monotonic() - t0
            else:
                log.write("Reusing the star measurement.")
            optics = resolve_optics(img.keywords, s, a.debayer, log)
            progress.step("Evaluating", 0.85, 0.9)
            report_analysis(a, stars_for(a, s.subtract_tracking), s, optics, log, verbose=True)
            progress.step("Drawing the map", 0.9, 1)
            m = render_map(a, s, optics, key, log)
            log.write(f"Done in {time.monotonic() - t0:.1f} s.")
            return a, m, seconds

        def done(res):
            a, m, seconds = res
            self.analysis = a
            self.preview_stale = False
            self.map_source = 0
            self._fill_sources()
            if seconds is not None:
                self.frame_timing = {"seconds": seconds, "source": "calculate", "maps": False}
            self.clear_ai_review()
            self.v.show_map_view(True)
            self.v.preview.set_image(m, f"{a.target_name} - {map_view_by_key(key).label}")
            self.update_evaluations()
            self.update_assessment()
            self.update_image_info()
            self.update_series_hints()
            self.status(f"{len(a.stars)} stars measured.")
            if then:
                then()

        self.run(work, done, "Calculate")

    def _export_base(self, a: Analysis) -> str | None:
        path = a.header.get("path") or ""
        default = str(Path(path).with_suffix("")) if path else os.path.join(os.path.expanduser("~"),
                                                                            a.header.get("name") or "image")
        chosen, _ = QFileDialog.getSaveFileName(self.v, "Export Maps", default + "_Size.png",
                                                "PNG images (*.png)")
        if not chosen:
            return None
        base = chosen[:-4] if chosen.lower().endswith(".png") else chosen
        for mv in MAP_VIEWS:
            if base.endswith(mv.file_suffix[:-4]):
                base = base[:-len(mv.file_suffix) + 4]
        return base

    def export_maps(self) -> None:
        """The four maps, the 3D plot and the star table, named
        <base>_Size.png ... next to each other."""
        if self.analysis is None and self.target is None:
            self.message("Open an image first (File › Open Image…).")
            return
        if not self._analysis_current():
            self.calculate(then=self.export_maps)
            return
        a = self.analysis
        base = self._export_base(a)
        if base is None:
            return
        s = self.settings()

        def work(log, progress, emit):
            optics = self.optics_for(a, s)
            stars = stars_for(a, s.subtract_tracking)
            for i, mv in enumerate(MAP_VIEWS):
                progress.step(f"Drawing {mv.label}", i / 5, (i + 1) / 5)
                p = base + mv.file_suffix
                save_png(render_map(a, s, optics, mv.key, log), p)
                log.note(f"Saved: {p}")
            tilt = compute_siril_tilt(stars, a.w, a.h)
            if s.show_3d_tilt_plot:
                if not optics.physical:
                    log.warning("3D sensor tilt plot skipped: it needs pixel pitch, focal length and aperture "
                                "(Settings › Preferences › Optics, or the FITS header).")
                elif tilt is not None:
                    p = base + "_Aberration3D.png"
                    save_png(render_3d_tilt_plot(tilt, a.w, a.h, optics.pixel_pitch_um, optics.focal_length_mm,
                                                 optics.aperture_mm), p)
                    log.note(f"Saved: {p}")
            if s.do_export:
                p = base + "_aberration.csv"
                export_csv(stars, p)
                log.note(f"CSV exported to: {p}")
            return base

        self.run(work, lambda b: self.status(f"Maps exported: {Path(b).name}_…"), "Export maps")

    def export_star_table(self) -> None:
        a = self.current_analysis()
        if a is None:
            self.message("Calculate first: there is no measurement to export.")
            return
        path = a.header.get("path") or ""
        default = str(Path(path).with_suffix("")) + "_aberration.csv" if path else "stars_aberration.csv"
        out, _ = QFileDialog.getSaveFileName(self.v, "Export Star Table", default, "CSV files (*.csv)")
        if out:
            export_csv(stars_for(a, self.s.subtract_tracking), out)
            self.log("note", f"CSV exported to: {out}")

    def show_3d_plot(self) -> None:
        a = self.current_analysis()
        if a is None:
            self.message("Calculate first.")
            return
        optics = self.optics_for(a, self.s)
        if not optics.physical:
            self.message("The 3D tilt plot needs pixel pitch, focal length and aperture - from the FITS header or "
                         "Settings › Preferences › Optics.")
            return
        tilt = compute_siril_tilt(stars_for(a, self.s.subtract_tracking), a.w, a.h)
        if tilt is None:
            self.message("Not every quadrant and ring has stars - no tilt evaluation.")
            return
        img = render_3d_tilt_plot(tilt, a.w, a.h, optics.pixel_pitch_um, optics.focal_length_mm, optics.aperture_mm)
        dlg = QDialog(self.v)
        dlg.setWindowTitle(f"3D Tilt Plot - {a.target_name}")
        view = MapPreview()
        view.set_image(img)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)

        def save():
            p, _ = QFileDialog.getSaveFileName(dlg, "Save 3D Tilt Plot", "Aberration3D.png", "PNG images (*.png)")
            if p:
                save_png(img, p)
        buttons.accepted.connect(save)
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        lay.addWidget(buttons)
        dlg.resize(1040, 820)
        dlg.show()

    # ------------------------------------------------------------------
    # Results files
    def save_results(self) -> None:
        if self.analysis is None:
            self.message("Calculate first: there are no results to save.")
            return
        initial = self.analysis.file or results_default_path(self.analysis.header)
        path, _ = QFileDialog.getSaveFileName(self.v, "Save Results", initial, "Results files (*.json)")
        if not path:
            return
        try:
            save_results(self.analysis, self.s, path)
            self.log("note", f"Results saved ({len(self.analysis.stars)} stars): {path}")
            self.status("Results saved.")
        except OSError as e:
            self.message(f"Saving the results failed: {e}", QMessageBox.Icon.Critical)

    def load_results(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self.v, "Load Results", "",
                                                  "Results files (*_aberration.json *.json);;All files (*)")
        if not path:
            return

        def background_for(header, w, h, debayer):
            img = self.target if self.target and header.get("path") and self.target.path == header["path"] else None
            if img is None or (img.width // (2 if debayer else 1), img.height // (2 if debayer else 1)) != (w, h):
                return None
            rgb = stretched_rgb8(img)
            return rgb[::2, ::2][:h, :w] if debayer else rgb

        try:
            a = load_results(path, background_for)
        except ValueError as e:
            self.message(str(e), QMessageBox.Icon.Critical)
            return
        self.log("note", f"Results loaded ({len(a.stars)} stars of {a.target_name}, {a.w} x {a.h} px): {path}")
        self.analysis = a
        self.preview_stale = False
        self.map_source = 0
        self._fill_sources()
        self.clear_ai_review()
        self.v.tabs.setCurrentIndex(0)
        self.refresh_preview()

    # ------------------------------------------------------------------
    # AI review
    def set_ai_text(self, html_text: str) -> None:
        self.v.ai_box.setHtml(html_text)
        self.v.series_ai_box.setHtml(html_text)

    def clear_ai_review(self) -> None:
        self.set_ai_text("<i>Analysis › Ask Claude gives a second opinion on the assessment of a Calculate, on the "
                         "comparison of a series, or both.</i>")
        self.ai_pending = None
        self.v.act_continue.setEnabled(False)
        self.v.ai_continue_button.setEnabled(False)

    def ask_claude(self) -> None:
        have_single = self.analysis is not None and not self.preview_stale
        have_series = self.series_result is not None and self.series_result["measured"] > 0
        if not have_single and not have_series:
            self.message("Calculate an image, or analyze a series, first: the review needs a current analysis.")
            return
        k = self.store.api_key()
        if k is None:
            self.message("Enter an Anthropic API key first (Settings › Preferences › AI review), or set the "
                         "environment variable ANTHROPIC_API_KEY.")
            return
        s = self.settings()
        a = self.analysis
        frames, result = list(self.series_frames), self.series_result
        ctx = {"s": s, "have_single": have_single, "have_series": have_series, "message": None}
        self.set_ai_text("<i>Waiting for Claude's answer - this can take a minute ...</i>")
        self.v.tabs.setCurrentIndex(TAB_SERIES if self.v.tabs.currentIndex() == TAB_SERIES else TAB_ASSESSMENT)
        if self.v.tabs.currentIndex() == TAB_SERIES:
            self.v.series_tabs.setCurrentIndex(3)
        self.ai_pending = None
        self.v.act_continue.setEnabled(False)
        self.v.ai_continue_button.setEnabled(False)

        def work(log, progress, emit):
            single = maps = None
            if have_single:
                progress.step("Preparing the data", 0, 0.1)
                optics = self.optics_for(a, s)
                stars = stars_for(a, s.subtract_tracking)
                rep = report_analysis(a, stars, s, optics, SILENT)
                single = {"analysis": a, "stars": stars, "metrics": rep["metrics"], "optics": optics,
                          "assessment": assess_image(rep["metrics"], s.optics_type)}
                if s.ai_send_map:
                    maps = []
                    for mv in MAP_VIEWS:
                        if mv.ai:
                            progress.step(f"Drawing {mv.label}", 0.1, 0.2)
                            maps.append({"label": mv.label, "png": png_bytes(render_map(a, s, optics, mv.key),
                                                                             AI_MAP_MAX_SIDE)})
            ctx["message"] = build_message(s, single, {"frames": frames, "result": result} if have_series else None,
                                           maps)
            progress.step("Waiting for Claude's answer", 0.2, 1)
            return self._request(k[0], ctx["message"], None, progress)

        self.run(work, lambda r: self.show_ai_review(r, ctx), "Ask Claude",
                 failed=lambda t: self.show_ai_review({"error": t}, ctx))

    def _request(self, key: str, message: dict, previous: Review | None, progress) -> Review | dict:
        try:
            return request_review(key, message, previous,
                                  on_progress=lambda n: progress.within(min(0.95, n / 6000)))
        except ReviewError as e:
            return {"error": str(e)}

    def continue_claude(self) -> None:
        if self.ai_pending is None:
            return
        k = self.store.api_key()
        if k is None:
            self.message("Enter an Anthropic API key first (Settings › Preferences › AI review).")
            return
        review, message, ctx = self.ai_pending
        self.set_ai_text("<i>Waiting for the rest of Claude's answer - this can take a minute ...</i>")
        self.v.act_continue.setEnabled(False)
        self.v.ai_continue_button.setEnabled(False)

        def done(r):
            if isinstance(r, dict):
                self.set_ai_text(f"<b>Continuing the AI review failed:</b> {html.escape(r['error'])}<br><br>"
                                 + format_review_html(review))
                self.log("error", "Continuing the AI review failed: " + r["error"])
                self.v.act_continue.setEnabled(True)
                self.v.ai_continue_button.setEnabled(True)
                return
            self.show_ai_review(r, ctx)

        self.run(lambda log, progress, emit: self._request(k[0], message, review, progress), done, "Continue")

    def show_ai_review(self, review, ctx: dict) -> None:
        s = ctx["s"]
        error = review.get("error") if isinstance(review, dict) else None
        if s.ai_save_log and ctx["message"]:
            files: list[tuple[str, str]] = []
            target_path = self.analysis.header.get("path", "") if ctx["have_single"] and self.analysis else ""
            if ctx["have_single"] and self.analysis:
                files.append(("Single frame (Calculate):", target_path or
                              f"{self.analysis.target_name} (not saved to a file)"))
            if ctx["have_series"]:
                files.append((f"Series ({len(self.series_frames)} frames, side, file):", ""))
                for f in self.series_frames:
                    files.append((f"   {(f.side + '     ')[:6]}", f.path + ("" if f.metrics else
                                  "  (not measured" + (f": {f.error}" if f.error else "") + ")")))
            settings = [f"Settings: optics type {OPTICS_TYPES[s.optics_type]}; explanation "
                        f"{AI_DETAIL_LEVELS[s.ai_detail]}; language {s.ai_language}; maps "
                        f"{'attached' if ctx['have_single'] and s.ai_send_map else 'not attached'}"]
            if not error and review.parts > 1:
                settings.append(f"Answer in {review.parts} parts: continued after the token limit was reached.")
            directory = (os.path.dirname(self.series_frames[0].path) if ctx["have_series"] and self.series_frames
                         else os.path.dirname(target_path) if target_path else tempfile.gettempdir())
            try:
                p = save_ai_log(directory, _dt.datetime.now(_dt.timezone.utc), files, settings, ctx["message"],
                                None if error else review, error)
                self.log("note", f"AI request and answer saved to: {p}")
            except OSError as e:
                self.log("warning", f"Cannot save the AI request and answer: {e}")
        if error:
            self.set_ai_text(f"<b>The AI review failed:</b> {html.escape(error)}")
            self.log("error", "AI review failed: " + error)
            return
        self.set_ai_text(format_review_html(review))
        self.log("note", f"--- AI review ({review.model}) ---")
        self.log("text", format_review_text(review))
        if review.truncated and review.raw_text.strip():
            self.ai_pending = (review, ctx["message"], ctx)
            self.v.act_continue.setEnabled(True)
            self.v.ai_continue_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Series
    def update_series_hints(self) -> None:
        s = self.s
        frames = self.series_frames
        directory = f"<i>{html.escape(os.path.dirname(frames[0].path))}</i>" if frames else \
            "the directory of the first frame"
        items = [f"<b>StarAberrationSeries.csv</b> - one line per frame, in {directory}" if s.do_export else
                 "No CSV table - <i>Export the star table</i> is off (Preferences › Output)"]
        if s.series_save_maps:
            items.append("Next to each frame: <b><i>name</i>_Size.png, _Shape.png, _Coma.png, _Tilt.png</b>" +
                         (" and <b>_Aberration3D.png</b> (with pixel pitch, focal length and aperture)"
                          if s.show_3d_tilt_plot else ""))
            items.append(f"After pass 2, in {directory}: <b>SeriesStatic_all_Size.png ... _Tilt.png</b>, and the "
                         "same per side (e.g. SeriesStatic_West_...)")
        else:
            items.append("No map images - <i>Save maps next to the frames</i> is off (Preferences › Output)")
        items.append("Next to each frame: <b><i>name</i>_Mosaic.png</b>" if s.series_save_mosaic else
                     "No mosaics - <i>Save a 3x3 mosaic next to the frames</i> is off (Preferences › Output)")
        if s.ai_save_log:
            items.append(f"Ask Claude: <b>StarAberrationAI_<i>date</i>_<i>time</i>.txt</b> in {directory}")
        items.append("Files of an earlier run are replaced; the frames themselves are never changed")

        def section(title, lines):
            return (f"<p style='margin-top:10px; margin-bottom:2px;'><b>{title}</b></p><ul style='margin-top:0px;'>"
                    + "".join(f"<li>{t}</li>" for t in lines) + "</ul>")

        self.v.series_hints.setText(
            "<h3>Series analysis: before you start</h3>" + series_hints_status(frames, self.frame_timing, s)
            .replace("Add files... on the right", "Series › Add Frames… or by dropping them here")
            .replace("with West / East", "with Series › Set Side") +
            section("Files written", items) +
            section("How many frames", [
                "At least <b>3 per side</b> of the flip - with fewer, the sides are not compared",
                "Better <b>5-10 per side</b>: the uncertainty shrinks with √n (4 frames: half, 9: a third, 16: a "
                "quarter of a single frame's)", "Beyond about 15 per side, more frames gain little"]) +
            section("Before and after the meridian flip", [
                "Frames from <b>both sides</b>: after the flip gravity pulls on the optical train from the other side",
                "What changes with the side is mechanical - sag, play, mirror flop, focuser, a flexing guide scope; "
                "what stays is fixed in the optics - sensor tilt, spacing, collimation",
                "Best the last frames before and the first after the flip: similar altitude and temperature, so the "
                "flip is the only change",
                "Without a flip, the series still shows the static optics and the drift over the session"]) +
            section("The frames", [
                "Unregistered, uncropped light frames of one night and one optical train - the image center must be "
                "the optical axis",
                "Same filter and exposure; leave out frames with clouds, wind gusts or bad guiding",
                "Check the settings first with Calculate on one frame: are there enough stars up to the corners?",
                "The side comes from PIERSIDE (N.I.N.A., ASCOM), the camera angle (ASIAIR) or the hour angle: check "
                "the Side column, and set it with Series › Set Side where it is ?"]))

    def fill_series_tree(self) -> None:
        t = self.v.series_tree
        selected = {id(f) for f in self.selected_frames()}
        t.clear()
        for f in self.series_frames:
            m = f.metrics
            tilt = series_metric("tilt").get(m) if m else None
            cells = [f.name, f.side, f.side_source, f"{f.ha_h:+.2f}" if f.ha_h is not None else "",
                     f"{f.alt_deg:.0f}°" if f.alt_deg is not None else "",
                     f"{f.focus_pos:.0f}" if f.focus_pos is not None else "",
                     str(m["n"]) if m else ("failed" if f.error or f.header_error else ""),
                     f"{m['surface']['f_center']:.2f}" if m and m.get("surface") else "",
                     f"{100 * math.hypot(*tilt):.0f}% {direction_name(math.degrees(math.atan2(tilt[1], tilt[0])))}"
                     if tilt else "",
                     f"{m['rings']['e_rad']:+.3f}" if m and m.get("rings") and m["rings"]["e_rad"] is not None else "",
                     f"{m['tracking']['ellipticity']:.3f}" if m and m.get("tracking") else "",
                     f"{m['coma']['k']:+.3f}" if m and m.get("coma") else "",
                     f"{sip_max_shift(sip_corner_shifts(f)):.1f} px" if f.sip else ""]
            item = QTreeWidgetItem(cells)
            item.setToolTip(0, f.path)
            if f.error or f.header_error:
                item.setToolTip(6, f.error or f.header_error)
            t.addTopLevelItem(item)
            item.setSelected(id(f) in selected)

    def selected_frames(self) -> list:
        t = self.v.series_tree
        return [self.series_frames[t.indexOfTopLevelItem(i)] for i in t.selectedItems()
                if 0 <= t.indexOfTopLevelItem(i) < len(self.series_frames)]

    def _series_changed(self, measured_too: bool) -> None:
        assign_series_sides(self.series_frames)
        if measured_too:
            self.series_result = None
            self.series_static = []
            self._fill_sources()
            self.v.series_tabs.setCurrentIndex(0)
        elif self.series_result is not None:
            static, stop = self.series_result.get("static", []), self.series_result.get("stop")
            self.series_result = compare_series(self.series_frames)
            self.series_result["static"], self.series_result["stop"] = static, stop
            self.v.series_box.setHtml(format_series_report(self.series_result))
        self.fill_series_tree()
        self.update_series_hints()

    def series_add_dialog(self) -> None:
        start_dir = os.path.dirname(self.series_frames[-1].path) if self.series_frames else (
            os.path.dirname(self.recent[0]) if self.recent else "")
        paths, _ = QFileDialog.getOpenFileNames(self.v, "Add Light Frames", start_dir, IMAGE_FILTER)
        if paths:
            self.series_add(paths)
            self.v.tabs.setCurrentIndex(TAB_SERIES)

    def series_add(self, paths: list[str]) -> None:
        known = {f.path for f in self.series_frames}
        for p in paths:
            if p not in known:
                self.series_frames.append(read_series_frame(p))
        self.series_frames.sort(key=lambda f: (f.time_ms is None, f.time_ms or 0, f.name))
        self._series_changed(True)

    def series_remove(self) -> None:
        sel = {id(f) for f in self.selected_frames()}
        self.series_frames = [f for f in self.series_frames if id(f) not in sel]
        self._series_changed(True)

    def series_clear(self) -> None:
        self.series_frames = []
        self._series_changed(True)

    def series_set_side(self, side: str | None) -> None:
        if not self.selected_frames():
            self.status("Select frames in the series list first.")
            return
        for f in self.selected_frames():
            f.manual_side = side
        self._series_changed(False)

    def series_item_clicked(self, item, _col) -> None:
        i = self.v.series_tree.indexOfTopLevelItem(item)
        if 0 <= i < len(self.series_frames):
            f = self.series_frames[i]
            if f.map_image is not None:
                self.v.series_tabs.setCurrentIndex(1)
                self.v.series_preview.set_image(f.map_image, f"{f.name} - {map_view_by_key(f.map_key).label}")

    def _series_running(self, running: bool) -> None:
        self.series_running = running
        text = "Stop" if running else "Analyze Series"
        self.v.series_run_button.setText(text)
        self.v.act_series_run.setText("&Stop the Series" if running else "Analyze &Series")
        self.v.series_run_button.setEnabled(True)
        self.v.act_series_run.setEnabled(True)

    def analyze_series(self) -> None:
        if self.series_running:
            self.stop_running()
            return
        if not self.series_frames:
            self.message("Add the light frames of the series first (Series › Add Frames…).")
            return
        s = self.settings()
        self.store.save(s)
        frames = self.series_frames
        key = self.v.current_map_key()
        with_maps = s.series_show_maps or s.series_save_maps or s.series_save_mosaic
        self.v.tabs.setCurrentIndex(TAB_SERIES)
        self.v.series_tabs.setCurrentIndex(1)
        t_start = time.monotonic()
        holder: dict = {}

        def on_frame(f, a, optics, i, report):
            if s.series_show_maps:
                f.map_image = scaled_to_max_side(render_map(a, s, optics, key), 1600)
                f.map_key = key
            if s.series_save_maps:
                for mv in MAP_VIEWS:
                    p = frame_output_path(f, mv.file_suffix)
                    save_png(render_map(a, s, optics, mv.key), p)
                    f.output_files.append(p)
                if s.show_3d_tilt_plot and optics.physical and report["tilt"] is not None:
                    p = frame_output_path(f, "_Aberration3D.png")
                    save_png(render_3d_tilt_plot(report["tilt"], a.w, a.h, optics.pixel_pitch_um,
                                                 optics.focal_length_mm, optics.aperture_mm), p)
                    f.output_files.append(p)
            if s.series_save_mosaic and a.background is not None:
                p = frame_output_path(f, "_Mosaic.png")
                save_png(render_mosaic(a.background), p)
                f.output_files.append(p)

        def work(log, progress, emit):
            def frame_done(f, a, optics, i, report):
                if with_maps:
                    on_frame(f, a, optics, i, report)
                emit(f)
            progress.step("Measuring the frames", 0, 0.9)
            run = measure_series(frames, s, log, progress, lambda: "w" in holder and holder["w"].is_cancelled,
                                 frame_done)
            result = compare_series(frames)
            result["stop"] = {"after": run["after"]} if run["stopped"] else None
            static = []
            if not run["stopped"]:
                progress.step("Pass 2: pooling the frames", 0.9, 1)
                static = build_static_series(frames, log)
                for e in static:
                    for line in format_static_series(e):
                        log.write(line)
                    if s.series_save_maps:
                        d = os.path.dirname(frames[0].path)
                        a = e["analysis"]
                        for mv in MAP_VIEWS:
                            p = os.path.join(d, f"SeriesStatic_{e['label'].replace(' ', '')}{mv.file_suffix}")
                            save_png(render_map(a, s, a.optics, mv.key), p)
                            log.write(f"Saved: {p}")
            result["static"] = static
            if s.do_export:
                p = os.path.join(os.path.dirname(frames[0].path), "StarAberrationSeries.csv")
                export_series_csv(frames, p)
                log.note(f"Series CSV exported to: {p}")
            return result

        def frame_message(f):
            self.fill_series_tree()
            if f.map_image is not None:
                self.v.series_preview.set_image(f.map_image, f"{f.name} - {map_view_by_key(f.map_key).label}")

        def done(result):
            self._series_running(False)
            measured = sum(1 for f in frames if f.metrics)
            if measured:
                self.frame_timing = {"seconds": (time.monotonic() - t_start) / measured, "source": "series",
                                     "maps": with_maps}
            self.series_result = result
            self.series_static = result["static"]
            self._fill_sources()
            self.fill_series_tree()
            self.v.series_box.setHtml(format_series_report(result))
            self.v.series_tabs.setCurrentIndex(2)
            self.clear_ai_review()
            self.log("note", f"Series done in {time.monotonic() - t_start:.1f} s.")
            self.status(f"Series: {measured} of {len(frames)} frames measured.")
            self.update_series_hints()

        worker = self.run(work, done, "Analyze series", on_message=frame_message,
                          failed=lambda t: (self._series_running(False),
                                            self.message(t, QMessageBox.Icon.Critical)))
        if worker is None:
            return
        holder["w"] = worker
        worker.signals.cancelled.connect(lambda: self._series_running(False))
        self._series_running(True)
