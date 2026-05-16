"""
Drive Nina locomotion by tilting hoverboard driver modules via Dynamixel MX-28.

Primary Nina locomotion: lean servos on the Dynamixel bus (joint mode). API subset
matches ``NavigationManager`` as used by ``DriveController``, autonomy, and goto.

**Straight-line prime:** before each new symmetric straight FWD/BACK ``set_wheels``,
both lean servos move to ``NINA_HOVER_STRAIGHT_PRIME_POS`` (default 2048) for up to
``NINA_HOVER_STRAIGHT_PRIME_SEC`` (default 2 s). **Timed Turn left/right** (Drive
GUI) call the same prime before starting. D-pad pivots skip that explicit prime.

**Pivot / turn:** lean ID ``id_left`` (often 12) and ``id_right`` (often 13) use
opposite forward/back goals. **Turn left** = left forward lean + right backward
lean (right side uses ``NINA_HOVER_TURN_SLOW_WHEEL_PCT`` vs outer ``speed_percent``).
**Turn right** is the mirror.

**Straight pulse (series):** when ``NINA_HOVER_PULSE_FORWARD`` / ``pulse_forward_enabled`` is true,
``start_pulse_straight_forward`` and ``start_pulse_straight_backward`` run **independent** timed
series (separate parameters for forward vs backward hold, coast dwell, coast blend, and return ramps).
Both use ``pulse_series_max`` then full brake.
Cancel with ``stop()`` / ``emergency_stop()`` / ``set_wheels`` / ``drive_continuous``.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Dict, Optional

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS, DynamixelManager

log = logging.getLogger("nina.hoverboard_axis")

_POS_SPAN_DEG = 300.0


def _smoothstep01(t: float) -> float:
    """Hermite smoothstep; zero derivative at 0 and 1 (soft turnarounds)."""
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _smootherstep01(t: float) -> float:
    """Quintic Perlin smootherstep; zero 1st and 2nd derivative at 0 and 1."""
    t = max(0.0, min(1.0, float(t)))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _cubic_ease_in_out01(t: float) -> float:
    """Symmetric cubic ease-in-out; slower ends, faster middle than smoothstep."""
    t = max(0.0, min(1.0, float(t)))
    if t < 0.5:
        return 4.0 * t * t * t
    u = 2.0 * t - 2.0
    return 1.0 + 0.5 * u * u * u


def _trapezoid_blend01(t: float, edge_frac: float) -> float:
    """Blend parameter 0->1 with trapezoidal *velocity* (ease accel / cruise / decel).

    *edge_frac* is the fraction of total time for symmetric accel+decel (each ``edge_frac``).
    """
    t = max(0.0, min(1.0, float(t)))
    e = max(0.08, min(0.35, float(edge_frac)))
    if 2.0 * e >= 0.999:
        return _smootherstep01(t)
    a = e / (2.0 * (1.0 - e))
    if t <= e:
        return a * (t / e) * (t / e)
    if t >= 1.0 - e:
        u = (1.0 - t) / e
        return 1.0 - a * u * u
    return a + (1.0 - 2.0 * a) * (t - e) / (1.0 - 2.0 * e)


def _pulse_ramp_blend_u(t: float, profile: str, trap_edge: float) -> float:
    """Map uniform time *t* in [0,1] to position blend *u* in [0,1] for servo ramps."""
    p = (profile or "smootherstep").strip().lower()
    if p == "smoothstep":
        return _smoothstep01(t)
    if p == "smootherstep":
        return _smootherstep01(t)
    if p in ("cubic_io", "cubic", "cubic-ease"):
        return _cubic_ease_in_out01(t)
    if p in ("trapezoid", "trap", "velocity"):
        return _trapezoid_blend01(t, trap_edge)
    return _smootherstep01(t)


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

# Extra raw ticks past calibrated ``forward_pos_*`` toward drive (symmetric straight FWD only).
_STRAIGHT_FWD_EXTRA_TICKS = 14

# Pivot only (L/R yaw): extra nudge away from each side's brake (after
# ``turn_push_ticks``). Must use directional nudge — a raw +Δ on both goals
# can land on brake and wipe blended timed-turn motion for that axis.
_TURN_PIVOT_GOAL_OFFSET_TICKS = 20


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


def _hover_turn_slow_wheel_pct() -> int:
    """Backward-side lean strength for timed Turn left/right (1–100, default 8)."""
    try:
        return max(1, min(100, int(os.environ.get("NINA_HOVER_TURN_SLOW_WHEEL_PCT", "8"))))
    except ValueError:
        return 8


def estimate_forward_pulse_series_duration_sec(axis: HoverboardAxisSettings) -> float:
    """Upper-bound seconds for ``start_pulse_straight_forward`` until the pulse thread exits.

    Uses straight-line prime cap plus forward-only series timing (``pulse_forward_return_ramp_sec``,
    ``pulse_series_fwd_sec``, ``pulse_series_coast_initial_sec``).
    """
    ramp_sec = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_forward_return_ramp_sec", 0.0))),
    )
    min_trans = max(
        0.0,
        min(2.0, float(getattr(axis, "pulse_series_min_transition_sec", 0.0))),
    )
    eff_ramp = min(5.0, max(ramp_sec, min_trans))
    n = max(1, min(20, int(getattr(axis, "pulse_series_max", 12))))
    series_fwd = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_series_fwd_sec", 0.9))),
    )
    coast_init = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_series_coast_initial_sec", 0.30))),
    )
    prime = _straight_prime_timeout_sec()
    pulse_body = (1.0 + 2.0 * float(n)) * eff_ramp + float(n) * (series_fwd + coast_init)
    return prime + pulse_body


def estimate_backward_pulse_series_duration_sec(axis: HoverboardAxisSettings) -> float:
    """Upper-bound seconds for ``start_pulse_straight_backward`` until the pulse thread exits.

    Uses the same prime cap plus **backward-only** series timing (``pulse_backward_return_ramp_sec``,
    ``pulse_series_back_sec``, ``pulse_series_back_coast_initial_sec``).
    """
    ramp_sec = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_backward_return_ramp_sec", 0.0))),
    )
    min_trans = max(
        0.0,
        min(2.0, float(getattr(axis, "pulse_series_min_transition_sec", 0.0))),
    )
    eff_ramp = min(5.0, max(ramp_sec, min_trans))
    n = max(1, min(20, int(getattr(axis, "pulse_series_max", 12))))
    series_back = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_series_back_sec", 0.9))),
    )
    coast_init = max(
        0.0,
        min(10.0, float(getattr(axis, "pulse_series_back_coast_initial_sec", 0.30))),
    )
    prime = _straight_prime_timeout_sec()
    pulse_body = (1.0 + 2.0 * float(n)) * eff_ramp + float(n) * (series_back + coast_init)
    return prime + pulse_body


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
        self._pulse_series_thread: Optional[threading.Thread] = None
        self._pulse_halt = threading.Event()
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
            "HoverboardAxisDrive init brake L(id%s)=%s R(id%s)=%s tilt=%s deg",
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

    def _halt_pulse_series(self, *, wait: bool = True) -> None:
        """Signal the straight pulse-series worker to exit and optionally join it."""
        self._pulse_halt.set()
        t = self._pulse_series_thread
        if t is not None and wait and threading.current_thread() is not t:
            t.join(timeout=3.0)
            if t.is_alive():
                log.warning("Hoverboard straight pulse-series thread did not exit in time")
        self._pulse_series_thread = None
        self._pulse_halt.clear()

    def is_forward_pulse_enabled(self) -> bool:
        return bool(getattr(self._axis, "pulse_forward_enabled", False))

    def is_straight_pulse_series_active(self) -> bool:
        t = self._pulse_series_thread
        return t is not None and t.is_alive()

    def is_forward_pulse_active(self) -> bool:
        """True while a forward or backward straight pulse series thread is running."""
        return self.is_straight_pulse_series_active()

    def start_pulse_straight_forward(self, speed_percent: int) -> None:
        """Forward pulse series for manual D-pad / Straight bench forward (when enabled).

        Runs ``pulse_series_max`` cycles: each cycle ramps from the prior near-brake pose to
        full forward, holds forward for ``pulse_series_fwd_sec``, ramps back to a **fixed**
        near-brake pose (``pulse_forward_coast_blend`` of the brake→forward span, default **0.2**).
        Dwell at that near-brake for ``pulse_series_coast_initial_sec`` each cycle (constant).
        Transition times use ``max(pulse_forward_return_ramp_sec,
        pulse_series_min_transition_sec)``. After the last cycle, servos command **full brake**.
        Cancelled by ``stop()`` / ``emergency_stop()`` / ``set_wheels`` /
        ``drive_continuous``. If ``pulse_forward_enabled`` is False, falls back to ``forward()``.
        """
        if not self._is_initialized:
            return
        if not self.is_forward_pulse_enabled():
            self.forward(speed_percent)
            return
        sp = max(0, min(100, int(speed_percent)))
        self._halt_pulse_series(wait=True)
        self._prime_straight_neutral()
        self._last_straight_key = "fwd"
        thr = threading.Thread(
            target=self._forward_pulse_loop,
            args=(sp,),
            name="nina_hover_fwd_pulse",
            daemon=True,
        )
        self._pulse_series_thread = thr
        thr.start()

    def start_pulse_straight_backward(self, speed_percent: int) -> None:
        """Backward pulse series for manual D-pad / Straight bench back (when enabled).

        Separate timing from forward: ``pulse_series_back_sec`` hold at full reverse lean,
        ``pulse_series_back_coast_initial_sec`` dwell at the near-brake pose,
        ``pulse_backward_coast_blend`` (brake→reverse span), ramps
        ``max(pulse_backward_return_ramp_sec, pulse_series_min_transition_sec)``.
        If ``pulse_forward_enabled`` is False, falls back to ``backward()``.
        """
        if not self._is_initialized:
            return
        if not self.is_forward_pulse_enabled():
            self.backward(speed_percent)
            return
        sp = max(0, min(100, int(speed_percent)))
        self._halt_pulse_series(wait=True)
        self._prime_straight_neutral()
        self._last_straight_key = "back"
        thr = threading.Thread(
            target=self._backward_pulse_loop,
            args=(sp,),
            name="nina_hover_back_pulse",
            daemon=True,
        )
        self._pulse_series_thread = thr
        thr.start()

    def _forward_pulse_loop(self, speed_pct: int) -> None:
        """Forward-only pulse series: brake→coast blend→…→full brake (see module doc)."""
        halt = self._pulse_halt
        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        ramp_sec = max(
            0.0,
            min(
                10.0,
                float(getattr(self._axis, "pulse_forward_return_ramp_sec", 0.0)),
            ),
        )
        min_trans = max(
            0.0,
            min(
                2.0,
                float(getattr(self._axis, "pulse_series_min_transition_sec", 0.0)),
            ),
        )
        eff_ramp = min(5.0, max(ramp_sec, min_trans))

        series_max = int(getattr(self._axis, "pulse_series_max", 12))
        series_max = max(1, min(20, series_max))
        main_hold = max(
            0.0,
            min(10.0, float(getattr(self._axis, "pulse_series_fwd_sec", 0.9))),
        )
        coast_init = max(
            0.0,
            min(
                10.0,
                float(getattr(self._axis, "pulse_series_coast_initial_sec", 0.30)),
            ),
        )
        coast_blend = max(
            0.0,
            min(
                1.0,
                float(getattr(self._axis, "pulse_forward_coast_blend", 0.2)),
            ),
        )

        goals = self._goals_for_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=speed_pct,
            right_dir=self.DIR_FORWARD,
            right_speed=speed_pct,
        )
        coast_goals = self._pulse_coast_goals(brake_goals, goals, coast_blend)

        log.info(
            "hover forward pulse series: n=%s fwd_hold=%.2fs coast_dwell=%.2fs "
            "transition=%.2fs coast_blend=%.2f | FWD L(id%s)=%s R(id%s)=%s | coast L=%s R=%s",
            series_max,
            main_hold,
            coast_init,
            eff_ramp,
            coast_blend,
            self._left_id,
            goals[self._left_id],
            self._right_id,
            goals[self._right_id],
            coast_goals[self._left_id],
            coast_goals[self._right_id],
        )

        try:
            if not halt.is_set():
                self._pulse_ramp_goals_between(
                    brake_goals, coast_goals, eff_ramp, halt
                )
            prev_coast: Dict[int, int] = dict(coast_goals)
            for _ in range(series_max):
                if halt.is_set():
                    break
                coast_dwell = coast_init
                self._pulse_ramp_goals_between(
                    prev_coast, goals, eff_ramp, halt
                )
                if halt.is_set():
                    break
                if main_hold > 0.0 and halt.wait(timeout=main_hold):
                    break
                self._pulse_ramp_goals_between(
                    goals, coast_goals, eff_ramp, halt
                )
                if halt.is_set():
                    break
                if coast_dwell > 0.0 and halt.wait(timeout=coast_dwell):
                    break
                prev_coast = dict(coast_goals)

            if not halt.is_set():
                self._apply_goals(brake_goals)
        finally:
            self._sync_pulse_moving_speed()

    def _backward_pulse_loop(self, speed_pct: int) -> None:
        """Backward-only pulse series: brake→coast blend→…→full brake (separate knobs from FWD)."""
        halt = self._pulse_halt
        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        ramp_sec = max(
            0.0,
            min(
                10.0,
                float(getattr(self._axis, "pulse_backward_return_ramp_sec", 0.0)),
            ),
        )
        min_trans = max(
            0.0,
            min(
                2.0,
                float(getattr(self._axis, "pulse_series_min_transition_sec", 0.0)),
            ),
        )
        eff_ramp = min(5.0, max(ramp_sec, min_trans))

        series_max = int(getattr(self._axis, "pulse_series_max", 12))
        series_max = max(1, min(20, series_max))
        main_hold = max(
            0.0,
            min(10.0, float(getattr(self._axis, "pulse_series_back_sec", 0.9))),
        )
        coast_init = max(
            0.0,
            min(
                10.0,
                float(getattr(self._axis, "pulse_series_back_coast_initial_sec", 0.30)),
            ),
        )
        coast_blend = max(
            0.0,
            min(
                1.0,
                float(getattr(self._axis, "pulse_backward_coast_blend", 0.2)),
            ),
        )

        goals = self._goals_for_wheels(
            left_dir=self.DIR_BACKWARD,
            left_speed=speed_pct,
            right_dir=self.DIR_BACKWARD,
            right_speed=speed_pct,
        )
        coast_goals = self._pulse_coast_goals(brake_goals, goals, coast_blend)

        log.info(
            "hover backward pulse series: n=%s back_hold=%.2fs coast_dwell=%.2fs "
            "transition=%.2fs coast_blend=%.2f | REV L(id%s)=%s R(id%s)=%s | coast L=%s R=%s",
            series_max,
            main_hold,
            coast_init,
            eff_ramp,
            coast_blend,
            self._left_id,
            goals[self._left_id],
            self._right_id,
            goals[self._right_id],
            coast_goals[self._left_id],
            coast_goals[self._right_id],
        )

        try:
            if not halt.is_set():
                self._pulse_ramp_goals_between(
                    brake_goals, coast_goals, eff_ramp, halt
                )
            prev_coast: Dict[int, int] = dict(coast_goals)
            for _ in range(series_max):
                if halt.is_set():
                    break
                coast_dwell = coast_init
                self._pulse_ramp_goals_between(
                    prev_coast, goals, eff_ramp, halt
                )
                if halt.is_set():
                    break
                if main_hold > 0.0 and halt.wait(timeout=main_hold):
                    break
                self._pulse_ramp_goals_between(
                    goals, coast_goals, eff_ramp, halt
                )
                if halt.is_set():
                    break
                if coast_dwell > 0.0 and halt.wait(timeout=coast_dwell):
                    break
                prev_coast = dict(coast_goals)

            if not halt.is_set():
                self._apply_goals(brake_goals)
        finally:
            self._sync_pulse_moving_speed()

    def _pulse_coast_goals(
        self,
        brake_goals: Dict[int, int],
        drive_goals: Dict[int, int],
        coast_blend: float,
    ) -> Dict[int, int]:
        """Interpolate brake→*drive_goals* by *coast_blend* (0 = brake, 1 = full drive lean)."""
        k = max(0.0, min(1.0, float(coast_blend)))
        lid = self._left_id
        rid = self._right_id
        bl = int(brake_goals[lid])
        br = int(brake_goals[rid])
        fl = int(drive_goals[lid])
        fr = int(drive_goals[rid])
        return {
            lid: self._dxl._clamp_pos(int(round(bl + (fl - bl) * k))),
            rid: self._dxl._clamp_pos(int(round(br + (fr - br) * k))),
        }

    def _sync_pulse_moving_speed(self, moving_speed_override: Optional[int] = None) -> None:
        """Re-apply MX Moving Speed for both lean IDs.

        If *moving_speed_override* is set (0-1023), use it for pulse ramps only;
        otherwise use ``axis.moving_speed``.
        """
        if not self._is_initialized:
            return
        if moving_speed_override is not None:
            ms = max(0, min(1023, int(moving_speed_override)))
        else:
            ms = max(0, min(1023, int(self._axis.moving_speed)))
        with self._bus_lock:
            self._dxl.sync_write_moving_speed_subset(
                {self._left_id: ms, self._right_id: ms}
            )

    def _pulse_step_wait(
        self,
        goals: Dict[int, int],
        *,
        sleep_each: float,
        halt: threading.Event,
    ) -> None:
        """After writing *goals*, either sleep a fixed slice or wait for present~=goal (optional)."""
        sync = bool(
            getattr(self._axis, "pulse_forward_sync_present", False)
        )
        tol = int(getattr(self._axis, "pulse_forward_present_tol_ticks", 4))
        max_w = float(
            getattr(
                self._axis,
                "pulse_forward_present_step_timeout_sec",
                0.25,
            )
        )
        max_w = max(0.02, min(1.0, max_w))
        tol = max(0, min(50, tol))
        lid = self._left_id
        rid = self._right_id
        gl = int(goals[lid])
        gr = int(goals[rid])

        if not sync or tol <= 0:
            if halt.wait(timeout=max(0.0, sleep_each)):
                return
            return

        t0 = time.monotonic()
        deadline = t0 + max(max_w, sleep_each * 0.75)
        poll = 0.008
        while not halt.is_set():
            with self._bus_lock:
                pl = self._dxl.read_reg(lid, *REG_PRESENT_POS)
                pr = self._dxl.read_reg(rid, *REG_PRESENT_POS)
            if pl is not None and pr is not None:
                pv_l = self._dxl._clamp_pos(int(pl))
                pv_r = self._dxl._clamp_pos(int(pr))
                if abs(pv_l - gl) <= tol and abs(pv_r - gr) <= tol:
                    break
            if time.monotonic() >= deadline:
                break
            time.sleep(poll)

    def _pulse_ramp_goals_between(
        self,
        start_goals: Dict[int, int],
        end_goals: Dict[int, int],
        ramp_sec: float,
        halt: threading.Event,
    ) -> None:
        """Interpolate MX goals *start_goals* -> *end_goals* (profiled blend + optional pulse MS)."""
        raw_ms = getattr(self._axis, "pulse_ramp_moving_speed", None)
        ms_override: Optional[int] = None
        if raw_ms is not None:
            ms_override = max(0, min(1023, int(raw_ms)))
        self._sync_pulse_moving_speed(moving_speed_override=ms_override)
        if ramp_sec <= 0.0:
            self._apply_goals(end_goals)
            self._sync_pulse_moving_speed()
            return
        lid = self._left_id
        rid = self._right_id
        sl = int(start_goals[lid])
        sr = int(start_goals[rid])
        el = int(end_goals[lid])
        er = int(end_goals[rid])
        profile = str(
            getattr(self._axis, "pulse_ramp_profile", "smootherstep") or "smootherstep"
        )
        trap_edge = float(
            getattr(self._axis, "pulse_ramp_trap_edge", 0.18)
        )
        trap_edge = max(0.08, min(0.35, trap_edge))
        # Same duration and step index for both motors; slightly denser steps for long ramps.
        n = max(3, min(250, int(round(ramp_sec / 0.017))))
        sleep_each = ramp_sec / float(n)
        prev_l, prev_r = sl, sr
        try:
            for i in range(1, n + 1):
                if halt.is_set():
                    return
                u = _pulse_ramp_blend_u(i / float(n), profile, trap_edge)
                raw_l = int(round(sl + (el - sl) * u))
                raw_r = int(round(sr + (er - sr) * u))
                if el >= sl:
                    gl = max(prev_l, min(raw_l, el))
                else:
                    gl = min(prev_l, max(raw_l, el))
                if er >= sr:
                    gr = max(prev_r, min(raw_r, er))
                else:
                    gr = min(prev_r, max(raw_r, er))
                prev_l, prev_r = gl, gr
                g = {
                    lid: self._dxl._clamp_pos(gl),
                    rid: self._dxl._clamp_pos(gr),
                }
                self._apply_goals(g)
                if i < n:
                    self._pulse_step_wait(g, sleep_each=sleep_each, halt=halt)
                    if halt.is_set():
                        return
        finally:
            # Restore default moving speed after pulse-only override.
            self._sync_pulse_moving_speed()

    def _pulse_moving_speed_override(self) -> Optional[int]:
        raw_ms = getattr(self._axis, "pulse_ramp_moving_speed", None)
        if raw_ms is None:
            return None
        return max(0, min(1023, int(raw_ms)))

    def _pulse_half_cosine_leg(
        self,
        start_goals: Dict[int, int],
        end_goals: Dict[int, int],
        ramp_sec: float,
        halt: threading.Event,
        *,
        rising: bool,
    ) -> None:
        """Half raised-cosine: *start*->*end* over ``ramp_sec`` (``rising`` True = coast->FWD)."""
        ms_override = self._pulse_moving_speed_override()
        self._sync_pulse_moving_speed(moving_speed_override=ms_override)
        if ramp_sec <= 0.0:
            self._apply_goals(end_goals)
            self._sync_pulse_moving_speed()
            return
        lid = self._left_id
        rid = self._right_id
        sl = int(start_goals[lid])
        sr = int(start_goals[rid])
        el = int(end_goals[lid])
        er = int(end_goals[rid])
        n = max(3, min(250, int(round(ramp_sec / 0.017))))
        sleep_each = ramp_sec / float(n)
        prev_l, prev_r = sl, sr
        try:
            for i in range(1, n + 1):
                if halt.is_set():
                    return
                phi = i / float(n)
                if rising:
                    u = 0.5 * (1.0 - math.cos(math.pi * phi))
                else:
                    u = 0.5 * (1.0 + math.cos(math.pi * phi))
                raw_l = int(round(sl + (el - sl) * u))
                raw_r = int(round(sr + (er - sr) * u))
                if el >= sl:
                    gl = max(prev_l, min(raw_l, el))
                else:
                    gl = min(prev_l, max(raw_l, el))
                if er >= sr:
                    gr = max(prev_r, min(raw_r, er))
                else:
                    gr = min(prev_r, max(raw_r, er))
                prev_l, prev_r = gl, gr
                g = {
                    lid: self._dxl._clamp_pos(gl),
                    rid: self._dxl._clamp_pos(gr),
                }
                self._apply_goals(g)
                if i < n:
                    self._pulse_step_wait(g, sleep_each=sleep_each, halt=halt)
                    if halt.is_set():
                        return
        finally:
            self._sync_pulse_moving_speed()

    def _pulse_cosine_coast_forward_coast(
        self,
        coast_goals: Dict[int, int],
        forward_goals: Dict[int, int],
        ramp_sec: float,
        halt: threading.Event,
    ) -> None:
        """One full pulse: coast->forward->coast over ``2*ramp_sec`` with matched in/out velocity."""
        ms_override = self._pulse_moving_speed_override()
        self._sync_pulse_moving_speed(moving_speed_override=ms_override)
        if ramp_sec <= 0.0:
            self._apply_goals(forward_goals)
            self._apply_goals(coast_goals)
            self._sync_pulse_moving_speed()
            return
        T = 2.0 * ramp_sec
        lid = self._left_id
        rid = self._right_id
        cl = int(coast_goals[lid])
        cr = int(coast_goals[rid])
        fl = int(forward_goals[lid])
        fr = int(forward_goals[rid])
        n = max(3, min(300, int(round(T / 0.017))))
        sleep_each = T / float(n)
        prev_l, prev_r = cl, cr
        try:
            for i in range(1, n + 1):
                if halt.is_set():
                    return
                phi = i / float(n)
                u = 0.5 * (1.0 - math.cos(2.0 * math.pi * phi))
                raw_l = int(round(cl + (fl - cl) * u))
                raw_r = int(round(cr + (fr - cr) * u))
                if phi <= 0.5 + 1e-15:
                    if fl >= cl:
                        gl = max(prev_l, min(raw_l, fl))
                    else:
                        gl = min(prev_l, max(raw_l, fl))
                    if fr >= cr:
                        gr = max(prev_r, min(raw_r, fr))
                    else:
                        gr = min(prev_r, max(raw_r, fr))
                else:
                    if fl >= cl:
                        gl = min(prev_l, max(raw_l, cl))
                    else:
                        gl = max(prev_l, min(raw_l, cl))
                    if fr >= cr:
                        gr = min(prev_r, max(raw_r, cr))
                    else:
                        gr = max(prev_r, min(raw_r, cr))
                prev_l, prev_r = gl, gr
                g = {
                    lid: self._dxl._clamp_pos(gl),
                    rid: self._dxl._clamp_pos(gr),
                }
                self._apply_goals(g)
                if i < n:
                    self._pulse_step_wait(g, sleep_each=sleep_each, halt=halt)
                    if halt.is_set():
                        return
        finally:
            self._sync_pulse_moving_speed()

    def stop(self) -> None:
        if not self._is_initialized:
            return
        self._halt_pulse_series(wait=True)
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

        # Pivot: opposite leans. **Equal speeds** ⇒ full calibrated pivot (D-pad).
        # **Unequal speeds** ⇒ blend each axis toward its goal by speed/100 (timed
        # Turn left/right: strong forward lean vs weaker backward lean on the other axis).
        if left_speed > 0 and right_speed > 0 and lf != rf:
            fl = self._dxl._clamp_pos(int(self._axis.forward_pos_left))
            fr = self._dxl._clamp_pos(int(self._axis.forward_pos_right))
            bl = self._dxl._clamp_pos(int(self._axis.backward_pos_left))
            br = self._dxl._clamp_pos(int(self._axis.backward_pos_right))
            if lf:
                l_tgt, r_tgt = fl, br
            else:
                l_tgt, r_tgt = bl, fr
            if self._axis.swap_turn_lr:
                l_tgt, r_tgt = r_tgt, l_tgt
            push = int(self._axis.turn_push_ticks)
            if push > 0:
                l_tgt = _nudge_goal_from_brake(l_tgt, nl, push)
                r_tgt = _nudge_goal_from_brake(r_tgt, nr, push)
            extra = int(_TURN_PIVOT_GOAL_OFFSET_TICKS)
            if extra > 0:
                l_tgt = self._dxl._clamp_pos(
                    _nudge_goal_from_brake(l_tgt, nl, extra)
                )
                r_tgt = self._dxl._clamp_pos(
                    _nudge_goal_from_brake(r_tgt, nr, extra)
                )
            if left_speed == right_speed:
                lg, rg = l_tgt, r_tgt
            else:
                u_l = max(0.0, min(1.0, left_speed / 100.0))
                u_r = max(0.0, min(1.0, right_speed / 100.0))
                lg = int(round(nl + (l_tgt - nl) * u_l))
                rg = int(round(nr + (r_tgt - nr) * u_r))
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
        self._halt_pulse_series(wait=True)
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
        """Timed yaw: left lean (``id_left``) forward, right lean backward (weaker).

        Runs straight-line :meth:`_prime_straight_neutral` first (same family as
        symmetric straight). Outer lean uses *speed_percent*; inner uses
        ``NINA_HOVER_TURN_SLOW_WHEEL_PCT`` (default 8).
        """
        outer = self._resolve_speed(speed_percent)
        slow = _hover_turn_slow_wheel_pct()
        outer = max(outer, slow)
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 5.0)
        )
        self._prime_straight_neutral()
        time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))
        self.set_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=outer,
            right_dir=self.DIR_BACKWARD,
            right_speed=slow,
        )
        time.sleep(max(0.0, dur))
        self.stop()

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Timed yaw: right lean forward, left lean backward (weaker). See ``turn_left``."""
        outer = self._resolve_speed(speed_percent)
        slow = _hover_turn_slow_wheel_pct()
        outer = max(outer, slow)
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 5.0)
        )
        self._prime_straight_neutral()
        time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))
        self.set_wheels(
            left_dir=self.DIR_BACKWARD,
            left_speed=slow,
            right_dir=self.DIR_FORWARD,
            right_speed=outer,
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
