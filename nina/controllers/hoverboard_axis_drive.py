"""
Drive Nina locomotion by tilting hoverboard driver modules via Dynamixel MX-28.

Primary Nina locomotion: lean servos on the Dynamixel bus (joint mode). API subset
matches ``NavigationManager`` as used by ``DriveController``, autonomy, and goto.

**Straight forward / straight backward (D-pad W/S, bench straight, ``forward()`` /
``backward()``):** before applying FWD/REV lean goals, both lean servos move to
the straight-line **prime** position (default raw **2048**) and the driver waits
until present position is within tolerance or **2 s** elapses—then FWD/BACK
goals are applied. Pivots (A/D) still use ``stop()`` + settle then opposing leans.
Override with ``NINA_HOVER_STRAIGHT_PRIME_POS``, ``NINA_HOVER_STRAIGHT_PRIME_SEC``,
``NINA_HOVER_STRAIGHT_PRIME_TOL_TICKS``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import DynamixelManager, REG_PRESENT_POS

log = logging.getLogger("nina.hoverboard_axis")

_POS_SPAN_DEG = 300.0
# Extra raw ticks past calibrated ``forward_pos_*`` toward drive (symmetric straight FWD only).
_STRAIGHT_FWD_EXTRA_TICKS = 14


def _straight_prime_goal_ticks() -> int:
    try:
        return max(0, min(4095, int(os.environ.get("NINA_HOVER_STRAIGHT_PRIME_POS", "2048"))))
    except ValueError:
        return 2048


def _straight_prime_timeout_sec() -> float:
    try:
        return max(0.05, min(5.0, float(os.environ.get("NINA_HOVER_STRAIGHT_PRIME_SEC", "2.0"))))
    except ValueError:
        return 2.0


def _straight_prime_tol_ticks() -> int:
    try:
        return max(1, min(512, int(os.environ.get("NINA_HOVER_STRAIGHT_PRIME_TOL_TICKS", "32"))))
    except ValueError:
        return 32


def _nudge_goal_from_brake(goal: int, brake: int, push: int) -> int:
    """Move *goal* *push* raw ticks away from *brake* (if they differ)."""
    if push <= 0:
        return goal
    if goal > brake:
        return goal + push
    if goal < brake:
        return goal - push
    return goal


def apply_hoverboard_brake_positions(dxl: DynamixelManager, axis_cfg: HoverboardAxisSettings) -> None:
    """Command lean servos to the configured brake pose (boot / stopped)."""
    dxl._require_initialized()
    lid = int(axis_cfg.id_left)
    rid = int(axis_cfg.id_right)
    left = dxl._clamp_pos(int(axis_cfg.brake_pos_left))
    right = dxl._clamp_pos(int(axis_cfg.brake_pos_right))
    ms = max(0, min(1023, int(axis_cfg.moving_speed)))
    dxl.sync_write_moving_speed_subset({lid: ms, rid: ms})
    dxl.sync_write_goal_position({lid: left, rid: right})


_POS_SCALE = 4096.0 / _POS_SPAN_DEG


class HoverboardAxisDrive:
    """Lean servos: straight lines use FWD/REV goals; pivots use those goals in opposition."""

    DRIVER_LABEL = "Hoverboard lean — Dynamixel MX-28 (ID 12+13)"
    DIR_FORWARD = "forward"
    DIR_BACKWARD = "backward"
    SIDE_LEFT = "left"
    SIDE_RIGHT = "right"

    def __init__(
        self,
        dxl: DynamixelManager,
        bus_lock: threading.RLock,
        axis_cfg: HoverboardAxisSettings,
        nav_cfg,
    ) -> None:
        self._dxl = dxl
        self._bus_lock = bus_lock
        self._axis = axis_cfg
        self.config = nav_cfg
        self._invert_left = bool(getattr(nav_cfg, "invert_left_dir", False))
        self._invert_right = bool(getattr(nav_cfg, "invert_right_dir", False))
        self._invert_left_override: Optional[bool] = None
        self._invert_right_override: Optional[bool] = None
        self._left_id = int(axis_cfg.id_left)
        self._right_id = int(axis_cfg.id_right)
        self._brake_left = 2048
        self._brake_right = 2048
        self._is_initialized = False
        # Straight FWD/BACK: prime lean to center (2048) once per new straight run.
        self._last_straight_key: Optional[str] = None

    # ------------------------------------------------------------------
    def update_axis_config(self, axis_cfg: HoverboardAxisSettings) -> None:
        """Refresh FWD/REV (and related) goals after motion calibration save."""
        self._axis = axis_cfg

    def initialize(self) -> None:
        if self._is_initialized:
            return
        with self._bus_lock:
            self._brake_left = self._dxl._clamp_pos(
                int(self._axis.brake_pos_left)
            )
            self._brake_right = self._dxl._clamp_pos(
                int(self._axis.brake_pos_right)
            )
            apply_hoverboard_brake_positions(self._dxl, self._axis)
        self._is_initialized = True
        log.info(
            "HoverboardAxisDrive init brake L(id%s)=%s R(id%s)=%s tilt=%s°",
            self._left_id,
            self._brake_left,
            self._right_id,
            self._brake_right,
            self._axis.tilt_deg,
        )

    def shutdown(self) -> None:
        try:
            self.emergency_stop(routine_shutdown=True)
        finally:
            self._is_initialized = False
            self._last_straight_key = None

    def set_status(self, mode: str) -> None:
        return

    def set_invert_left(self, on: bool) -> None:
        self._invert_left_override = bool(on)
        log.info("hoverboard invert_left runtime=%s", bool(on))

    def set_invert_right(self, on: bool) -> None:
        self._invert_right_override = bool(on)
        log.info("hoverboard invert_right runtime=%s", bool(on))

    def get_invert_left(self) -> bool:
        if self._invert_left_override is not None:
            return self._invert_left_override
        return self._invert_left

    def get_invert_right(self) -> bool:
        if self._invert_right_override is not None:
            return self._invert_right_override
        return self._invert_right

    def _eff_sign_left(self) -> int:
        base = -1 if self.get_invert_left() else 1
        return int(self._axis.sign_left * base)

    def _eff_sign_right(self) -> int:
        base = -1 if self.get_invert_right() else 1
        return int(self._axis.sign_right * base)

    def _resolve_speed(self, speed_percent: Optional[int]) -> int:
        if speed_percent is None:
            return max(
                0,
                min(100, int(getattr(self.config, "default_speed_percent", 8))),
            )
        return max(0, min(100, int(speed_percent)))

    def _speed_to_delta_raw(self, speed_pct: int) -> int:
        sp = max(0, min(100, int(speed_pct)))
        scale = max(0.2, sp / 100.0)
        deg = float(self._axis.tilt_deg) * scale
        raw = int(round(abs(deg) * _POS_SCALE))
        return max(2, raw)

    def prime_turn_left_straight(self, speed: int) -> None:
        return

    def engage_brake(self) -> None:
        """Hold brake pose (lean servos at configured brake goals)."""
        self.stop()

    def release_brake(self) -> None:
        """Logical brake off; hoverboard lean axes need no extra action here."""

    # ------------------------------------------------------------------
    # Forward pulse (removed from product): UI uses continuous ``forward()`` /
    # ``drive_continuous`` + ``set_wheels``. Prior coast↔FWD pulse thread + ramps
    # lived in git history on branch ``feature/nina-app``. ``HoverboardAxisSettings``
    # still carries pulse_* fields for env compatibility; they are ignored here.
    # ------------------------------------------------------------------

    def _halt_forward_pulse(self, *, wait: bool = True) -> None:
        """No-op (legacy hook for callers that still invoke halt before set_wheels)."""
        _ = wait
        return

    def is_forward_pulse_enabled(self) -> bool:
        return False

    def is_forward_pulse_active(self) -> bool:
        return False

    def start_pulse_straight_forward(self, speed_percent: int) -> None:
        """Legacy entry point: continuous forward lean (pulse algorithm disabled)."""
        if not self._is_initialized:
            return
        self.forward(speed_percent)

    def stop(self) -> None:
        if not self._is_initialized:
            return
        self._halt_forward_pulse(wait=True)
        self._apply_goals(
            {self._left_id: self._brake_left, self._right_id: self._brake_right}
        )
        time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))
        self._last_straight_key = None

    def emergency_stop(self, *, routine_shutdown: bool = False) -> None:
        _ = routine_shutdown  # Same keyword shape as NavigationManager; no extra GPIO.
        self.stop()

    def _apply_goals(self, goals: Dict[int, int]) -> None:
        if not self._is_initialized:
            return
        with self._bus_lock:
            self._dxl.sync_write_goal_position(goals)

    @staticmethod
    def _symmetric_straight_key(
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> Optional[str]:
        """``\"fwd\"`` / ``\"back\"`` for symmetric straight line, else ``None``."""
        if left_speed <= 0 or right_speed <= 0 or left_dir != right_dir:
            return None
        if left_dir == HoverboardAxisDrive.DIR_FORWARD:
            return "fwd"
        if left_dir == HoverboardAxisDrive.DIR_BACKWARD:
            return "back"
        return None

    def _prime_straight_neutral(self) -> None:
        """Command both lean servos to center, then wait until close or timeout."""
        if not self._is_initialized:
            return
        goal = self._dxl._clamp_pos(_straight_prime_goal_ticks())
        ms = max(0, min(1023, int(self._axis.moving_speed)))
        lid, rid = self._left_id, self._right_id
        deadline = time.monotonic() + _straight_prime_timeout_sec()
        tol = _straight_prime_tol_ticks()
        with self._bus_lock:
            self._dxl._require_initialized()
            self._dxl.sync_write_moving_speed_subset({lid: ms, rid: ms})
            self._dxl.sync_write_goal_position({lid: goal, rid: goal})
        while time.monotonic() < deadline:
            with self._bus_lock:
                pl = self._dxl.read_reg(lid, *REG_PRESENT_POS)
                pr = self._dxl.read_reg(rid, *REG_PRESENT_POS)
            if pl is not None and pr is not None:
                if abs(int(pl) - goal) <= tol and abs(int(pr) - goal) <= tol:
                    break
            time.sleep(0.03)

    def _goals_for_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> Dict[int, int]:
        nl = self._brake_left
        nr = self._brake_right
        sl = self._eff_sign_left()
        sr = self._eff_sign_right()

        if left_speed <= 0 and right_speed <= 0:
            return {self._left_id: nl, self._right_id: nr}

        lf = left_dir == self.DIR_FORWARD
        rf = right_dir == self.DIR_FORWARD
        if (
            lf
            and rf
            and left_speed > 0
            and right_speed > 0
        ):
            fl = self._dxl._clamp_pos(
                _nudge_goal_from_brake(
                    int(self._axis.forward_pos_left),
                    nl,
                    _STRAIGHT_FWD_EXTRA_TICKS,
                )
            )
            fr = self._dxl._clamp_pos(
                _nudge_goal_from_brake(
                    int(self._axis.forward_pos_right),
                    nr,
                    _STRAIGHT_FWD_EXTRA_TICKS,
                )
            )
            return {self._left_id: fl, self._right_id: fr}

        lb = left_dir == self.DIR_BACKWARD
        rb = right_dir == self.DIR_BACKWARD
        if (
            lb
            and rb
            and left_speed > 0
            and right_speed > 0
        ):
            bl = self._dxl._clamp_pos(int(self._axis.backward_pos_left))
            br = self._dxl._clamp_pos(int(self._axis.backward_pos_right))
            return {self._left_id: bl, self._right_id: br}

        # Pivot: opposite leans from configured straight-line goals (left back + right
        # forward = turn left; left forward + right back = turn right).
        if left_speed > 0 and right_speed > 0 and lf != rf:
            fl = self._dxl._clamp_pos(int(self._axis.forward_pos_left))
            fr = self._dxl._clamp_pos(int(self._axis.forward_pos_right))
            bl = self._dxl._clamp_pos(int(self._axis.backward_pos_left))
            br = self._dxl._clamp_pos(int(self._axis.backward_pos_right))
            if lf:
                lg, rg = fl, br
            else:
                lg, rg = bl, fr
            if self._axis.swap_turn_lr:
                lg, rg = rg, lg
            push = int(self._axis.turn_push_ticks)
            if push > 0:
                lg = _nudge_goal_from_brake(lg, nl, push)
                rg = _nudge_goal_from_brake(rg, nr, push)
            return {
                self._left_id: self._dxl._clamp_pos(lg),
                self._right_id: self._dxl._clamp_pos(rg),
            }

        dl = self._speed_to_delta_raw(left_speed) if left_speed > 0 else 0
        dr = self._speed_to_delta_raw(right_speed) if right_speed > 0 else 0

        def fwd_l() -> int:
            return nl + sl * dl

        def back_l() -> int:
            return nl - sl * dl

        def fwd_r() -> int:
            return nr + sr * dr

        def back_r() -> int:
            return nr - sr * dr

        lg = nl
        rg = nr

        if left_speed > 0:
            lg = fwd_l() if lf else back_l()
        if right_speed > 0:
            rg = fwd_r() if rf else back_r()

        return {
            self._left_id: self._dxl._clamp_pos(lg),
            self._right_id: self._dxl._clamp_pos(rg),
        }

    def set_wheels(
        self,
        *,
        left_dir: str,
        left_speed: int,
        right_dir: str,
        right_speed: int,
    ) -> None:
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        self._halt_forward_pulse(wait=True)
        sk = self._symmetric_straight_key(
            left_dir, left_speed, right_dir, right_speed
        )
        if sk is not None:
            if sk != self._last_straight_key:
                self._prime_straight_neutral()
            self._last_straight_key = sk
        else:
            self._last_straight_key = None
        goals = self._goals_for_wheels(
            left_dir=left_dir,
            left_speed=left_speed,
            right_dir=right_dir,
            right_speed=right_speed,
        )
        self._apply_goals(goals)

    def drive_continuous(
        self,
        left_dir: str,
        right_dir: str,
        speed_percent: Optional[int] = None,
        *,
        right_speed_percent: Optional[int] = None,
    ) -> None:
        if left_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid left_dir '{left_dir}'")
        if right_dir not in (self.DIR_FORWARD, self.DIR_BACKWARD):
            raise ValueError(f"Invalid right_dir '{right_dir}'")
        left_speed = self._resolve_speed(speed_percent)
        if right_speed_percent is None:
            right_speed = left_speed
        else:
            right_speed = self._resolve_speed(right_speed_percent)
        sk = self._symmetric_straight_key(
            left_dir, left_speed, right_dir, right_speed
        )
        if sk is None:
            self.stop()
            time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))
        self.set_wheels(
            left_dir=left_dir,
            left_speed=left_speed,
            right_dir=right_dir,
            right_speed=right_speed,
        )

    def turn_left(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        speed = self._resolve_speed(speed_percent)
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 2.3)
        )
        self.set_wheels(
            left_dir=self.DIR_BACKWARD,
            left_speed=speed,
            right_dir=self.DIR_FORWARD,
            right_speed=speed,
        )
        time.sleep(max(0.0, dur))
        self.stop()

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        speed = self._resolve_speed(speed_percent)
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 2.3)
        )
        self.set_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=speed,
            right_dir=self.DIR_BACKWARD,
            right_speed=speed,
        )
        time.sleep(max(0.0, dur))
        self.stop()

    def forward(self, speed_percent: Optional[int] = None) -> None:
        sp = self._resolve_speed(speed_percent)
        self.set_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=sp,
            right_dir=self.DIR_FORWARD,
            right_speed=sp,
        )

    def backward(self, speed_percent: Optional[int] = None) -> None:
        sp = self._resolve_speed(speed_percent)
        self.set_wheels(
            left_dir=self.DIR_BACKWARD,
            left_speed=sp,
            right_dir=self.DIR_BACKWARD,
            right_speed=sp,
        )
