"""
Thin facade around the Nina hardware controllers used by the UI.

The UI creates exactly one `NinaService`, lazily initializes the
Dynamixel bus on first use, and shares it across the playback and
record workers. All bus access is serialized via `bus_lock` so a
playback worker and a record worker can never race on the serial port.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt

from nina.config.hover_calibration import (
    save_hover_calibration_partial,
)
from nina.config.settings import NinaSettings, load_settings
from nina.controllers.action_runner import ActionRunner
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.config.motor_ids import EXPECTED_DYNAMIXEL_IDS, HOVERBOARD_LEAN_IDS
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    apply_hoverboard_brake_positions,
)
from nina.sensors.battery_ads1115_monitor import BatteryAds1115Monitor
from nina.sensors.obstacle_stop_monitor import ObstacleStopMonitor
from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer
from sirena_ui.workers.autonomy_controller import AutonomyController
from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.face_follow_controller import FaceFollowController
from sirena_ui.workers.slam_worker import SlamWorker
from sirena_ui.workers.face_greeter import FaceGreeter, FaceGreetReceiver
from sirena_ui.workers.vision_worker import VisionWorker


DEFAULT_MOTOR_IDS: List[int] = list(EXPECTED_DYNAMIXEL_IDS)

log = logging.getLogger("sirena_ui.nina_service")


class NinaService:
    def __init__(self, settings: Optional[NinaSettings] = None) -> None:
        if settings is None:
            repo_root = Path(__file__).resolve().parents[2]
            settings = load_settings(repo_root)
        self.settings = settings
        self.dxl = DynamixelManager(
            serial_port=settings.serial_port,
            baudrate=settings.baudrate,
            expected_motor_ids=DEFAULT_MOTOR_IDS,
        )
        self.action_runner = ActionRunner(
            manifest_path=settings.manifest_path,
            actions_dir=settings.actions_dir,
            dxl=self.dxl,
        )
        self.bus_lock = threading.RLock()
        self._bus_ready = False
        self._motor_count = len(DEFAULT_MOTOR_IDS)
        self._drive: Optional[DriveController] = None
        self._face_follow: Optional[FaceFollowController] = None
        self._vision: Optional[VisionWorker] = None
        self._face_greeter: Optional[FaceGreeter] = None
        self._slam: Optional[SlamWorker] = None
        self._autonomy: Optional[AutonomyController] = None
        self._obstacle_monitor: Optional[ObstacleStopMonitor] = None
        self._battery_monitor: Optional[BatteryAds1115Monitor] = None

    @property
    def expected_motor_count(self) -> int:
        return self._motor_count

    @property
    def bus_ready(self) -> bool:
        return self._bus_ready

    def park_hoverboard_brake(self) -> None:
        """Return lean servos to the configured brake pose (safe park)."""
        with self.bus_lock:
            if not self._bus_ready:
                return
            apply_hoverboard_brake_positions(self.dxl, self.settings.hoverboard_axis)

    def preview_hover_lean_positions(self, left: int, right: int) -> None:
        """Command both lean servos to absolute goal ticks (calibration preview)."""
        axis = self.settings.hoverboard_axis
        lid = int(axis.id_left)
        rid = int(axis.id_right)
        with self.bus_lock:
            if not self._bus_ready:
                return
            self.dxl._require_initialized()
            ms = max(0, min(1023, int(axis.moving_speed)))
            self.dxl.sync_write_moving_speed_subset({lid: ms, rid: ms})
            self.dxl.sync_write_goal_position(
                {
                    lid: self.dxl._clamp_pos(int(left)),
                    rid: self.dxl._clamp_pos(int(right)),
                }
            )

    def persist_hover_lean_calibration(
        self,
        *,
        forward_pos_left: Optional[int] = None,
        forward_pos_right: Optional[int] = None,
        backward_pos_left: Optional[int] = None,
        backward_pos_right: Optional[int] = None,
    ) -> None:
        """Append to JSON on disk and refresh in-memory lean goals (no drive rebuild)."""
        updates: Dict[str, int] = {}
        for key, val in (
            ("forward_pos_left", forward_pos_left),
            ("forward_pos_right", forward_pos_right),
            ("backward_pos_left", backward_pos_left),
            ("backward_pos_right", backward_pos_right),
        ):
            if val is None:
                continue
            updates[key] = max(0, min(4095, int(val)))
        if not updates:
            return
        save_hover_calibration_partial(updates)
        new_axis = replace(self.settings.hoverboard_axis, **updates)
        self.settings = replace(self.settings, hoverboard_axis=new_axis)
        if self._drive is not None:
            self._drive.update_hoverboard_axis(new_axis)

    def ensure_bus(self) -> Dict[str, object]:
        """Initialize the bus once, run a non-fatal health check, enable torque."""
        with self.bus_lock:
            first_bus_init = not self._bus_ready
            if not self._bus_ready:
                self.dxl.initialize_bus()
                self._bus_ready = True
            health = self.dxl.run_health_check()
            self.dxl.ensure_joint_mode_for_ids(HOVERBOARD_LEAN_IDS)
            self.dxl.set_torque_all(True)
            if first_bus_init:
                apply_hoverboard_brake_positions(self.dxl, self.settings.hoverboard_axis)
            return {
                "connected": health.connected,
                "detected": health.detected_motors,
                "expected": health.expected_motors,
                "detail": health.detail,
            }

    def start_obstacle_stop_monitor(self) -> None:
        """Start forward HC-SR04 obstacle handling when enabled in settings."""
        if not self.settings.obstacle_stop.enabled:
            return
        if self._obstacle_monitor is not None:
            return
        try:
            mon = ObstacleStopMonitor(self)
            mon.start()
            self._obstacle_monitor = mon
        except Exception as exc:
            log.warning("Obstacle stop monitor did not start: %s", exc)

    def run_obstacle_stop_reaction(self) -> None:
        """JYQD stop, neutral action, lean brake, then US-English gTTS phrase."""
        try:
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            self.drive.stop(drain=True)
        except Exception:
            log.exception("Obstacle stop: drive / face-follow stop failed")

        with self.bus_lock:
            if not self._bus_ready:
                return
            try:
                self.dxl._require_initialized()
            except Exception:
                return
            try:
                apply_hoverboard_brake_positions(
                    self.dxl, self.settings.hoverboard_axis
                )
                self.action_runner.run_named_action(
                    self.settings.neutral_action_name
                )
                apply_hoverboard_brake_positions(
                    self.dxl, self.settings.hoverboard_axis
                )
            except Exception:
                log.exception("Obstacle stop: neutral / brake pose failed")

        phrase = (self.settings.obstacle_stop.tts_text or "").strip()
        if not phrase:
            phrase = "There is an obstacle in my way"
        out = Path(tempfile.gettempdir()) / "nina_obstacle_stop_alert.mp3"
        try:
            AudioGenerator.generate(
                phrase, out, lang="en", tld="us", slow=False
            )
            AudioPlayer().play(out)
        except AudioGeneratorError as exc:
            log.warning("Obstacle stop TTS unavailable: %s", exc)
        except Exception:
            log.exception("Obstacle stop TTS / playback failed")

    def start_battery_ads1115_monitor(self) -> None:
        """Start ADS1115 pack-voltage monitor when enabled in settings."""
        if not self.settings.battery_ads1115.enabled:
            return
        if self._battery_monitor is not None:
            return
        try:
            mon = BatteryAds1115Monitor(self)
            mon.start()
            self._battery_monitor = mon
        except Exception as exc:
            log.warning("Battery ADS1115 monitor did not start: %s", exc)

    def run_low_battery_reaction(self) -> None:
        """JYQD stop, neutral action, lean IDs to ``lean_goal`` (default 2048), gTTS."""
        try:
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            self.drive.stop(drain=True)
        except Exception:
            log.exception("Low battery: drive / face-follow stop failed")

        axis = self.settings.hoverboard_axis
        lid = int(axis.id_left)
        rid = int(axis.id_right)
        ms = max(0, min(1023, int(axis.moving_speed)))
        goal = int(self.settings.battery_ads1115.lean_goal)

        with self.bus_lock:
            if not self._bus_ready:
                return
            try:
                self.dxl._require_initialized()
            except Exception:
                return
            try:
                self.action_runner.run_named_action(
                    self.settings.neutral_action_name
                )
                self.dxl.sync_write_moving_speed_subset({lid: ms, rid: ms})
                self.dxl.sync_write_goal_position({lid: goal, rid: goal})
            except Exception:
                log.exception("Low battery: neutral / lean goal failed")

        phrase = (self.settings.battery_ads1115.tts_text or "").strip()
        if not phrase:
            phrase = "I'm low on battery , please put me on charge"
        out = Path(tempfile.gettempdir()) / "nina_low_battery_alert.mp3"
        try:
            AudioGenerator.generate(
                phrase, out, lang="en", tld="us", slow=False
            )
            AudioPlayer().play(out)
        except AudioGeneratorError as exc:
            log.warning("Low battery TTS unavailable: %s", exc)
        except Exception:
            log.exception("Low battery TTS / playback failed")

    @property
    def drive(self) -> DriveController:
        """Lazy singleton for the drive controller (hoverboard lean via Dynamixel)."""

        if self._drive is None:
            nav_settings = self.settings.navigation
            nav_manager = HoverboardAxisDrive(
                self.dxl,
                self.bus_lock,
                self.settings.hoverboard_axis,
                nav_settings,
            )
            # Manual drive duty is fixed in DriveController (no slider); do not
            # seed the GUI state from nav_settings.default_speed_percent.
            self._drive = DriveController(
                nav_manager=nav_manager,
                default_speed_percent=None,
            )
        return self._drive

    @property
    def face_follow(self) -> FaceFollowController:
        """Lazy singleton for vision-guided person follow (same loop as Qt Vision)."""
        if self._face_follow is None:
            self._face_follow = FaceFollowController(self.drive, parent=None)
        return self._face_follow

    @property
    def vision(self) -> VisionWorker:
        """Lazy singleton for the camera + face/object detection worker.

        Created on first access so the GUI doesn't pay the
        OpenCV/Ultralytics import + camera-open cost until the user
        actually navigates to the Vision screen.
        """
        if self._vision is None:
            w = VisionWorker()
            g = FaceGreeter(parent=w)
            recv = FaceGreetReceiver(g, parent=w)
            w.faces_recognized.connect(
                recv.on_faces_recognized,
                type=Qt.QueuedConnection,
            )
            self._vision = w
            self._face_greeter = g
        return self._vision

    def reset_face_greet_cooldown(self) -> None:
        """Forget per-person greeting cooldown (e.g. when opening Vision)."""
        if self._face_greeter is not None:
            self._face_greeter.reset_cooldown()

    @property
    def face_greeter(self) -> Optional[FaceGreeter]:
        """Lazy FaceGreeter; exists after first ``vision`` access."""
        return self._face_greeter

    @property
    def slam(self) -> SlamWorker:
        """Lazy singleton for the lidar + SLAM worker.

        Created on first access so the GUI doesn't pay the BreezySLAM
        import / lidar probe cost until the user opens the Map screen
        or enables autonomous mode. The actual lidar driver is
        chosen by `nina.sensors.lidar_factory.build_lidar` from
        `LidarSettings.model` (S2E by default; A1 selectable for
        legacy bots) so the worker never has to know which physical
        scanner is plugged in.
        """
        if self._slam is None:
            self._slam = SlamWorker(
                self.settings.slam,
                lidar_settings=self.settings.lidar,
            )
        return self._slam

    @property
    def autonomy(self) -> AutonomyController:
        """Lazy singleton for the autonomous-navigation controller.

        Owns the short-range sensors (HC-SR04 ring, GP2Y0E02B IR,
        RealSense D435) and the AutonomousPilot. Lidar scans come from
        `self.slam` so we don't double-open the serial port.
        """
        if self._autonomy is None:
            self._autonomy = AutonomyController(
                drive=self.drive,
                slam=self.slam,
                settings=self.settings.autonomy,
                goto_settings=self.settings.goto,
            )
        return self._autonomy

    def shutdown(self) -> None:
        if self._battery_monitor is not None:
            try:
                self._battery_monitor.stop()
            except Exception:
                pass
            self._battery_monitor = None
        if self._obstacle_monitor is not None:
            try:
                self._obstacle_monitor.stop()
            except Exception:
                pass
            self._obstacle_monitor = None
        with self.bus_lock:
            # Order matters: autonomy depends on slam (lidar) and drive,
            # so it has to come down first - that also parks the wheels.
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            if self._autonomy is not None:
                try:
                    self._autonomy.shutdown()
                except Exception:
                    pass
                self._autonomy = None
            if self._slam is not None:
                try:
                    self._slam.shutdown()
                except Exception:
                    pass
                self._slam = None
            if self._vision is not None:
                try:
                    self._vision.shutdown()
                except Exception:
                    pass
                self._vision = None
                self._face_greeter = None
            self._face_follow = None
            if self._drive is not None:
                try:
                    self._drive.shutdown()
                except Exception:
                    pass
                self._drive = None
            try:
                self.dxl.close()
            finally:
                self._bus_ready = False

    def list_actions(self) -> Dict[str, str]:
        return self.action_runner.list_actions()

    def action_path(self, name: str) -> Path:
        return self.settings.actions_dir / self.list_actions()[name]

    def delete_action(
        self,
        name: str,
        *,
        delete_recording: bool = True,
        delete_audio: bool = False,
    ) -> Dict[str, Any]:
        """Remove an action from the manifest (UI-facing wrapper).

        Mirrors `ActionRunner.delete_action` but goes through the same
        `bus_lock` other UI workers use, so a delete cannot interleave
        with a playback or recording session that's actively touching
        the manifest.
        """
        with self.bus_lock:
            return self.action_runner.delete_action(
                name,
                delete_recording=delete_recording,
                delete_audio=delete_audio,
            )

    def action_audio_path(self, name: str) -> Optional[Path]:
        """
        Resolve the audio file to play alongside an action, if any.

        Lookup order:
          1. Explicit `audio` field on the manifest entry.
          2. Convention: `nina/actions/audio/<name>.{wav,mp3}`.
        """
        rel = self.action_runner.get_action_audio(name)
        if rel:
            candidate = self.settings.actions_dir / rel
            if candidate.exists():
                return candidate
        for ext in (".wav", ".mp3"):
            candidate = self.settings.actions_dir / "audio" / f"{name}{ext}"
            if candidate.exists():
                return candidate
        return None

    def action_audio_offset(self, name: str) -> float:
        """Per-action delay (seconds) before the audio clip is fired."""
        return self.action_runner.get_action_audio_offset(name)

    # ---------- audio authoring (used by the GUI audio editor) ----------

    @staticmethod
    def audio_generator_available() -> Optional[str]:
        """Return None if gTTS is importable, else an error message."""
        return AudioGenerator.is_available()

    def get_action_audio_info(self, name: str) -> Dict[str, Any]:
        """Bundle current audio state for the editor dialog."""
        rel = self.action_runner.get_action_audio(name)
        path = self.action_audio_path(name)
        return {
            "audio_rel": rel,
            "audio_path": path,
            "audio_offset": self.action_audio_offset(name),
        }

    def generate_action_audio(
        self,
        name: str,
        text: str,
        *,
        lang: str = "en",
        tld: str = "us",
        offset: float = 0.0,
        slow: bool = False,
    ) -> Path:
        """
        Render an MP3 for `name` with gTTS, save to
        `nina/actions/audio/<name>.mp3`, and update the manifest entry
        (audio + audio_offset) in one shot.
        """
        audio_dir = self.settings.actions_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        rel = f"audio/{name}.mp3"
        out_path = self.settings.actions_dir / rel
        AudioGenerator.generate(text, out_path, lang=lang, tld=tld, slow=slow)
        self.action_runner.set_action_audio(name, rel, audio_offset=offset)
        return out_path

    def set_action_audio_offset(self, name: str, offset: float) -> None:
        """Update only the audio_offset for an action that already has audio."""
        rel = self.action_runner.get_action_audio(name)
        if not rel:
            raise ValueError(
                f"Action '{name}' has no audio clip; generate one first."
            )
        self.action_runner.set_action_audio(name, rel, audio_offset=offset)

    def clear_action_audio(self, name: str) -> None:
        """Remove the audio mapping (and offset) from an action."""
        self.action_runner.set_action_audio(name, None)

    def preview_audio(self, audio_path: Path) -> None:
        """Play an audio file once (used by the editor 'Preview' button)."""
        AudioPlayer().play(audio_path)
