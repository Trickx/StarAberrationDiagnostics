"""The look of the app: Fusion style with a dark or light palette (following
the system unless chosen), a stylesheet for cards, toolbar, tabs and a
segmented control, Material Design icons (qtawesome) and the pyqtgraph
colors to match."""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    window: str
    surface: str  # cards, panels
    surface2: str  # inputs, hovered
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    plot_bg: str
    dark: bool


DARK = Colors(window="#16181d", surface="#1f232a", surface2="#2a2f38", border="#323844", text="#e6e9ef",
              muted="#9aa3b2", accent="#5b8def", accent_text="#ffffff", plot_bg="#111318", dark=True)
LIGHT = Colors(window="#f3f4f7", surface="#ffffff", surface2="#eef0f4", border="#d9dde5", text="#1d2330",
               muted="#5f6b7d", accent="#2f6fe4", accent_text="#ffffff", plot_bg="#ffffff", dark=False)

_current: Colors = DARK


def colors() -> Colors:
    return _current


def system_is_dark() -> bool:
    hints = QApplication.styleHints()
    try:
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return QApplication.palette().window().color().lightness() < 128


def icon(name: str, color: str | None = None) -> QIcon:
    """A Material Design icon ("mdi6.folder-open" ...); an empty icon when
    qtawesome lacks it."""
    try:
        import qtawesome as qta
        return qta.icon(name, color=color or _current.text, color_disabled=_current.muted)
    except Exception:  # missing font or icon name: no icon rather than a crash
        return QIcon()


def apply(app: QApplication, mode: str = "auto") -> Colors:
    """mode: "auto" (the system's), "dark" or "light"."""
    global _current
    dark = system_is_dark() if mode == "auto" else mode == "dark"
    c = _current = DARK if dark else LIGHT
    app.setStyle("Fusion")
    pal = QPalette()
    role = QPalette.ColorRole
    for r, v in ((role.Window, c.window), (role.WindowText, c.text), (role.Base, c.surface2),
                 (role.AlternateBase, c.surface), (role.ToolTipBase, c.surface), (role.ToolTipText, c.text),
                 (role.Text, c.text), (role.Button, c.surface), (role.ButtonText, c.text),
                 (role.BrightText, "#ff5555"), (role.Highlight, c.accent), (role.HighlightedText, c.accent_text),
                 (role.Link, c.accent), (role.PlaceholderText, c.muted), (role.Mid, c.border),
                 (role.Dark, c.border), (role.Light, c.surface2)):
        pal.setColor(r, QColor(v))
    for r in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, r, QColor(c.muted))
    app.setPalette(pal)
    f = QFont(app.font())
    f.setPointSizeF(max(f.pointSizeF(), 12.5))
    app.setFont(f)
    app.setStyleSheet(_stylesheet(c))
    pg.setConfigOptions(antialias=True, background=c.plot_bg, foreground=c.muted, imageAxisOrder="row-major")
    return c


def _stylesheet(c: Colors) -> str:
    return f"""
QMainWindow, QDialog {{ background: {c.window}; }}
QToolTip {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border}; padding: 6px;
            border-radius: 6px; }}

QToolBar {{ background: {c.window}; border: none; border-bottom: 1px solid {c.border}; padding: 6px 8px;
            spacing: 6px; }}
QToolBar QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 10px;
                        color: {c.text}; }}
QToolBar QToolButton:hover {{ background: {c.surface2}; }}
QToolBar QToolButton:disabled {{ color: {c.muted}; }}
QToolBar QToolButton#primary {{ background: {c.accent}; color: {c.accent_text}; font-weight: 600; }}
QToolBar QToolButton#primary:hover {{ background: {QColor(c.accent).lighter(115).name()}; }}
QToolBar QToolButton#primary:disabled {{ background: {c.surface2}; color: {c.muted}; }}

QFrame#segmented {{ background: {c.surface2}; border-radius: 9px; padding: 2px; }}
QFrame#segmented QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 5px 14px;
                                color: {c.muted}; }}
QFrame#segmented QToolButton:checked {{ background: {c.surface}; color: {c.text}; font-weight: 600; }}
QFrame#segmented QToolButton:hover:!checked {{ color: {c.text}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c.muted}; padding: 8px 16px; margin-right: 2px;
                border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {c.text}; border-bottom: 2px solid {c.accent}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ color: {c.text}; }}

QDockWidget {{ color: {c.muted}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c.window}; padding: 8px 10px; text-transform: uppercase; }}

QGroupBox {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; margin-top: 14px;
             padding: 14px 10px 10px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 4px;
                    color: {c.muted}; }}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; background: transparent; }}

QPushButton {{ background: {c.surface2}; border: 1px solid {c.border}; border-radius: 8px; padding: 6px 14px;
               color: {c.text}; }}
QPushButton:hover {{ border-color: {c.accent}; }}
QPushButton:default, QPushButton#primary {{ background: {c.accent}; border-color: {c.accent};
                                             color: {c.accent_text}; font-weight: 600; }}
QPushButton:disabled {{ color: {c.muted}; border-color: {c.border}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {c.surface2}; border: 1px solid {c.border};
    border-radius: 7px; padding: 4px 8px; color: {c.text}; selection-background-color: {c.accent}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 6px;
                               selection-background-color: {c.accent}; }}

QTextBrowser, QPlainTextEdit, QTreeWidget {{ background: {c.surface}; border: 1px solid {c.border};
    border-radius: 10px; padding: 6px; color: {c.text}; }}
QTreeWidget::item {{ padding: 4px 2px; }}
QTreeWidget::item:selected {{ background: {c.accent}; color: {c.accent_text}; }}
QHeaderView::section {{ background: {c.surface}; color: {c.muted}; border: none;
                        border-bottom: 1px solid {c.border}; padding: 6px 4px; font-weight: 600; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c.border}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {c.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none;
    border: none; height: 0; width: 0; }}

QSplitter::handle {{ background: {c.window}; }}
QStatusBar {{ background: {c.window}; color: {c.muted}; border-top: 1px solid {c.border}; }}
QProgressBar {{ background: {c.surface2}; border: none; border-radius: 6px; height: 12px; text-align: center;
                color: {c.text}; }}
QProgressBar::chunk {{ background: {c.accent}; border-radius: 6px; }}
QMenuBar {{ background: {c.window}; }}
QMenu {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c.accent}; color: {c.accent_text}; }}
QMenu::separator {{ height: 1px; background: {c.border}; margin: 4px 8px; }}
QLabel#hint {{ color: {c.muted}; }}
QLabel#title {{ font-size: 20px; font-weight: 700; }}
"""
