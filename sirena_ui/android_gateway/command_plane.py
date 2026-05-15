"""Run callables on the Qt GUI thread from FastAPI worker threads.

HTTP threads must never hold ``NinaService.bus_lock`` or construct Qt workers.
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future
from typing import Callable, TypeVar

from PyQt5.QtCore import QObject, Qt, pyqtSlot

log = logging.getLogger("sirena_ui.android_gateway.command_plane")

T = TypeVar("T")

# Process at most this many HTTP→GUI jobs per event-loop tick so a burst of
# tablet polls cannot freeze taps for multiple seconds.
_MAX_DRAIN_PER_TICK = 2


class QtCommandPlane(QObject):
    """Queue (Future, fn) pairs; drain on the GUI thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: "queue.Queue[tuple[Future, Callable[[], object]]]" = queue.Queue()

    def submit(self, fn: Callable[[], T], *, timeout: float = 120.0) -> T:
        """Block the caller until ``fn`` runs on the Qt thread and completes."""
        fut: Future = Future()
        self._pending.put((fut, fn))  # type: ignore[arg-type]
        self._schedule_drain()
        return fut.result(timeout=timeout)  # type: ignore[no-any-return]

    def _schedule_drain(self) -> None:
        from PyQt5.QtCore import QMetaObject

        QMetaObject.invokeMethod(self, "_drain", Qt.QueuedConnection)

    @pyqtSlot()
    def _drain(self) -> None:
        processed = 0
        while processed < _MAX_DRAIN_PER_TICK:
            try:
                fut, fn = self._pending.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                fut.set_result(fn())
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
        if not self._pending.empty():
            self._schedule_drain()
