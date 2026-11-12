"""Running work in a background thread: a function gets a Log and a
Progress; what they report reaches the GUI thread as Qt signals, as do the
result and errors."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..model.analysis import Cancelled, Progress
from ..model.log import Log


class WorkerSignals(QObject):
    progress = Signal(float, str)
    log = Signal(str, str)
    message = Signal(object)  # free-form intermediate results (e.g. a frame's map)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class Worker(QRunnable):
    """fn(log, progress, emit) -> result; emit(obj) sends an intermediate
    result to the GUI thread."""

    def __init__(self, fn: Callable[[Log, Progress, Callable[[Any], None]], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        log = Log(lambda level, text: self.signals.log.emit(level, text))
        progress = Progress(lambda f, label: self.signals.progress.emit(f, label), self._cancel.is_set)
        try:
            result = self.fn(log, progress, self.signals.message.emit)
        except Cancelled:
            self.signals.cancelled.emit()
            return
        except Exception as e:  # reported to the user, with the traceback in the console
            self.signals.log.emit("error", traceback.format_exc().rstrip())
            self.signals.failed.emit(str(e) or e.__class__.__name__)
            return
        if self._cancel.is_set():
            self.signals.cancelled.emit()
        else:
            self.signals.finished.emit(result)


def start(worker: Worker) -> Worker:
    QThreadPool.globalInstance().start(worker)
    return worker
