"""Two-way binding of widgets to fields of a Settings object.

A Binder holds the settings it edits (get()) and a callback called with the
field name after every change made in a widget. load() puts the values of
the settings into the widgets without calling back.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget


class Binder:
    def __init__(self, get: Callable[[], object], changed: Callable[[str], None] | None = None):
        self._get = get
        self._changed = changed
        self._bindings: list[tuple[QWidget | QButtonGroup, str, Callable, Callable]] = []
        # Until the first load(), signals of widgets still being built (a combo
        # box filled after binding, a range set) must not change the settings.
        self._loading = True

    def _notify(self, field: str, value) -> None:
        if self._loading:
            return
        setattr(self._get(), field, value)
        if self._changed:
            self._changed(field)

    def check(self, w: QAbstractButton, field: str, invert: bool = False) -> QAbstractButton:
        w.toggled.connect(lambda v: self._notify(field, (not v) if invert else v))
        self._bindings.append((w, field, lambda v: w.setChecked((not v) if invert else bool(v)), w.isChecked))
        return w

    def number(self, w: QSpinBox | QDoubleSpinBox, field: str) -> QWidget:
        w.valueChanged.connect(lambda v: self._notify(field, v))
        self._bindings.append((w, field, w.setValue, w.value))
        return w

    def index(self, w: QComboBox, field: str) -> QComboBox:
        w.currentIndexChanged.connect(lambda i: self._notify(field, i))
        self._bindings.append((w, field, w.setCurrentIndex, w.currentIndex))
        return w

    def choice(self, w: QComboBox, field: str, values: list[str]) -> QComboBox:
        w.currentIndexChanged.connect(lambda i: self._notify(field, values[i]))
        self._bindings.append((w, field, lambda v: w.setCurrentIndex(values.index(v) if v in values else 0),
                               lambda: values[w.currentIndex()]))
        return w

    def text(self, w: QLineEdit, field: str) -> QLineEdit:
        w.textEdited.connect(lambda t: self._notify(field, t))
        self._bindings.append((w, field, w.setText, w.text))
        return w

    def group(self, g: QButtonGroup, field: str) -> QButtonGroup:
        g.idToggled.connect(lambda i, on: on and self._notify(field, i))
        self._bindings.append((g, field, lambda v: g.button(v).setChecked(True) if g.button(v) else None,
                               g.checkedId))
        return g

    def load(self) -> None:
        s = self._get()
        self._loading = True
        try:
            for _, field, setter, _ in self._bindings:
                setter(getattr(s, field))
        finally:
            self._loading = False
