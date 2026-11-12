"""Building blocks of the main window: aligned field rows, group boxes,
rich-text labels, the map preview, the legend of the FWHM fill and the
console."""

from __future__ import annotations

import json
from importlib import resources

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                               QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy,
                               QSpinBox, QVBoxLayout, QWidget)

from ..render.maps import FWHM_FILL_RATIO_MAX, fwhm_fill_color
from ..render.paint import opaque_over, qcolor
from ..model.fwhm_grid import FWHM_RATIO_LIMITS

UI_TEXT: dict = json.loads(resources.files(__package__).joinpath("ui_text.json").read_text(encoding="utf-8"))

LABEL_WIDTH = 150


def tip(widget: QWidget, key: str) -> QWidget:
    """Sets text (where empty) and tooltip of a widget from the texts of the
    original dialog."""
    t = UI_TEXT.get(key)
    if t:
        if t["tooltip"]:
            widget.setToolTip(t["tooltip"])
        if t["text"] and hasattr(widget, "setText") and not widget.text():
            widget.setText(t["text"])
    widget.setObjectName(key)
    return widget


def check(key: str, text: str = "") -> QCheckBox:
    return tip(QCheckBox(text or UI_TEXT.get(key, {}).get("text", "")), key)


def radio(key: str) -> QRadioButton:
    return tip(QRadioButton(UI_TEXT.get(key, {}).get("text", "")), key)


def button(key: str, text: str = "") -> QPushButton:
    return tip(QPushButton(text or UI_TEXT.get(key, {}).get("text", "")), key)


def spin(key: str, lo: int, hi: int) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setFixedWidth(110)
    return tip(w, key)


def dspin(key: str, lo: float, hi: float, decimals: int, step: float | None = None) -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(decimals)
    w.setSingleStep(step if step is not None else 10 ** -decimals if decimals else 1)
    w.setFixedWidth(110)
    return tip(w, key)


def field_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lab.setFixedWidth(LABEL_WIDTH)
    return lab


def field_row(label: QLabel, *widgets: QWidget, stretch_last: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(label)
    for i, w in enumerate(widgets):
        row.addWidget(w, 1 if stretch_last and i == len(widgets) - 1 else 0)
    if not stretch_last:
        row.addStretch(1)
    return row


def indent_row(*widgets: QWidget, stretch_last: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addSpacing(LABEL_WIDTH + 6)
    for i, w in enumerate(widgets):
        row.addWidget(w, 1 if stretch_last and i == len(widgets) - 1 else 0)
    if not stretch_last:
        row.addStretch(1)
    return row


def group(title: str, *rows) -> QGroupBox:
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(4)
    for r in rows:
        if isinstance(r, QWidget):
            lay.addWidget(r)
        else:
            lay.addLayout(r)
    return box


def page(*groups: QWidget) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(6)
    for g in groups:
        lay.addWidget(g)
    lay.addStretch(1)
    return w


def rich_label(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.TextFormat.RichText)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lab.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    return lab


def evaluation_html(lines: list[str]) -> str:
    """Evaluation lines ("Topic: text"; lines starting with spaces indented
    under the line above) as rich text."""
    import html
    out = []
    for line in lines:
        sub = line[:1].isspace()
        line = line.strip()
        k = line.find(":")
        body = (f"<b>{html.escape(line[:k + 1])}</b>{html.escape(line[k + 1:])}" if k > 0 else html.escape(line))
        style = "margin-top:0px; margin-bottom:1px; margin-left:16px;" if sub else "margin-top:0px; margin-bottom:4px;"
        out.append(f"<p style='{style}'>{body}</p>")
    return "".join(out)


class MapPreview(QGraphicsView):
    """The map (or the image) with zoom (wheel) and pan (drag); fitted to the
    view until the user zooms."""

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.item)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(0x20, 0x20, 0x20)))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.fitting = True
        self.caption = QLabel(self)
        self.caption.setStyleSheet("QLabel { color: white; background: rgba(0,0,0,140); padding: 3px 6px; }")
        self.caption.move(6, 6)

    def set_image(self, image: QImage | None, caption: str = "") -> None:
        if image is None:
            self.item.setPixmap(QPixmap())
        else:
            first = self.item.pixmap().isNull() or self.item.pixmap().size() != image.size()
            self.item.setPixmap(QPixmap.fromImage(image))
            self.scene().setSceneRect(QRectF(0, 0, image.width(), image.height()))
            if first:
                self.fitting = True
        self.caption.setText(caption)
        self.caption.adjustSize()
        self.caption.setVisible(bool(caption))
        if self.fitting:
            self.fit()

    def fit(self) -> None:
        if not self.item.pixmap().isNull():
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
        self.fitting = True

    def wheelEvent(self, e: QWheelEvent) -> None:
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(f, f)
        self.fitting = False

    def mouseDoubleClickEvent(self, e) -> None:
        self.fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.fitting:
            self.fit()


class FwhmLegend(QWidget):
    """The legend of the FWHM fill: the color ramp with the class limits,
    and the unfilled and the hatched cell."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(int(self.fontMetrics().height() * 6.2))

    def paintEvent(self, e) -> None:
        g = QPainter(self)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        fh, asc = fm.height(), fm.ascent()
        sky = 0x202020
        fg = self.palette().windowText().color()
        x, y = 2, 0
        bar_w, bar_h = max(60, self.width() - 4), round(fh * 1.6)
        g.setPen(fg)
        g.drawText(QPointF(x, y + asc), "Fill: FWHM relative to the center (framed inner 3×3 cells)")
        y += round(fh * 1.3)
        steps = 40
        for i in range(steps):
            ratio = 1 + (FWHM_FILL_RATIO_MAX - 1) * (i + 0.5) / steps
            g.fillRect(QRectF(x + bar_w * i / steps, y, bar_w / steps + 0.5, bar_h),
                       qcolor(opaque_over(fwhm_fill_color(ratio), sky)))
        g.setPen(fg)
        g.drawRect(QRectF(x, y, bar_w, bar_h))
        for t in [1.0] + FWHM_RATIO_LIMITS:
            tx = x + bar_w * (t - 1) / (FWHM_FILL_RATIO_MAX - 1)
            g.drawLine(QPointF(tx, y + bar_h), QPointF(tx, y + bar_h + 3))
            label = f"×{t:.2f}" + ("+" if t >= FWHM_FILL_RATIO_MAX else "")
            lw = fm.horizontalAdvance(label)
            g.drawText(QPointF(max(x, min(tx - lw / 2, x + bar_w - lw)), y + bar_h + 3 + asc), label)
        y += bar_h + 3 + round(fh * 1.3)
        box = bar_h
        g.fillRect(QRectF(x, y, box, box), qcolor(0xFF000000 | sky))
        g.drawRect(QRectF(x, y, box, box))
        g.drawText(QPointF(x + box + 6, y + asc - 1), "not significant")
        x2 = x + box + 6 + fm.horizontalAdvance("not significant") + fh
        g.fillRect(QRectF(x2, y, box, box), qcolor(0xFF000000 | sky))
        g.fillRect(QRectF(x2, y, box, box), QBrush(qcolor(0x90B0B0B0), Qt.BrushStyle.BDiagPattern))
        g.drawRect(QRectF(x2, y, box, box))
        g.drawText(QPointF(x2 + box + 6, y + asc - 1), "uncertain")
        g.end()


class Console(QPlainTextEdit):
    """The log of the runs, colored by level."""

    COLORS = {"note": "#3b8eea", "warning": "#d18616", "error": "#e05252"}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(20000)
        f = self.font()
        f.setFamily("Menlo")
        f.setStyleHint(f.StyleHint.Monospace)
        self.setFont(f)

    def append_line(self, level: str, text: str) -> None:
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        if level in self.COLORS:
            fmt.setForeground(QColor(self.COLORS[level]))
        else:
            fmt.setForeground(self.palette().text().color())
        cur.insertText(text + "\n", fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()
