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
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt

from nina.config.settings import NinaSettings, load_settings
from nina.controllers.action_runner import ActionRunner, action_playback_speed
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.config.motor_ids import EXPECTED_DYNAMIXEL_IDS, HOVERBOARD_LEAN_IDS
from nina.sensors.ads1115 import (
    format_pack_voltage,
    get_battery_snapshot,
    is_battery_motion_blocked,
    set_battery_latched_low,
)
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    apply_hoverboard_brake_positions,
)
from nina.sensors.battery_ads1115_monitor import BatteryAds1115Monitor
from nina.sensors.touch_at42qt2120_monitor import TouchAt42qt2120Monitor
from nina.sensors.mpu9250 import Mpu9250DriftMonitor, is_imu_monitor_enabled
from nina.sensors.ir_obstacle_stop_monitor import IrObstacleStopMonitor
from nina.sensors.esp32_trigger_monitor import Esp32TriggerMonitor
from nina.services.audio_generator import AudioGenerator
from nina.services.audio_player import AudioPlayer
from nina.services.sensor_alert_audio import (
    maybe_speak_low_battery,
    maybe_speak_obstacle_alert,
    maybe_speak_touch_alert,
)
from sirena_ui.workers.autonomy_controller import AutonomyController
from nina.movements.store import MovementStore, default_movements_path
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
        self._ir_obstacle_monitor: Optional[IrObstacleStopMonitor] = None
        self._ir_obstacle_start_detail: Optional[str] = None
        self._battery_monitor: Optional[BatteryAds1115Monitor] = None
        self._touch_monitor: Optional[TouchAt42qt2120Monitor] = None
        self._esp32_trigger_monitor: Optional[Esp32TriggerMonitor] = None
        self._esp32_trigger_start_detail: Optional[str] = None
        self._esp32_reaction_thread: Optional[threading.Thread] = None
        self._esp32_reaction_stop = threading.Event()
        self._esp32_reaction_audio: Optional[AudioPlayer] = None
        self._esp32_reaction_audio_timer: Optional[threading.Timer] = None
        self._esp32_reaction_lock = threading.Lock()
        self._imu_monitor: Optional[Mpu9250DriftMonitor] = None
        self._movement_store: Optional[MovementStore] = None

    @property
    def movement_store(self) -> MovementStore:
        if self._movement_store is None:
            path = self.settings.actions_dir / "saved_movements.json"
            self._movement_store = MovementStore(path)
        return self._movement_store

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

    def is_hoverboard_in_motion(self) -> bool:
        """True when drive layer reports wheels away from idle/brake neutral."""
        drv = self._drive
        if drv is None:
            return False
        return bool(getattr(drv, "is_in_motion", lambda: False)())

    def start_ir_obstacle_stop_monitor(self) -> None:
        """Start GP2Y0E02B IR obstacle handling when enabled."""
        if not self.settings.ir_obstacle_stop.enabled:
            return
        if self._ir_obstacle_monitor is not None:
            return
        try:
            mon = IrObstacleStopMonitor(
                self,
                in_motion_fn=self.is_hoverboard_in_motion,
            )
            mon.start()
            self._ir_obstacle_monitor = mon
            self._ir_obstacle_start_detail = None
        except Exception as exc:
            self._ir_obstacle_start_detail = str(exc)
            log.warning("IR obstacle stop monitor did not start: %s", exc)

    def ir_obstacle_status(self) -> Dict[str, Any]:
        """Status for the standalone IR obstacle-stop monitor."""
        if not self.settings.ir_obstacle_stop.enabled:
            return {
                "enabled": False,
                "running": False,
                "sensor_open": False,
                "blocked": False,
                "distance_mm": None,
                "threshold_mm": int(self.settings.ir_obstacle_stop.threshold_mm),
                "detail": "IR obstacle stop disabled",
            }
        mon = self._ir_obstacle_monitor
        if mon is None:
            detail = self._ir_obstacle_start_detail or "IR obstacle monitor not started"
            return {
                "enabled": True,
                "running": False,
                "sensor_open": False,
                "blocked": False,
                "distance_mm": None,
                "threshold_mm": int(self.settings.ir_obstacle_stop.threshold_mm),
                "detail": detail,
            }
        try:
            st = mon.status()
        except Exception as exc:
            return {
                "enabled": True,
                "running": False,
                "sensor_open": False,
                "blocked": False,
                "distance_mm": None,
                "threshold_mm": int(self.settings.ir_obstacle_stop.threshold_mm),
                "detail": f"IR status failed: {exc}",
            }
        st["enabled"] = True
        return st

    def run_obstacle_stop_reaction(self) -> None:
        """JYQD stop, immediate obstacle alert, then neutral action + lean brake."""
        try:
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            self.drive.stop(drain=True)
        except Exception:
            log.exception("Obstacle stop: drive / face-follow stop failed")

        phrase = (self.settings.ir_obstacle_stop.tts_text or "").strip()
        try:
            maybe_speak_obstacle_alert(phrase=phrase)
        except Exception:
            log.exception("Obstacle stop alert playback failed")

        if not self._bus_ready:
            try:
                self.ensure_bus()
            except Exception:
                log.exception("Obstacle stop: bus init failed")
                return

        with self.bus_lock:
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

    def start_esp32_trigger_monitor(self) -> bool:
        """Start GPIO poll for ESP32 trigger (default BCM 17 / pin 11 → namaste)."""
        if not self.settings.esp32_trigger.enabled:
            log.debug(
                "ESP32 trigger monitor disabled "
                "(set NINA_ESP32_TRIGGER_ENABLE=1 to enable)"
            )
            return False
        mon = self._esp32_trigger_monitor
        if mon is not None and mon.is_running():
            return True
        try:
            mon = Esp32TriggerMonitor(self)
            mon.start()
            self._esp32_trigger_monitor = mon
            self._esp32_trigger_start_detail = None
            return True
        except Exception as exc:
            self._esp32_trigger_monitor = None
            self._esp32_trigger_start_detail = str(exc)
            log.warning("ESP32 trigger monitor did not start: %s", exc)
            return False

    def esp32_trigger_status(self) -> Dict[str, Any]:
        """Status for the ESP32 GPIO trigger monitor."""
        s = self.settings.esp32_trigger
        if not s.enabled:
            return {
                "enabled": False,
                "running": False,
                "gpio_bcm": int(s.gpio_bcm),
                "line_high": None,
                "armed": False,
                "action_name": str(s.action_name),
                "fire_count": 0,
                "detail": "ESP32 trigger disabled",
            }
        mon = self._esp32_trigger_monitor
        if mon is None:
            detail = self._esp32_trigger_start_detail or "ESP32 trigger monitor not started"
            return {
                "enabled": True,
                "running": False,
                "gpio_bcm": int(s.gpio_bcm),
                "line_high": None,
                "armed": False,
                "action_name": str(s.action_name),
                "fire_count": 0,
                "detail": detail,
            }
        try:
            st = mon.status()
        except Exception as exc:
            return {
                "enabled": True,
                "running": False,
                "gpio_bcm": int(s.gpio_bcm),
                "line_high": None,
                "armed": False,
                "action_name": str(s.action_name),
                "fire_count": 0,
                "detail": f"ESP32 status failed: {exc}",
            }
        st["enabled"] = True
        return st

    def is_esp32_reaction_active(self) -> bool:
        """True while an ESP32-triggered arm action is playing."""
        t = self._esp32_reaction_thread
        return t is not None and t.is_alive()

    def start_esp32_trigger_reaction(self, action_name: str) -> None:
        """Stop drive and play a named gesture (non-blocking for GPIO monitor)."""
        with self._esp32_reaction_lock:
            if self.is_esp32_reaction_active():
                log.info("ESP32 trigger: action already running — ignoring re-fire")
                return
            self._esp32_reaction_stop.clear()
            thr = threading.Thread(
                target=self._run_esp32_trigger_reaction,
                args=(action_name,),
                name="nina_esp32_trigger_reaction",
                daemon=True,
            )
            self._esp32_reaction_thread = thr
            thr.start()

    def request_esp32_trigger_release_stop(self) -> None:
        """GPIO went LOW during an ESP32 action — ease arms back to neutral."""
        if not self.is_esp32_reaction_active():
            return
        if self._esp32_reaction_stop.is_set():
            return
        self._esp32_reaction_stop.set()
        log.info("ESP32 trigger release — ramping action to neutral")
        audio = self._esp32_reaction_audio
        if audio is not None:
            try:
                audio.stop_all()
            except Exception:
                log.debug("ESP32 trigger: audio stop failed", exc_info=True)
        timer = self._esp32_reaction_audio_timer
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def run_esp32_trigger_reaction(self, action_name: str) -> None:
        """Blocking ESP32 reaction (tests / CLI). Prefer :meth:`start_esp32_trigger_reaction`."""
        self.start_esp32_trigger_reaction(action_name)
        t = self._esp32_reaction_thread
        if t is not None:
            t.join()

    def _run_esp32_trigger_reaction(self, action_name: str) -> None:
        """Stop drive and play a named gesture (with manifest audio if configured)."""
        audio_player = AudioPlayer()
        audio_timer: Optional[threading.Timer] = None
        self._esp32_reaction_audio = audio_player
        self._esp32_reaction_audio_timer = None
        try:
            try:
                if self._face_follow is not None:
                    try:
                        self._face_follow.stop()
                    except Exception:
                        pass
                self.drive.stop(drain=True)
            except Exception:
                log.exception("ESP32 trigger: drive / face-follow stop failed")

            if is_battery_motion_blocked():
                try:
                    maybe_speak_low_battery()
                except Exception:
                    pass
                log.warning("ESP32 trigger: motion blocked (low battery)")
                return

            if not self._bus_ready:
                log.info("ESP32 trigger: Dynamixel bus not ready — initializing")
                try:
                    self.ensure_bus()
                except Exception:
                    log.exception("ESP32 trigger: bus init failed")
                    return

            audio_path = self.action_audio_path(action_name)
            audio_offset = self.action_audio_offset(action_name) if audio_path else 0.0
            trig = self.settings.esp32_trigger
            release_speed = max(
                1,
                int(1023 * float(trig.release_ramp_speed_pct)),
            )

            try:
                with self.bus_lock:
                    self.dxl._require_initialized()
                    if audio_path is not None:
                        if audio_offset <= 0.0:
                            audio_player.play(audio_path)
                        else:
                            audio_timer = threading.Timer(
                                audio_offset,
                                audio_player.play,
                                args=(audio_path,),
                            )
                            audio_timer.daemon = True
                            audio_timer.start()
                            self._esp32_reaction_audio_timer = audio_timer
                    play_speed = action_playback_speed()
                    log.info(
                        "ESP32 trigger: playing '%s' smooth speed=%.2f",
                        action_name,
                        play_speed,
                    )
                    completed = self.action_runner.run_named_action(
                        action_name,
                        smooth=True,
                        sub_hz=50.0,
                        max_speed=1023,
                        speed=play_speed,
                        stop_event=self._esp32_reaction_stop,
                        release_neutral_name=self.settings.neutral_action_name,
                        release_ramp_sec=float(trig.release_ramp_sec),
                        release_max_speed=release_speed,
                    )
                    if completed:
                        log.info("ESP32 trigger: action '%s' finished", action_name)
                    else:
                        log.info(
                            "ESP32 trigger: action '%s' released to neutral",
                            action_name,
                        )
            except Exception:
                log.exception("ESP32 trigger: action '%s' failed", action_name)
        finally:
            if audio_timer is not None:
                audio_timer.cancel()
            audio_player.stop_all()
            self._esp32_reaction_audio = None
            self._esp32_reaction_audio_timer = None
            self._esp32_reaction_thread = None

    def start_battery_ads1115_monitor(self) -> None:
        """Start ADS1115 pack-voltage monitor when enabled in settings."""
        if not self.settings.battery_ads1115.enabled:
            log.info(
                "Battery ADS1115 monitor disabled "
                "(set NINA_BATTERY_ADS1115_ENABLE=1 for UI pack voltage)"
            )
            return
        mon = self._battery_monitor
        if mon is not None and mon.is_running():
            return
        if mon is not None:
            try:
                mon.stop()
            except Exception:
                pass
            self._battery_monitor = None
        try:
            mon = BatteryAds1115Monitor(self)
            mon.start()
            self._battery_monitor = mon
        except Exception as exc:
            log.warning("Battery ADS1115 monitor did not start: %s", exc)

    def touch_monitor_running(self) -> bool:
        """True when the AT42QT2120 background poll thread is active."""
        mon = self._touch_monitor
        return mon is not None and mon.is_running()

    def start_touch_at42qt2120_monitor(self) -> bool:
        """Start AT42QT2120 touch monitor when enabled in settings.

        Safe to call repeatedly after boot — returns False until the chip
        answers on I²C (e.g. ``/dev/i2c-7`` not ready at UI startup).
        """
        if not self.settings.touch_at42qt2120.enabled:
            log.debug(
                "AT42QT2120 touch monitor disabled "
                "(set NINA_TOUCH_AT42QT2120_ENABLE=1 to enable)"
            )
            return False
        if self.touch_monitor_running():
            return True
        if self._touch_monitor is not None:
            self._touch_monitor = None
        try:
            mon = TouchAt42qt2120Monitor(self)
            mon.start()
            self._touch_monitor = mon
            return True
        except Exception as exc:
            log.warning("AT42QT2120 touch monitor did not start: %s", exc)
            return False

    def _park_all_motors_at_goal(self, goal: int) -> None:
        """Sync-write goal position for Dynamixel IDs 1–13 (neutral pose)."""
        axis = self.settings.hoverboard_axis
        ms = max(0, min(1023, int(axis.moving_speed)))
        goals = {int(sid): int(goal) for sid in EXPECTED_DYNAMIXEL_IDS}
        with self.bus_lock:
            if not self._bus_ready:
                return
            try:
                self.dxl._require_initialized()
            except Exception:
                return
            try:
                self.dxl.set_moving_speed_all(ms)
                self.dxl.sync_write_goal_position(goals)
            except Exception:
                log.exception("Park all motors at goal %s failed", goal)

    def run_touch_reaction(self) -> None:
        """Stop drive, speak touch alert, park motors 1–13 at neutral (2048)."""
        try:
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            self.drive.stop(drain=True)
        except Exception:
            log.exception("Touch: drive / face-follow stop failed")

        phrase = (self.settings.touch_at42qt2120.tts_text or "").strip()
        try:
            maybe_speak_touch_alert(phrase=phrase)
        except Exception:
            log.exception("Touch alert playback failed")

        goal = max(0, min(4095, int(self.settings.touch_at42qt2120.motor_goal)))
        self._park_all_motors_at_goal(goal)

    def start_mpu9250_imu_monitor(self) -> None:
        """Start MPU-9250 drift sampler when ``NINA_IMU_MPU9250_ENABLE`` is set.

        Once the sampler is running we also wire its yaw drift + begin/end
        straight-leg hooks into the hoverboard drive so straight-line pulses
        self-correct toward zero yaw without any extra UI plumbing.
        """
        if not is_imu_monitor_enabled():
            return
        if self._imu_monitor is not None:
            return
        try:
            mon = Mpu9250DriftMonitor()
            mon.start()
            self._imu_monitor = mon
        except Exception as exc:
            log.warning("MPU-9250 IMU monitor did not start: %s", exc)
            return
        self._apply_imu_hooks_to_drive()

    @property
    def imu_monitor(self) -> Optional[Mpu9250DriftMonitor]:
        return self._imu_monitor

    def imu_straight_begin(self) -> None:
        if self._imu_monitor is not None:
            self._imu_monitor.begin_straight_leg()

    def imu_straight_end(self) -> None:
        if self._imu_monitor is not None:
            self._imu_monitor.end_straight_leg()

    def _imu_yaw_drift_deg(self) -> Optional[float]:
        """Sampler the drive layer calls during straight holds (None when idle)."""
        mon = self._imu_monitor
        if mon is None:
            return None
        try:
            s = mon.snapshot()
        except Exception:
            return None
        if not s.ok or s.drift_side == "n/a":
            return None
        return float(s.yaw_drift_deg)

    def _imu_yaw_rate_dps(self) -> Optional[float]:
        """Instantaneous yaw rate (deg/s) for the drive layer's active settle.

        Used by :meth:`HoverboardAxisDrive._active_settle_until_still`
        to wait until the chassis is actually stationary (not just
        commanded to brake) before sampling drift / running a
        correction step. Returns ``None`` when no MPU-9250 monitor is
        running so the drive layer falls back to the fixed-timer
        settle. Unlike :meth:`_imu_yaw_drift_deg` this samples even
        when ``drift_side == "n/a"`` (i.e. between straight legs)
        because the active-settle is paused-state monitoring, not a
        drift measurement.
        """
        mon = self._imu_monitor
        if mon is None:
            return None
        try:
            s = mon.snapshot()
        except Exception:
            return None
        if not s.ok:
            return None
        return float(s.yaw_rate_dps)

    def _apply_imu_hooks_to_drive(self) -> None:
        """Push IMU hooks into the drive layer (no-op when drive isn't built yet)."""
        drv = self._drive
        if drv is None:
            return
        nav = drv.nav_manager() if hasattr(drv, "nav_manager") else None
        if nav is None or not hasattr(nav, "set_imu_hooks"):
            return
        nav.set_imu_hooks(
            yaw_drift_fn=self._imu_yaw_drift_deg,
            begin_straight_fn=self.imu_straight_begin,
            end_straight_fn=self.imu_straight_end,
            yaw_rate_fn=self._imu_yaw_rate_dps,
        )

    def is_battery_low_latched(self) -> bool:
        return is_battery_motion_blocked()

    def battery_pack_voltage_display(self) -> str:
        return format_pack_voltage(get_battery_snapshot())

    def run_low_battery_reaction(self) -> None:
        """Stop drive, park motors 1–13 at neutral (2048), speak low-battery TTS.

        Motion stays blocked (``is_battery_motion_blocked()`` returns True)
        until the pack recovers to ``clear_voltage_v`` — the monitor thread
        unlatches on its own once the voltage rises. The same espeak helper
        (:func:`nina.services.sensor_alert_audio.maybe_speak_low_battery`)
        is used here, in the per-0.2 V repeat warning, and in the worker
        refusals so the operator always hears the same phrase.
        """
        set_battery_latched_low(True)
        try:
            if self._face_follow is not None:
                try:
                    self._face_follow.stop()
                except Exception:
                    pass
            self.drive.stop(drain=True)
        except Exception:
            log.exception("Low battery: drive / face-follow stop failed")

        goal = max(0, min(4095, int(self.settings.battery_ads1115.lean_goal)))
        self._park_all_motors_at_goal(goal)

        try:
            maybe_speak_low_battery(
                phrase=(self.settings.battery_ads1115.tts_text or "").strip()
            )
        except Exception:
            log.exception("Low battery alert playback failed")

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
            # If the IMU monitor is already running, wire its drift sampler /
            # begin-end hooks straight into the new nav layer so the first
            # straight pulse benefits without waiting for another bring-up tick.
            if self._imu_monitor is not None and hasattr(
                nav_manager, "set_imu_hooks"
            ):
                nav_manager.set_imu_hooks(
                    yaw_drift_fn=self._imu_yaw_drift_deg,
                    begin_straight_fn=self.imu_straight_begin,
                    end_straight_fn=self.imu_straight_end,
                    yaw_rate_fn=self._imu_yaw_rate_dps,
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
        if self._imu_monitor is not None:
            try:
                self._imu_monitor.stop()
            except Exception:
                pass
            self._imu_monitor = None
        if self._battery_monitor is not None:
            try:
                self._battery_monitor.stop()
            except Exception:
                pass
            self._battery_monitor = None
        if self._touch_monitor is not None:
            try:
                self._touch_monitor.stop()
            except Exception:
                pass
            self._touch_monitor = None
        if self._esp32_trigger_monitor is not None:
            try:
                self._esp32_trigger_monitor.stop()
            except Exception:
                pass
            self._esp32_trigger_monitor = None
        if self._ir_obstacle_monitor is not None:
            try:
                self._ir_obstacle_monitor.stop()
            except Exception:
                pass
            self._ir_obstacle_monitor = None
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
