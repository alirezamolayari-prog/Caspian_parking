"""Run slow work (reports, exports) on the Qt thread pool — the UI thread never blocks (SPEC §2.4)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(object)


class Task(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:  # reported to the UI, never swallowed silently
            log.exception("background task failed")
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


def run_in_background(
    function: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[Exception], None] | None = None,
) -> Task:
    task = Task(function)
    task.signals.finished.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    QThreadPool.globalInstance().start(task)
    return task
