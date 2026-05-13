"""Action playback / record using the same workers as ``ActionsScreen``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.playback_worker import PlaybackWorker
from sirena_ui.workers.record_worker import RecordWorker

if TYPE_CHECKING:
    pass

log = logging.getLogger("sirena_ui.android_gateway.robot_actions")


class RobotActionController(QObject):
    """Lives on the Qt GUI thread; owns playback/record workers (like ``ActionsScreen``)."""

    #: String payloads only — never pass ``NinaService`` through Qt signals.
    playback_finished = pyqtSignal(str, bool, str)  # name, ok, message

    def __init__(self, service: NinaService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._playback_worker: PlaybackWorker | None = None
        self._record_worker: RecordWorker | None = None
        self._playback_workers: List[PlaybackWorker] = []

    def busy_playback(self) -> bool:
        w = self._playback_worker
        return w is not None and w.isRunning()

    def busy_record(self) -> bool:
        w = self._record_worker
        return w is not None and w.isRunning()

    def try_play(self, name: str) -> Dict[str, Any]:
        if self.busy_record():
            return {"ok": False, "error": "recording in progress", "code": "busy_record"}
        if self.busy_playback():
            return {"ok": False, "error": "playback in progress", "code": "busy_playback"}
        audio_path = self._service.action_audio_path(name)
        audio_offset = (
            self._service.action_audio_offset(name) if audio_path else 0.0
        )
        # Match kiosk ActionsScreen + tablet delegate: optional ensure on worker thread.
        worker = PlaybackWorker(
            self._service,
            name,
            audio_path=audio_path,
            audio_offset_sec=audio_offset,
            ensure_before_play=True,
            parent=None,
        )
        worker.finished_ok.connect(self._on_play_ok)
        worker.failed.connect(self._on_play_fail)
        self._playback_workers.append(worker)

        def _cleanup() -> None:
            try:
                self._playback_workers.remove(worker)
            except ValueError:
                pass
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        self._playback_worker = worker
        worker.start()
        return {"ok": True, "queued": True, "action": name}

    @pyqtSlot(str)
    def _on_play_ok(self, name: str) -> None:
        self._playback_worker = None
        self.playback_finished.emit(name, True, "")

    @pyqtSlot(str)
    def _on_play_fail(self, message: str) -> None:
        self._playback_worker = None
        self.playback_finished.emit("", False, message)

    def try_start_record(
        self,
        name: str,
        seconds: float,
        hz: float,
        countdown: float,
        hold_after: bool,
        register_manifest: bool,
    ) -> Dict[str, Any]:
        if self.busy_playback():
            return {"ok": False, "error": "playback in progress", "code": "busy_playback"}
        if self.busy_record():
            return {"ok": False, "error": "recording already running", "code": "busy_record"}
        worker = RecordWorker(
            self._service,
            name,
            seconds=seconds,
            hz=hz,
            countdown_sec=countdown,
            register=register_manifest,
            hold_after=hold_after,
            parent=None,
        )
        self._record_worker = worker

        def _done(_n: str, _frames: int) -> None:
            self._record_worker = None

        def _fail(_msg: str) -> None:
            self._record_worker = None

        worker.finished_ok.connect(_done)
        worker.failed.connect(_fail)
        worker.start()
        return {"ok": True, "queued": True, "name": name}

    def try_stop_record(self) -> Dict[str, Any]:
        w = self._record_worker
        if w is None or not w.isRunning():
            return {"ok": False, "error": "no active recording"}
        w.request_stop()
        return {"ok": True, "stopping": True}

    def record_status(self) -> Dict[str, Any]:
        w = self._record_worker
        if w is None or not w.isRunning():
            return {"running": False}
        return {"running": True, "name": getattr(w, "_name", "")}
