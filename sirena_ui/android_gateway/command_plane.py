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
        self._urgent: "queue.Queue[tuple[Future, Callable[[], object]]]" = queue.Queue()

    def submit(self, fn: Callable[[], T], *, timeout: float = 120.0, urgent: bool = False) -> T:
        """Block the caller until ``fn`` runs on the Qt thread and completes."""
        fut: Future = Future()
        target = self._urgent if urgent else self._pending
        target.put((fut, fn))  # type: ignore[arg-type]
        self._schedule_drain()
        return fut.result(timeout=timeout)  # type: ignore[no-any-return]

    def _schedule_drain(self) -> None:
        from PyQt5.QtCore import QMetaObject

        QMetaObject.invokeMethod(self, "_drain", Qt.QueuedConnection)

    def _drain_queue(
        self,
        q: "queue.Queue[tuple[Future, Callable[[], object]]]",
        budget: int,
    ) -> int:
        processed = 0
        while processed < budget:
            try:
                fut, fn = q.get_nowait()
            except queue.Empty:
                break
            processed += 1
            try:
                fut.set_result(fn())
            except Exception as exc:  # noqa: BLE001
                fut.set_exception(exc)
        return processed

    @pyqtSlot()
    def _drain(self) -> None:
        # Stop / hold / E-stop jump ahead of status polls and slow primes.
        used = self._drain_queue(self._urgent, _MAX_DRAIN_PER_TICK)
        remaining = max(0, _MAX_DRAIN_PER_TICK - used)
        if remaining:
            self._drain_queue(self._pending, remaining)
        if not self._urgent.empty() or not self._pending.empty():
            self._schedule_drain()
