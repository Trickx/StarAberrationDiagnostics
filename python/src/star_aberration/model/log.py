"""The console of a run: what the original script wrote to PixInsight's
Process Console. The model writes to a Log; the GUI shows it, a silent Log
discards it (the dialog evaluates an analysis again whenever a setting
changes)."""

from __future__ import annotations

import sys
from typing import Callable


class Log:
    """Writes to a sink(level, text); levels: "text", "note", "warning",
    "error"."""

    def __init__(self, sink: Callable[[str, str], None] | None = None):
        self._sink = sink

    def _emit(self, level: str, text: str) -> None:
        if self._sink is not None:
            self._sink(level, text)

    def write(self, text: str = "") -> None:
        self._emit("text", text)

    def note(self, text: str) -> None:
        self._emit("note", text)

    def warning(self, text: str) -> None:
        self._emit("warning", text)

    def error(self, text: str) -> None:
        self._emit("error", text)


class SilentLog(Log):
    def __init__(self):
        super().__init__(None)


class StdoutLog(Log):
    def __init__(self):
        super().__init__(lambda level, text: print(("" if level == "text" else f"[{level}] ") + text,
                                                   file=sys.stderr if level in ("warning", "error") else sys.stdout))


SILENT = SilentLog()
