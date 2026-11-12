"""The controller: holds the state (target image, analysis, series, pending
AI answer), reacts to the view and runs the work in background threads."""

from __future__ import annotations

import datetime as _dt
import html
import math
import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox, QTreeWidgetItem

from .. import TITLE
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
from ..model.image import ImageData, load_image, stretched_rgb8
from ..model.log import SILENT, Log
from ..model.optics import (GUIDING_UNKNOWN, effective_guiding, estimate_mount, keyword_values,
                            read_camera_from_header, read_optics_from_header, resolve_optics)
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
from ..view.main_window import TIPS_ITEM, MainWindow
from ..view.widgets import evaluation_html
from .worker import Worker, start

DOC_URL = "https://tricx.de/pi-scripts/StarAberrationDiagnostics/"
CALC_HINT = "<i>Click Calculate to evaluate the image.</i>"


class MainController:
    def __init__(self, view: MainWindow, store: SettingsStore):
        self.v = view
        self.store = store
        self.images: list[ImageData] = []
        self.target: ImageData | None = None
        self.target_bitmap: QImage | None = None
        self.analysis: Analysis | None = None
        self.preview_stale = False
        self.map_source = 0
        self.series_frames = []
        self.series_result = None
        self.series_static: list[dict] = []
        self.series_running = False
        self.series_shown = False
        self.frame_timing = None
        self.ai_pending: tuple[Review, dict, dict] | None = None
        self.busy: Worker | None = None
        self.render_worker: Worker | None = None
        self.render_gen = 0
        self.workers: set[Worker] = set()

        s = store.load()
        view.manual_optics = (s.pixel_pitch_um, s.focal_length_mm, s.aperture_mm)
        view.manual_mount = s.mount_type
        view.apply_settings(s)
        self._fill_target_combo()
        self._update_ai_key_info()
        self.clear_ai_review()
        self._update_header_fields()
        self.update_series_hints()
        state = store.load_ui_state()
        if "splitter" in state:
            view.splitter.setSizes(state["splitter"])

        self.redraw_timer = QTimer()
        self.redraw_timer.setSingleShot(True)
        self.redraw_timer.setInterval(150)
        self.redraw_timer.timeout.connect(self.refresh_preview)
        self._connect()
        self.update_left()

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        v = self.v
        v.open_button.clicked.connect(self.open_image)
        v.target_combo.activated.connect(self.select_target_index)
        v.preview_button.clicked.connect(self.calculate)
        v.apply_button.clicked.connect(self.save_maps)
        v.documentation_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOC_URL)))
        v.close_button.clicked.connect(v.close)
        v.cancel_run_button.clicked.connect(self.stop_running)
        v.closing.connect(self.on_close)
        v.save_results_button.clicked.connect(self.save_results)
        v.load_results_button.clicked.connect(self.load_results)
        v.maps_tabs.currentChanged.connect(lambda _: self.refresh_preview())
        v.params_tabs.currentChanged.connect(lambda _: self.update_left())
        v.map_source_combo.activated.connect(self.select_map_source)
        v.optics_from_header_check.toggled.connect(self._optics_mode_changed)
        for w in (v.pixel_pitch_spin, v.focal_length_spin, v.aperture_spin):
            w.valueChanged.connect(self._manual_optics_changed)
        v.mount_combo.activated.connect(lambda i: setattr(v, "manual_mount", i))
        v.guiding_combo.activated.connect(lambda _: self._update_header_fields())
        v.ai_key_edit.editingFinished.connect(self._store_api_key)
        v.ai_button.clicked.connect(self.ask_claude)
        v.series_ai_button.clicked.connect(self.ask_claude)
        v.ai_continue_button.clicked.connect(self.continue_claude)
        v.series_add_button.clicked.connect(self.series_add)
        v.series_remove_button.clicked.connect(self.series_remove)
        v.series_clear_button.clicked.connect(self.series_clear)
        v.series_west_button.clicked.connect(lambda: self.series_set_side(SIDE_WEST))
        v.series_east_button.clicked.connect(lambda: self.series_set_side(SIDE_EAST))
        v.series_auto_button.clicked.connect(lambda: self.series_set_side(None))
        v.series_tree.itemClicked.connect(self.series_item_clicked)
        v.series_analyze_button.clicked.connect(self.analyze_series)
        for w in v.setting_widgets():
            for sig in ("toggled", "valueChanged", "currentIndexChanged", "textEdited"):
                if hasattr(w, sig) and w.objectName() not in ("aiLanguageEdit",):
                    getattr(w, sig).connect(self.setting_changed)
                    break

    # ------------------------------------------------------------------
    # Helpers
    def settings(self) -> Settings:
        return self.v.collect_settings()

    def log(self, level: str, text: str) -> None:
        self.v.console.append_line(level, text)

    def gui_log(self) -> Log:
        return Log(self.log)

    def message(self, text: str, icon=QMessageBox.Icon.Information) -> None:
        QMessageBox(icon, TITLE, text, QMessageBox.StandardButton.Ok, self.v).exec()

    def run(self, fn, done, label: str, exclusive: bool = True, on_message=None, failed=None) -> Worker | None:
        """Runs fn(log, progress, emit) in the background; done(result) in the
        GUI thread."""
        if exclusive and self.busy is not None:
            self.message("Please wait - another run is still going on (or click Stop).")
            return None
        w = Worker(fn)
        w.signals.log.connect(self.log)
        if exclusive:
            self.busy = w
            self._set_busy(True, label)
            w.signals.progress.connect(self._progress)
        if on_message:
            w.signals.message.connect(on_message)

        def finish(result=None, ok=True):
            self.workers.discard(w)
            if exclusive:
                self.busy = None
                self._set_busy(False)
            if ok:
                done(result)

        def fail(text):
            finish(ok=False)
            if failed:
                failed(text)
            else:
                self.message(text, QMessageBox.Icon.Critical)

        w.signals.finished.connect(lambda r: finish(r))
        w.signals.failed.connect(fail)
        w.signals.cancelled.connect(lambda: (finish(ok=False), self.log("warning", "Stopped.")))
        self.workers.add(w)
        start(w)
        return w

    def _set_busy(self, busy: bool, label: str = "") -> None:
        v = self.v
        for b in (v.preview_button, v.apply_button, v.ai_button, v.series_ai_button, v.open_button,
                  v.load_results_button, v.save_results_button):
            b.setEnabled(not busy)
        v.series_analyze_button.setEnabled(not busy or self.series_running)
        v.cancel_run_button.setVisible(busy)
        v.progress.setValue(0)
        v.progress.setFormat(label if busy else "")

    def _progress(self, fraction: float, label: str) -> None:
        self.v.progress.setValue(int(1000 * min(1.0, max(0.0, fraction))))
        self.v.progress.setFormat(label)

    def stop_running(self) -> None:
        if self.busy is not None:
            self.busy.cancel()

    def on_close(self) -> None:
        for w in list(self.workers):
            w.cancel()
        self.store.save(self.settings())
        self.store.save_ui_state({"splitter": self.v.splitter.sizes()})

    # ------------------------------------------------------------------
    # Target image
    def _fill_target_combo(self) -> None:
        c = self.v.target_combo
        c.blockSignals(True)
        c.clear()
        for img in self.images:
            c.addItem(img.name, img.path)
        c.addItem(TIPS_ITEM)
        c.setCurrentIndex(self.images.index(self.target) if self.target in self.images else len(self.images))
        c.blockSignals(False)

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.v, "Open image", "", "Images (*.fits *.fit *.fts *.xisf);;All files (*)")
        if not path:
            return

        def work(log, progress, emit):
            progress.step("Loading the image", 0, 0.6)
            img = load_image(path)
            progress.step("Stretching for display", 0.6, 1)
            return img, stretched_rgb8(img)

        def done(res):
            img, rgb = res
            self.images = [i for i in self.images if i.path != img.path] + [img]
            self.target = img
            self.target_bitmap = image_from_rgb(rgb)
            self.log("text", f"Opened: {path} ({img.width} x {img.height}, {'color' if img.is_color else 'mono'})")
            self._target_selected()

        self.run(work, done, "Loading the image")

    def select_target_index(self, index: int) -> None:
        if index < len(self.images):
            img = self.images[index]
            if img is not self.target:
                self.target = img
                self.target_bitmap = image_from_rgb(stretched_rgb8(img))
        else:
            self.target = None
            self.target_bitmap = None
        self._target_selected()

    def _target_selected(self) -> None:
        self._fill_target_combo()
        img = self.target
        self.v.image_info_label.setText(f"{img.width} x {img.height} pixels, {3 if img.is_color else 1} channel(s)"
                                        if img else "No target image selected.")
        self.v.color_target = bool(img and img.is_color)
        self._update_header_fields()
        self.v.update_controls()
        self.preview_stale = self.analysis is not None and not self._analysis_current()
        self.refresh_preview()
        self.update_left()

    def _analysis_current(self) -> bool:
        a = self.analysis
        if a is None or self.target is None:
            return a is not None and bool(a.file) and self.target is None
        if a.file:
            return True
        return a.key == analysis_key(self.target.name, self.target.path, self.settings())

    # ------------------------------------------------------------------
    # Optics fields
    def _optics_mode_changed(self, header: bool) -> None:
        v = self.v
        if header:
            v.manual_optics = (v.pixel_pitch_spin.value(), v.focal_length_spin.value(), v.aperture_spin.value())
            v.manual_mount = v.mount_combo.currentIndex()
        else:
            for sp, val in zip((v.pixel_pitch_spin, v.focal_length_spin, v.aperture_spin), v.manual_optics):
                sp.blockSignals(True)
                sp.setValue(val)
                sp.blockSignals(False)
            v.mount_combo.setCurrentIndex(v.manual_mount)
        self._update_header_fields()
        self.setting_changed()

    def _manual_optics_changed(self) -> None:
        v = self.v
        if not v.optics_from_header_check.isChecked():
            v.manual_optics = (v.pixel_pitch_spin.value(), v.focal_length_spin.value(), v.aperture_spin.value())

    def _keywords(self) -> list:
        if self.target is not None:
            return self.target.keywords
        if self.analysis is not None:
            return self.analysis.header.get("keywords", [])
        return []

    def _update_header_fields(self) -> None:
        v = self.v
        header = v.optics_from_header_check.isChecked()
        kw = self._keywords()
        values = keyword_values(kw)
        if header:
            hdr = read_optics_from_header(kw)
            for sp, key in ((v.pixel_pitch_spin, "pixel_pitch_um"), (v.focal_length_spin, "focal_length_mm"),
                            (v.aperture_spin, "aperture_mm")):
                sp.blockSignals(True)
                sp.setValue(hdr[key])
                sp.blockSignals(False)
            v.camera_value.setText(read_camera_from_header(kw))
            est = estimate_mount(values)
            v.mount_combo.setCurrentIndex(est["type"])
            have = self.target is not None or self.analysis is not None
            v.mount_source_label.setText(f"from {est['source']}" if est["source"] else
                                         ("not in the header" if have else ""))
        else:
            v.camera_value.setText("")
            v.mount_source_label.setText("")
        g = effective_guiding(v.guiding_combo.currentIndex(), values)
        v.guiding_source_label.setText(f"from {g['source']}" if v.guiding_combo.currentIndex() == GUIDING_UNKNOWN
                                       and g["type"] != GUIDING_UNKNOWN else "")

    def optics_for(self, a: Analysis, s: Settings):
        if getattr(a, "optics", None) is not None:
            return a.optics
        return resolve_optics(a.header.get("keywords", []), s, a.debayer)

    # ------------------------------------------------------------------
    # Settings changes, preview and evaluations
    def setting_changed(self, *_) -> None:
        self.v.update_controls()
        self.update_series_hints()
        if self.analysis is not None:
            self.preview_stale = not self._analysis_current() and self.map_source == 0
        self.redraw_timer.start()

    def current_analysis(self) -> Analysis | None:
        if self.map_source > 0 and self.map_source - 1 < len(self.series_static):
            return self.series_static[self.map_source - 1]["analysis"]
        if self.analysis is not None and not self.preview_stale:
            return self.analysis
        return None

    def refresh_preview(self) -> None:
        """Draws the map of the selected tab in the background and updates the
        evaluations and the assessment."""
        a = self.current_analysis()
        self.update_evaluations()
        if a is None:
            caption = ""
            if self.target is not None:
                caption = self.target.name + (" - settings changed: click Calculate" if self.preview_stale else "")
            self.v.preview.set_image(self.target_bitmap, caption)
            self.update_left()
            return
        s = self.settings()
        key = self.v.current_map_key()
        self.render_gen += 1
        gen = self.render_gen
        optics = self.optics_for(a, s)

        def work(log, progress, emit):
            return render_map(a, s, optics, key)

        def done(img):
            if gen == self.render_gen:
                self.v.preview.set_image(img, f"{a.target_name} - {map_view_by_key(key).label}")
        self.run(work, done, "", exclusive=False, failed=lambda t: self.log("error", "Drawing the map failed: " + t))
        self.update_assessment()
        self.update_left()

    def update_evaluations(self) -> None:
        v = self.v
        a = self.current_analysis()
        if a is None:
            for lab in (v.grid_eval_label, v.shape_eval_label, v.tilt_eval_label, v.coma_eval_label):
                lab.setText(CALC_HINT)
            v.tracking_info_label.setText("<i>Measured with Calculate.</i>")
            return
        s = self.settings()
        optics = self.optics_for(a, s)
        stars = stars_for(a, s.subtract_tracking)
        scale, unit = fwhm_display_unit(s, optics)
        v.grid_eval_label.setText(evaluation_html(format_fwhm_grid_evaluation(
            evaluate_fwhm_grid(stars, a.w, a.h, FWHM_GRID_SIZE), scale, unit)))
        static = format_static_series(self.series_static[self.map_source - 1]) if self.map_source > 0 else []
        v.shape_eval_label.setText(evaluation_html(static + format_shape_evaluation(
            shape_evaluation_for(a, s), a.tracking, s.subtract_tracking)))
        tilt = compute_siril_tilt(stars, a.w, a.h)
        axis = compute_tilt_axis(tilt, a.w, a.h, optics.pixel_pitch_um, optics.focal_length_mm,
                                 optics.aperture_mm) if tilt else None
        v.tilt_eval_label.setText(evaluation_html(format_tilt_evaluation(tilt, axis, scale, unit)))
        if a.has_asymmetry:
            lines: list[str] = []
            log_coma_field(a.coma_fit, a.coma_fit_centroid, a.w, a.h, optics.pixel_pitch_um,
                           Log(lambda level, t: lines.append(t)))
            v.coma_eval_label.setText(evaluation_html([l for l in lines if not l.startswith("---")]))
        else:
            v.coma_eval_label.setText("<i>PSF asymmetry not measured.</i>")
        t = a.tracking
        v.tracking_info_label.setText(
            f"Ellipticity <b>{t['ellipticity']:.3f}</b> (eccentricity {t['eccentricity']:.2f}), axis "
            f"{t['angle_deg']:.0f}°<br/>" + ("Negligible: tracking/guiding looks clean." if t["ellipticity"] < 0.03
                                             else "Shared by all stars: tracking/guiding, wind or flexure."))

    def update_assessment(self) -> None:
        a = self.analysis if not self.preview_stale else None
        if a is None:
            self.v.assessment_box.setHtml("<i>Click Calculate to assess the image.</i>")
            return
        s = self.settings()
        rep = report_analysis(a, stars_for(a, s.subtract_tracking), s, self.optics_for(a, s), SILENT)
        self.v.assessment_box.setHtml(format_assessment_html(assess_image(rep["metrics"], s.optics_type)))

    def update_left(self) -> None:
        v = self.v
        tab = v.params_tabs.currentIndex()
        if tab == 2:
            v.show_left("assessment")
        elif tab == 3:
            v.show_left("series" if self.series_shown else "series_hints")
        elif self.target is None and self.analysis is None and self.map_source == 0:
            v.show_left("tips")
        else:
            v.show_left("map")

    def select_map_source(self, index: int) -> None:
        self.map_source = index
        self.refresh_preview()

    def _fill_map_sources(self) -> None:
        c = self.v.map_source_combo
        c.blockSignals(True)
        c.clear()
        c.addItem("Target image (Calculate)")
        for e in self.series_static:
            c.addItem(f"Series static: {e['label']} ({e['analysis'].series['frames']} frames)")
        self.map_source = min(self.map_source, len(self.series_static))
        c.setCurrentIndex(self.map_source)
        c.blockSignals(False)

    # ------------------------------------------------------------------
    # Calculate and Save
    def calculate(self, then=None) -> None:
        if self.target is None:
            if self.analysis is not None and self.analysis.file:
                self.refresh_preview()
                return
            self.message("Open a target image first.")
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
                log.write("Reusing the star measurement of the preview.")
            optics = resolve_optics(img.keywords, s, a.debayer, log)
            progress.step("Evaluating", 0.85, 0.9)
            report_analysis(a, stars_for(a, s.subtract_tracking), s, optics, log, verbose=True)
            progress.step("Drawing the map", 0.9, 1)
            m = render_map(a, s, optics, key, log)
            log.write(f"Done in {time.monotonic() - t0:.1f} s.")
            return a, m, seconds, key

        def done(res):
            a, m, seconds, key = res
            self.analysis = a
            self.preview_stale = False
            self.map_source = 0
            self._fill_map_sources()
            if seconds is not None:
                self.frame_timing = {"seconds": seconds, "source": "calculate", "maps": False}
            self.v.results_info_label.setText("")
            self.clear_ai_review()
            self.v.preview.set_image(m, f"{a.target_name} - {map_view_by_key(key).label}")
            self.update_evaluations()
            self.update_assessment()
            self.update_series_hints()
            if self.v.params_tabs.currentIndex() not in (1, 2):
                self.v.params_tabs.setCurrentIndex(1)
            self.update_left()
            if then:
                then()

        self.run(work, done, "Calculate")

    def save_maps(self) -> None:
        """The four maps, the 3D plot and the CSV next to the image."""
        if self.target is None and (self.analysis is None or not self.analysis.file):
            self.message("Open a target image first.")
            return
        if not self._analysis_current():
            self.calculate(then=self.save_maps)
            return
        a = self.analysis
        s = self.settings()
        path = a.header.get("path") or ""
        if path:
            base = str(Path(path).with_suffix(""))
        else:
            d = QFileDialog.getExistingDirectory(self.v, "Save the maps to")
            if not d:
                return
            base = os.path.join(d, a.header.get("name") or "image")

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
                                "(Setup > General or FITS header).")
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

        self.run(work, lambda b: self.v.statusBar().showMessage(f"Saved next to {b}", 8000), "Save")

    # ------------------------------------------------------------------
    # Results files
    def save_results(self) -> None:
        if self.analysis is None:
            self.message("Click Calculate first: there are no results to save.")
            return
        initial = self.analysis.file or results_default_path(self.analysis.header)
        path, _ = QFileDialog.getSaveFileName(self.v, "Save results", initial, "Results files (*.json);;All files (*)")
        if not path:
            return
        try:
            save_results(self.analysis, self.settings(), path)
            self.log("note", f"Results saved ({len(self.analysis.stars)} stars): {path}")
        except OSError as e:
            self.message(f"Saving the results failed: {e}", QMessageBox.Icon.Critical)

    def load_results(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self.v, "Load results", "",
                                              "Results files (*_aberration.json *.json);;All files (*)")
        if not path:
            return

        def background_for(header, w, h, debayer):
            img = next((i for i in self.images if (header.get("path") and i.path == header["path"])), None)
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
        self._fill_map_sources()
        self.v.results_info_label.setText(f"<i>Showing loaded results: {html.escape(Path(path).name)} "
                                          f"({len(a.stars)} stars)</i>")
        self._update_header_fields()
        self.clear_ai_review()
        self.v.params_tabs.setCurrentIndex(1)
        self.refresh_preview()

    # ------------------------------------------------------------------
    # AI review
    def _update_ai_key_info(self) -> None:
        k = self.store.api_key()
        e = self.v.ai_key_edit
        env = bool(k and k[1] == "environment")
        e.setEnabled(not env)
        if env:
            e.setText("")
            e.setToolTip("<p>Using the environment variable ANTHROPIC_API_KEY.</p>")
        elif k:
            e.setText(k[0])

    def _store_api_key(self) -> None:
        if self.v.ai_key_edit.isEnabled():
            self.store.store_api_key(self.v.ai_key_edit.text())
            self._update_ai_key_info()

    def set_ai_text(self, html_text: str) -> None:
        self.v.ai_box.setHtml(html_text)
        self.v.series_ai_box.setHtml(html_text)

    def clear_ai_review(self) -> None:
        self.set_ai_text("<i>Click Ask Claude for a second opinion on the assessment of a Calculate, on the "
                         "comparison of the Series tab, or both.</i>")
        self.ai_pending = None
        self.v.ai_continue_button.setEnabled(False)

    def _show_ai_tab(self) -> None:
        self.v.assessment_pane.setCurrentIndex(1)
        self.v.series_pane.setCurrentIndex(2)

    def ask_claude(self) -> None:
        have_single = self.analysis is not None and not self.preview_stale
        have_series = self.series_result is not None and self.series_result["measured"] > 0
        if not have_single and not have_series:
            self.message("Click Calculate, or Analyze series on the Series tab, first: the review needs a current "
                         "analysis.")
            return
        self._store_api_key()
        k = self.store.api_key()
        if k is None:
            self.message("Enter an Anthropic API key first (Assessment tab), or set the environment variable "
                         "ANTHROPIC_API_KEY.")
            return
        s = self.settings()
        self.store.save(s)
        a = self.analysis
        frames, result = list(self.series_frames), self.series_result
        ctx = {"s": s, "have_single": have_single, "have_series": have_series, "message": None}
        self.set_ai_text("<i>Waiting for Claude's answer - this can take a minute ...</i>")
        self._show_ai_tab()
        self.ai_pending = None
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
                    for v in MAP_VIEWS:
                        if v.ai:
                            progress.step(f"Drawing {v.label}", 0.1, 0.2)
                            maps.append({"label": v.label, "png": png_bytes(render_map(a, s, optics, v.key),
                                                                            AI_MAP_MAX_SIDE)})
            series = {"frames": frames, "result": result} if have_series else None
            ctx["message"] = build_message(s, single, series, maps)
            progress.step("Waiting for Claude's answer", 0.2, 1)
            return self._request(k[0], ctx["message"], None, progress)

        self.run(work, lambda r: self.show_ai_review(r, ctx), "Ask Claude",
                 failed=lambda t: self.show_ai_review({"error": t}, ctx))

    def _request(self, key: str, message: dict, previous: Review | None, progress) -> Review | dict:
        try:
            return request_review(key, message, previous,
                                  on_progress=lambda n: progress.within(min(0.95, n / 6000)),
                                  cancelled=lambda: False)
        except ReviewError as e:
            return {"error": str(e)}

    def continue_claude(self) -> None:
        if self.ai_pending is None:
            return
        k = self.store.api_key()
        if k is None:
            self.message("Enter an Anthropic API key first (Assessment tab), or set the environment variable "
                         "ANTHROPIC_API_KEY.")
            return
        review, message, ctx = self.ai_pending
        self.set_ai_text("<i>Waiting for the rest of Claude's answer - this can take a minute ...</i>")
        self._show_ai_tab()
        self.v.ai_continue_button.setEnabled(False)

        def done(r):
            if isinstance(r, dict):
                self.set_ai_text(f"<b>Continuing the AI review failed:</b> {html.escape(r['error'])}<br><br>"
                                 + format_review_html(review))
                self.log("error", "Continuing the AI review failed: " + r["error"])
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
            self.v.ai_continue_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Series
    def update_series_hints(self) -> None:
        s = self.settings()
        frames = self.series_frames
        directory = f"<i>{html.escape(os.path.dirname(frames[0].path))}</i>" if frames else \
            "the directory of the first frame"
        items = [f"<b>StarAberrationSeries.csv</b> - one line per frame, in {directory}" if s.do_export else
                 "No CSV table - <i>Export data table as CSV</i> is off (Setup › General)"]
        if s.series_save_maps:
            items.append("Next to each frame: <b><i>name</i>_Size.png, _Shape.png, _Coma.png, _Tilt.png</b>" +
                         (" and <b>_Aberration3D.png</b> (with pixel pitch, focal length and aperture)"
                          if s.show_3d_tilt_plot else ""))
            items.append(f"After pass 2, in {directory}: <b>SeriesStatic_all_Size.png ... _Tilt.png</b>, and the "
                         "same per side (e.g. SeriesStatic_West_...)")
        else:
            items.append("No map images - <i>Save maps next to the frames</i> is off")
        items.append("Next to each frame: <b><i>name</i>_Mosaic.png</b>" if s.series_save_mosaic else
                     "No mosaics - <i>Save a 3x3 mosaic next to the frames</i> is off")
        if s.ai_save_log:
            items.append(f"Ask Claude: <b>StarAberrationAI_<i>date</i>_<i>time</i>.txt</b> in {directory} "
                         "(Assessment tab)")
        items.append("Files of an earlier run are replaced; the frames themselves are never changed")

        def section(title, lines):
            return (f"<p style='margin-top:10px; margin-bottom:2px;'><b>{title}</b></p><ul style='margin-top:0px;'>"
                    + "".join(f"<li>{t}</li>" for t in lines) + "</ul>")

        self.v.series_hints_label.setText(
            "<h3>Series analysis: before you start</h3>" + series_hints_status(frames, self.frame_timing, s) +
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
                "the Side column, and set it with West / East where it is ?"]))

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
        self.v.series_has_frames = bool(self.series_frames)
        self.v.update_controls()

    def selected_frames(self) -> list:
        t = self.v.series_tree
        return [self.series_frames[t.indexOfTopLevelItem(i)] for i in t.selectedItems()
                if 0 <= t.indexOfTopLevelItem(i) < len(self.series_frames)]

    def _series_changed(self, measured_too: bool) -> None:
        assign_series_sides(self.series_frames)
        if measured_too:
            self.series_result = None
            self.series_static = []
            self._fill_map_sources()
            self.series_shown = False
        elif self.series_result is not None:
            static = self.series_result.get("static", [])
            stop = self.series_result.get("stop")
            self.series_result = compare_series(self.series_frames)
            self.series_result["static"], self.series_result["stop"] = static, stop
            self.v.series_box.setHtml(format_series_report(self.series_result))
        self.fill_series_tree()
        self.update_series_hints()
        self.update_left()

    def series_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self.v, "Add light frames", "",
                                                "Images (*.fits *.fit *.fts *.xisf);;All files (*)")
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
        for f in self.selected_frames():
            f.manual_side = side
        self._series_changed(False)

    def series_item_clicked(self, item, _col) -> None:
        i = self.v.series_tree.indexOfTopLevelItem(item)
        if 0 <= i < len(self.series_frames):
            f = self.series_frames[i]
            if f.map_image is not None:
                self.v.series_pane.setCurrentIndex(0)
                self.v.series_preview.set_image(f.map_image, f"Series frame {f.name} - "
                                                f"{map_view_by_key(f.map_key).label} (reduced)")

    def analyze_series(self) -> None:
        if self.series_running:
            self.stop_running()
            return
        if not self.series_frames:
            self.message("Add the light frames of the series first.")
            return
        s = self.settings()
        self.store.save(s)
        frames = self.series_frames
        key = self.v.current_map_key()
        with_maps = s.series_show_maps or s.series_save_maps or s.series_save_mosaic
        self.series_running = True
        self.series_shown = True
        self.v.series_analyze_button.setText("Stop")
        self.v.series_pane.setCurrentIndex(0)
        self.update_left()
        t_start = time.monotonic()

        def on_frame(f, a, optics, i, report):
            if s.series_show_maps:
                img = render_map(a, s, optics, key)
                f.map_image = scaled_to_max_side(img, 1600)
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
                on_frame(f, a, optics, i, report)
                emit(f)
            progress.step("Measuring the frames", 0, 0.9)
            run = measure_series(frames, s, log, progress, lambda: self.busy is not None and self.busy.is_cancelled,
                                 frame_done if with_maps else (lambda f, *a: emit(f)))
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
                self.v.series_preview.set_image(f.map_image, f"Series frame {f.name} - "
                                                f"{map_view_by_key(f.map_key).label} (reduced)")

        def finish():
            self.series_running = False
            self.v.series_analyze_button.setText("Analyze series")
            self._set_busy(False)

        def done(result):
            finish()
            measured = sum(1 for f in frames if f.metrics)
            if measured:
                self.frame_timing = {"seconds": (time.monotonic() - t_start) / measured, "source": "series",
                                     "maps": with_maps}
            self.series_result = result
            self.series_static = result["static"]
            self._fill_map_sources()
            self.fill_series_tree()
            self.v.series_box.setHtml(format_series_report(result))
            self.v.series_pane.setCurrentIndex(1)
            self.clear_ai_review()
            self.log("note", f"Series done in {time.monotonic() - t_start:.1f} s.")
            self.update_series_hints()

        w = self.run(work, done, "Analyze series", on_message=frame_message,
                     failed=lambda t: (finish(), self.message(t, QMessageBox.Icon.Critical)))
        if w is None:
            finish()
            return
        w.signals.cancelled.connect(finish)
        self.v.series_analyze_button.setEnabled(True)
