"""Background QThread that plays a named action via the smooth playback path."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QThread, pyqtSignal

from nina.controllers.action_runner import action_playback_speed
from nina.sensors.ads1115 import is_battery_motion_blocked
from nina.services.audio_player import AudioPlayer
from nina.services.sensor_alert_audio import maybe_speak_low_battery
from sirena_ui.workers.error_hints import explain_error
from sirena_ui.workers.nina_service import NinaService


class PlaybackWorker(QThread):
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: NinaService,
        action_name: str,
        smooth: bool = True,
        sub_hz: float = 50.0,
        max_speed: int = 1023,
        speed: Optional[float] = None,
        audio_path: Optional[Path] = None,
        audio_offset_sec: float = 0.0,
        *,
        ensure_before_play: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._action_name = action_name
        self._smooth = smooth
        self._sub_hz = sub_hz
        self._max_speed = max_speed
        self._speed = action_playback_speed() if speed is None else speed
        self._audio_path = audio_path
        self._audio_offset_sec = max(0.0, float(audio_offset_sec))
        self._audio_player = AudioPlayer()
        self._audio_timer: Optional[threading.Timer] = None
        self._eye_timer: Optional[threading.Timer] = None
        #: Call ``ensure_bus`` on the **worker** thread before motion (tablet delegate path).
        self._ensure_before_play = ensure_before_play

    def _schedule_eye(self) -> None:
        eid = self._service.action_runner.get_action_eye_expression(self._action_name)
        if eid is None:
            return
        offset = self._service.action_runner.get_action_eye_offset(self._action_name)

        def _fire() -> None:
            try:
                self._service.send_eye_expression(eid)
            except Exception:
                pass

        if offset <= 0.0:
            _fire()
            return
        timer = threading.Timer(offset, _fire)
        timer.daemon = True
        self._eye_timer = timer
        timer.start()

    def _schedule_audio(self) -> None:
        if self._audio_path is None:
            return
        if self._audio_offset_sec <= 0.0:
            self._audio_player.play(self._audio_path)
            return
        timer = threading.Timer(
            self._audio_offset_sec,
            self._audio_player.play,
            args=(self._audio_path,),
        )
        timer.daemon = True
        self._audio_timer = timer
        timer.start()

    def run(self) -> None:
        if is_battery_motion_blocked():
            try:
                maybe_speak_low_battery()
            except Exception:
                pass
            self.failed.emit(
                "Low battery — motion blocked. Please charge the pack."
            )
            return
        if self._ensure_before_play:
            try:
                self._service.ensure_bus()
            except Exception as exc:  # pragma: no cover - reported back to UI
                self.failed.emit(explain_error(exc, self._service.settings))
                return
        try:
            self._schedule_audio()
            self._schedule_eye()
            with self._service.bus_lock:
                self._service.action_runner.run_named_action(
                    self._action_name,
                    smooth=self._smooth,
                    sub_hz=self._sub_hz,
                    max_speed=self._max_speed,
                    speed=self._speed,
                )
            self.finished_ok.emit(self._action_name)
        except Exception as exc:  # pragma: no cover - reported back to UI
            if self._audio_timer is not None:
                self._audio_timer.cancel()
            if self._eye_timer is not None:
                self._eye_timer.cancel()
            self._audio_player.stop_all()
            self.failed.emit(explain_error(exc, self._service.settings))
