"""
Drive Nina locomotion by tilting hoverboard gyro modules via Dynamixel AX-18.

Primary Nina locomotion: lean servos on the Dynamixel bus. API subset matches
``NavigationManager`` as used by ``DriveController`` / autonomy / goto.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import DynamixelManager
from nina.controllers.hoverboard_power_relay import (
    HoverboardPowerRelay,
    build_power_relay,
)

log = logging.getLogger("nina.hoverboard_axis")

_POS_SPAN_DEG = 300.0


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

    DRIVER_LABEL = "Hoverboard lean — Dynamixel AX-18 (ID 12+13)"
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
        self._power_relay: Optional[HoverboardPowerRelay] = build_power_relay(
            axis_cfg.power_relay_bcm,
            power_on_level=int(axis_cfg.power_relay_power_on_level),
            status_led_bcm=axis_cfg.power_relay_status_led_bcm,
        )

    # ------------------------------------------------------------------
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
        # Parked / brake pose must match de-energised pack when a relay is wired
        # (kiosk already primed cut at NinaService boot; repeat here after DXL goals).
        if self._power_relay is not None:
            self._power_relay.set_power_cut()
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

    def _straight_tilt_delta_raw(self, base_deg: float, speed_pct: int) -> int:
        """Raw Dynamixel delta for ``base_deg`` at full stick, scaled by speed %."""
        sp = max(0, min(100, int(speed_pct)))
        scale = sp / 100.0
        deg = float(base_deg) * scale
        raw = int(round(abs(deg) * _POS_SCALE))
        return max(2, raw)

    def prime_turn_left_straight(self, speed: int) -> None:
        return

    def engage_brake(self) -> None:
        """Hold brake pose; optional GPIO cuts hoverboard pack power (see settings)."""
        self.stop()
        if self._power_relay is not None:
            self._power_relay.set_power_cut()

    def release_brake(self, *, energize_pack: bool = True) -> None:
        """Re-energise hoverboard pack when a power relay is configured."""
        if not energize_pack:
            return
        if self._power_relay is not None:
            self._power_relay.set_power_allowed()

    def stop(self) -> None:
        if not self._is_initialized:
            return
        self._apply_goals(
            {self._left_id: self._brake_left, self._right_id: self._brake_right}
        )
        time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))

    def emergency_stop(self, *, routine_shutdown: bool = False) -> None:
        self.stop()
        if self._power_relay is None:
            return
        if routine_shutdown:
            self._power_relay.apply_shutdown_policy(
                allow_power=bool(self._axis.power_relay_shutdown_allows_power),
            )
        else:
            self._power_relay.set_power_cut()

    def _apply_goals(self, goals: Dict[int, int]) -> None:
        if not self._is_initialized:
            return
        with self._bus_lock:
            self._dxl.sync_write_goal_position(goals)

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
            tdeg = float(getattr(self._axis, "straight_tilt_deg", 0.0) or 0.0)
            if tdeg > 0.0:
                sp = min(int(left_speed), int(right_speed))
                raw = self._straight_tilt_delta_raw(tdeg, sp)
                lg = self._dxl._clamp_pos(nl + sl * raw)
                rg = self._dxl._clamp_pos(nr + sr * raw)
                return {self._left_id: lg, self._right_id: rg}
            fl = self._dxl._clamp_pos(int(self._axis.forward_pos_left))
            fr = self._dxl._clamp_pos(int(self._axis.forward_pos_right))
            return {self._left_id: fl, self._right_id: fr}

        lb = left_dir == self.DIR_BACKWARD
        rb = right_dir == self.DIR_BACKWARD
        if (
            lb
            and rb
            and left_speed > 0
            and right_speed > 0
        ):
            tdeg = float(getattr(self._axis, "straight_tilt_deg", 0.0) or 0.0)
            if tdeg > 0.0:
                sp = min(int(left_speed), int(right_speed))
                raw = self._straight_tilt_delta_raw(tdeg, sp)
                lg = self._dxl._clamp_pos(nl - sl * raw)
                rg = self._dxl._clamp_pos(nr - sr * raw)
                return {self._left_id: lg, self._right_id: rg}
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
