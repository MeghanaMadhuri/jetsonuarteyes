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


class QtCommandPlane(QObject):
    """Queue (Future, fn) pairs; drain on the GUI thread."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: "queue.Queue[tuple[Future, Callable[[], object]]]" = queue.Queue()

    def submit(self, fn: Callable[[], T], *, timeout: float = 120.0) -> T:
        """Block the caller until ``fn`` runs on the Qt thread and completes."""
        fut: Future = Future()
        self._pending.put((fut, fn))  # type: ignore[arg-type]
        from PyQt5.QtCore import QMetaObject

        QMetaObject.invokeMethod(self, "_drain", Qt.QueuedConnection)
        return fut.result(timeout=timeout)  # type: ignore[no-any-return]

    @pyqtSlot()
    def _drain(self) -> None:
        while True:
            try:
                fut, fn = self._pending.get_nowait()
            except queue.Empty:
                break
            try:
                fut.set_result(fn())
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
