"""
Drive Nina locomotion by tilting hoverboard driver modules via Dynamixel MX-28.

Primary Nina locomotion: lean servos on the Dynamixel bus (joint mode). API subset
matches ``NavigationManager`` as used by ``DriveController``, autonomy, and goto.

**Straight-line prime:** before each new symmetric straight FWD/BACK ``set_wheels``,
both lean servos move to ``NINA_HOVER_STRAIGHT_PRIME_POS`` (default 2048) for up to
``NINA_HOVER_STRAIGHT_PRIME_SEC`` (default 2 s). **Timed Turn left/right** do **not**
use this prime—they go straight to pivot ``set_wheels`` from the current pose.
D-pad pivots also skip the explicit prime.

After a pivot the brake pose is **already** at the prime ticks, so the prime
returns immediately and can look like it was skipped to the operator. Set
``NINA_HOVER_POST_TURN_SETTLE_SEC`` (default 0.0) to add a visible pause inside
``start_pulse_straight_forward`` / ``start_pulse_straight_backward`` after the
prime so the lean stack truly relaxes before the next pulse series fires—useful
when "Straight back" right after "Turn left/right" felt like it jumped straight
into the back stroke without slowing down.

**Pivot / turn:** lean ID ``id_left`` (often 12) and ``id_right`` (often 13) use
opposite forward/back goals. **Turn left** = left forward lean + right backward
lean. **Timed** ``turn_left`` / ``turn_right`` use a partial pivot blend (default
~15° of nominal 90° via ``NINA_DRIVE_TURN_PIVOT_DEG`` / ``NINA_HOVER_TURN_PIVOT_BLEND_PCT``).
**Held** D-pad pivots use ``NINA_HOVER_TURN_SLOW_WHEEL_PCT`` vs outer
``speed_percent`` when the UI applies asymmetric duties. **Turn right** is the mirror.

**Straight pulse (series):** when ``NINA_HOVER_PULSE_FORWARD`` / ``pulse_forward_enabled`` is true,
``start_pulse_straight_forward`` and ``start_pulse_straight_backward`` run **independent** timed
series (separate parameters for forward vs backward hold, coast dwell, coast blend, and return ramps).
Both use ``pulse_series_max`` then full brake.
Cancel with ``stop()`` / ``emergency_stop()`` / ``set_wheels`` / ``drive_continuous``.

**IMU yaw correction (discrete pause-pivot-resume):** call :meth:`set_imu_hooks`
once at construction with a yaw-drift sampler and optional begin/end straight
callbacks (the ``NinaService`` wires :class:`Mpu9250DriftMonitor` here). During
the main and coast holds of both pulse series the controller polls the yaw
integrator at ~20 Hz. When ``|drift| >= NINA_HOVER_IMU_CORR_THRESHOLD_DEG`` the
controller **stops the wheels**, runs a brief **in-place pivot** in the opposite
direction (closed-loop: keeps pivoting until ``|drift| <= NINA_HOVER_IMU_CORR_DEADBAND_DEG``
or ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC`` elapses), brakes, **re-primes** the lean
stack at the neutral pose, and only then **resumes the primed forward / back
pulse**. The asymmetric-bias-while-moving approach is intentionally not used —
biasing mid-pulse felt like uncommanded fast turns to the operator.

Tuning env vars (defaults err on the calm / subtle side; bump them up only
if the bot can't keep heading):

  ``NINA_HOVER_IMU_CORR_ENABLE``           default 1
  ``NINA_HOVER_IMU_CORR_THRESHOLD_DEG``    default 6.0   (only fire when drift gets noticeable)
  ``NINA_HOVER_IMU_CORR_DEADBAND_DEG``     default 1.5   (stop pivot when drift returns inside this)
  ``NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT``  default 8     (much gentler than the timed Turn buttons)
  ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC``    default 0.12  (hard cap on each pivot)
  ``NINA_HOVER_IMU_CORR_SETTLE_SEC``       default 0.25  (brake settle before AND after pivot)
  ``NINA_HOVER_IMU_CORR_COOLDOWN_SEC``     default 1.0   (rest period after each correction before
                                                          the next IMU sample; persists across
                                                          pulse cycles so corrections don't fire
                                                          back-to-back)
  ``NINA_HOVER_IMU_CORR_POLL_HZ``          default 5     (drift sample rate inside pulse holds)
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
import traceback
from typing import Callable, Dict, Optional

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS, DynamixelManager

log = logging.getLogger("nina.hoverboard_axis")

# Yaw drift sampler — returns signed degrees ``+`` = bot drifted right, ``-`` = left,
# or ``None`` when the integrator is paused / calibrating / unavailable.
ImuYawDriftFn = Callable[[], Optional[float]]
# Optional begin/end hooks called when the drive enters / leaves a straight leg.
ImuStraightHook = Callable[[], None]

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
_TURN_PIVOT_GOAL_OFFSET_TICKS = 100


def hover_computed_turn_pivot_goals(
    axis: HoverboardAxisSettings,
    *,
    turn_left: bool,
) -> tuple[int, int]:
    """Full pivot (id_left, id_right) raw goals from FWD/REV + push ticks + pivot offset.

    Used by motion calibration UI defaults and :meth:`HoverboardAxisDrive._goals_for_wheels`
    when optional per-turn overrides are unset.
    """
    def _cg(t: int) -> int:
        return max(0, min(4095, int(t)))

    nl = _cg(int(axis.brake_pos_left))
    nr = _cg(int(axis.brake_pos_right))
    fl = _cg(int(axis.forward_pos_left))
    fr = _cg(int(axis.forward_pos_right))
    bl = _cg(int(axis.backward_pos_left))
    br = _cg(int(axis.backward_pos_right))
    if turn_left:
        l_tgt, r_tgt = fl, br
    else:
        l_tgt, r_tgt = bl, fr
    if axis.swap_turn_lr:
        l_tgt, r_tgt = r_tgt, l_tgt
    push = int(axis.turn_push_ticks)
    if push > 0:
        l_tgt = _nudge_goal_from_brake(l_tgt, nl, push)
        r_tgt = _nudge_goal_from_brake(r_tgt, nr, push)
    extra = int(_TURN_PIVOT_GOAL_OFFSET_TICKS)
    if extra > 0:
        l_tgt = _cg(_nudge_goal_from_brake(l_tgt, nl, extra))
        r_tgt = _cg(_nudge_goal_from_brake(r_tgt, nr, extra))
    return l_tgt, r_tgt


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


def _post_turn_settle_sec() -> float:
    """Extra dwell after the straight prime inside ``start_pulse_straight_*``.

    When ``stop()`` from a pivot leaves the lean stack at the brake pose AND the
    prime goal equals brake, ``_prime_straight_neutral`` returns immediately on
    the first present-position read—so the operator can't see priming happen
    before the next pulse series fires. Setting this >0 inserts a visible pause
    (logged) after the prime, before the back / forward pulse begins.
    """
    raw = (os.environ.get("NINA_HOVER_POST_TURN_SETTLE_SEC") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, min(2.0, float(raw)))
    except ValueError:
        return 0.0


def _imu_corr_enabled() -> bool:
    raw = (os.environ.get("NINA_HOVER_IMU_CORR_ENABLE") or "").strip().lower()
    if raw == "":
        return True  # default ON when hooks are wired
    return raw in ("1", "true", "yes", "y", "on")


def _imu_corr_threshold_deg() -> float:
    """At/above this |drift|, pause and pivot-correct. Higher = less twitchy."""
    try:
        return max(
            0.1,
            min(45.0, float(os.environ.get("NINA_HOVER_IMU_CORR_THRESHOLD_DEG", "6.0"))),
        )
    except ValueError:
        return 6.0


def _imu_corr_deadband_deg() -> float:
    """Pivot stops once |drift| drops back inside this band."""
    try:
        return max(
            0.0,
            min(10.0, float(os.environ.get("NINA_HOVER_IMU_CORR_DEADBAND_DEG", "1.5"))),
        )
    except ValueError:
        return 1.5


def _imu_corr_pivot_blend_pct() -> int:
    """Pivot lean blend %. Lower = gentler / slower yaw correction."""
    try:
        return max(
            1,
            min(100, int(float(os.environ.get("NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT", "8")))),
        )
    except ValueError:
        return 8


def _imu_corr_pivot_max_sec() -> float:
    """Cap on each pivot's duration. Shorter = smaller correction per event."""
    try:
        return max(
            0.02,
            min(3.0, float(os.environ.get("NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC", "0.12"))),
        )
    except ValueError:
        return 0.12


def _imu_corr_settle_sec() -> float:
    """Brake settle dwell applied both before and after each correction pivot."""
    try:
        return max(
            0.0,
            min(2.0, float(os.environ.get("NINA_HOVER_IMU_CORR_SETTLE_SEC", "0.25"))),
        )
    except ValueError:
        return 0.25


def _imu_corr_cooldown_sec() -> float:
    """Mandatory primed-forward rest after each correction before sampling again.

    Persists across pulse cycles via ``self._imu_corr_next_sample_at`` so two
    corrections never fire back-to-back. The bot must spend at least this long
    actually moving forward (or backward) primed before the next pivot can be
    triggered.
    """
    try:
        return max(
            0.0,
            min(10.0, float(os.environ.get("NINA_HOVER_IMU_CORR_COOLDOWN_SEC", "1.0"))),
        )
    except ValueError:
        return 1.0


def _imu_corr_poll_hz() -> float:
    """How often the pulse hold samples yaw drift. Lower = calmer / fewer pivots."""
    try:
        return max(
            1.0,
            min(60.0, float(os.environ.get("NINA_HOVER_IMU_CORR_POLL_HZ", "5.0"))),
        )
    except ValueError:
        return 5.0


def _hover_turn_slow_wheel_pct() -> int:
    """Backward-side lean for held D-pad pivots (1–100, default 8)."""
    try:
        return max(1, min(100, int(os.environ.get("NINA_HOVER_TURN_SLOW_WHEEL_PCT", "8"))))
    except ValueError:
        return 8


def _timed_turn_pivot_blend_pct() -> int:
    """How far timed Turn left/right lean toward full pivot (1–100).

    Default ~15° of nominal 90°: ``NINA_DRIVE_TURN_PIVOT_DEG=15`` → 17%% blend.
    Override directly with ``NINA_HOVER_TURN_PIVOT_BLEND_PCT``.
    """
    deg_raw = (os.environ.get("NINA_DRIVE_TURN_PIVOT_DEG") or "15").strip()
    if deg_raw:
        try:
            deg = max(1.0, min(90.0, float(deg_raw)))
            return max(1, min(100, int(round(deg / 90.0 * 100.0))))
        except ValueError:
            pass
    try:
        return max(
            1,
            min(100, int(os.environ.get("NINA_HOVER_TURN_PIVOT_BLEND_PCT", "17"))),
        )
    except ValueError:
        return 17


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

        # IMU yaw-correction hooks (wired from NinaService when the MPU-9250
        # monitor is enabled). All three may be None on dev hosts without IMU.
        self._imu_yaw_drift_fn: Optional[ImuYawDriftFn] = None
        self._imu_begin_fn: Optional[ImuStraightHook] = None
        self._imu_end_fn: Optional[ImuStraightHook] = None
        # Cached tunables: re-loaded by ``set_imu_hooks`` so unit tests / runtime
        # env changes are picked up without rebuilding the drive. All control
        # the discrete pause-pivot-resume correction inside the pulse holds (no
        # continuous bias mid-pulse).
        self._imu_corr_enabled_static: bool = _imu_corr_enabled()
        self._imu_corr_threshold_deg: float = _imu_corr_threshold_deg()
        self._imu_corr_deadband_deg: float = _imu_corr_deadband_deg()
        self._imu_corr_pivot_blend_pct: int = _imu_corr_pivot_blend_pct()
        self._imu_corr_pivot_max_sec: float = _imu_corr_pivot_max_sec()
        self._imu_corr_settle_sec: float = _imu_corr_settle_sec()
        self._imu_corr_cooldown_sec: float = _imu_corr_cooldown_sec()
        self._imu_corr_poll_sec: float = 1.0 / _imu_corr_poll_hz()
        # Earliest monotonic time at which the next IMU sample is allowed.
        # Set by every pivot to ``now + cooldown_sec`` so back-to-back
        # corrections can't fire. Reset to 0 by ``_imu_begin_straight`` so a
        # fresh straight leg can sample immediately.
        self._imu_corr_next_sample_at: float = 0.0

    # ------------------------------------------------------------------
    def update_axis_config(self, axis_cfg: HoverboardAxisSettings) -> None:
        """Refresh FWD/REV / pivot goals after motion calibration save."""
        self._axis = axis_cfg

    def update_navigation_settings(self, nav_cfg) -> None:
        """Refresh navigation knobs (e.g. ``turn_duration_sec``) after calibration save."""
        self.config = nav_cfg
        self._invert_left = bool(getattr(nav_cfg, "invert_left_dir", False))
        self._invert_right = bool(getattr(nav_cfg, "invert_right_dir", False))

    # ------------------------------------------------------------------
    # IMU yaw-correction hooks
    # ------------------------------------------------------------------
    def set_imu_hooks(
        self,
        *,
        yaw_drift_fn: Optional[ImuYawDriftFn],
        begin_straight_fn: Optional[ImuStraightHook] = None,
        end_straight_fn: Optional[ImuStraightHook] = None,
    ) -> None:
        """Wire the MPU-9250 (or any) yaw drift sampler into the straight pulses.

        *yaw_drift_fn* should return signed degrees (positive = bot drifted to the
        right) or ``None`` while the integrator is paused / calibrating. The
        optional begin / end hooks are invoked when the drive enters / leaves a
        straight leg (forward or backward pulse series, plus symmetric straight
        ``set_wheels``) so the integrator can be reset and stopped without UI
        plumbing.

        Correction is **discrete**: the pulse loop polls the sampler at
        ``NINA_HOVER_IMU_CORR_POLL_HZ`` and, when drift crosses the threshold,
        brakes, pivots in place until drift returns inside the deadband (or the
        per-pivot cap elapses), brakes again, re-primes the lean stack, and only
        then lets the primed forward / back pulse resume.
        """
        self._imu_yaw_drift_fn = yaw_drift_fn
        self._imu_begin_fn = begin_straight_fn
        self._imu_end_fn = end_straight_fn
        # Refresh static-ish knobs in case env changed since construction.
        self._imu_corr_enabled_static = _imu_corr_enabled()
        self._imu_corr_threshold_deg = _imu_corr_threshold_deg()
        self._imu_corr_deadband_deg = _imu_corr_deadband_deg()
        self._imu_corr_pivot_blend_pct = _imu_corr_pivot_blend_pct()
        self._imu_corr_pivot_max_sec = _imu_corr_pivot_max_sec()
        self._imu_corr_settle_sec = _imu_corr_settle_sec()
        self._imu_corr_cooldown_sec = _imu_corr_cooldown_sec()
        self._imu_corr_poll_sec = 1.0 / _imu_corr_poll_hz()
        # Allow the first sample of the next straight leg to fire immediately.
        self._imu_corr_next_sample_at = 0.0
        log.info(
            "hoverboard IMU hooks: drift=%s begin=%s end=%s enabled=%s "
            "threshold=%.2f deg deadband=%.2f deg pivot=%s%% max=%.2fs "
            "settle=%.2fs cooldown=%.2fs poll=%.2fHz (discrete pause-pivot-resume)",
            "set" if yaw_drift_fn else "off",
            "set" if begin_straight_fn else "off",
            "set" if end_straight_fn else "off",
            self._imu_corr_enabled_static,
            self._imu_corr_threshold_deg,
            self._imu_corr_deadband_deg,
            self._imu_corr_pivot_blend_pct,
            self._imu_corr_pivot_max_sec,
            self._imu_corr_settle_sec,
            self._imu_corr_cooldown_sec,
            _imu_corr_poll_hz(),
        )

    def _imu_sample_drift_deg(self) -> Optional[float]:
        """One safe IMU sample. Returns ``None`` when no sampler / sampler raised / idle."""
        fn = self._imu_yaw_drift_fn
        if fn is None or not self._imu_corr_enabled_static:
            return None
        try:
            return fn()
        except Exception:  # pragma: no cover - sampler bugs must not crash drive
            return None

    def _imu_corrective_hold(
        self,
        base_goals: Dict[int, int],
        duration_sec: float,
        halt: threading.Event,
        *,
        is_forward: bool,
    ) -> bool:
        """Hold *base_goals* for *duration_sec*; pivot-correct when drift crosses threshold.

        While in the hold the lean stack is parked at *base_goals* (the primed
        forward / back lean already applied by the pulse loop). The IMU yaw
        drift is polled at ``poll_hz``; as soon as ``|drift| >= threshold`` the
        method pauses, runs a closed-loop pivot until ``|drift| <= deadband`` (or
        the per-pivot cap), brakes, re-primes, and resumes the primed motion
        for the remainder of *duration_sec*. Returns ``True`` if *halt* fires
        during the hold so the pulse loop can break out cleanly.

        After each correction the next IMU sample is suppressed for
        ``cooldown_sec`` seconds (instance-level, so it persists across pulse
        cycles). That guarantees the bot spends at least that long actually
        moving forward/back primed before another correction can fire — without
        it the corrections stack up and feel like uncommanded turns.

        Falls back to a plain ``halt.wait`` when no IMU sampler is wired, so
        non-IMU bots behave exactly like before.
        """
        _ = is_forward  # pivot direction is the SIGN of drift; motion direction is irrelevant
        if duration_sec <= 0.0:
            return False
        if self._imu_yaw_drift_fn is None or not self._imu_corr_enabled_static:
            return halt.wait(timeout=duration_sec)
        step = max(0.01, float(self._imu_corr_poll_sec))
        end = time.monotonic() + duration_sec
        threshold = self._imu_corr_threshold_deg
        while True:
            if halt.is_set():
                return True
            now = time.monotonic()
            if now >= end:
                return False
            # Hold the primed lean (cheap re-apply so the MX servos stay locked).
            try:
                self._apply_goals(base_goals)
            except Exception:
                pass
            # Only sample IMU once the cooldown from the previous correction
            # has elapsed. During the cooldown we just keep base_goals applied
            # so the bot is doing primed forward/back motion only.
            if now >= self._imu_corr_next_sample_at:
                yaw = self._imu_sample_drift_deg()
                if yaw is not None and abs(yaw) >= threshold:
                    # Drift exceeded — pause the primed motion, pivot-correct,
                    # re-prime, then enforce the cooldown before next sample.
                    if self._perform_pivot_correction(yaw, halt):
                        return True
                    try:
                        self._apply_goals(base_goals)
                    except Exception:
                        pass
                    self._imu_corr_next_sample_at = (
                        time.monotonic() + self._imu_corr_cooldown_sec
                    )
                    log.info(
                        "hover IMU correction: cooldown %.2fs (primed-only motion)",
                        self._imu_corr_cooldown_sec,
                    )
            remaining = end - time.monotonic()
            if remaining <= 0.0:
                return False
            if halt.wait(timeout=min(step, remaining)):
                return True

    def _perform_pivot_correction(
        self,
        drift_deg: float,
        halt: threading.Event,
    ) -> bool:
        """Brake → pivot until drift returns to deadband → brake → re-prime.

        Yaw tracking is intentionally **not** paused around the pivot so the
        integrator sees the pivot's own rotation undoing the drift; we exit as
        soon as ``|drift| <= deadband`` (or after ``pivot_max_sec`` as a safety
        cap). Returns ``True`` if the pulse halt event fires during the routine.

        Sign convention: positive *drift_deg* = bot drifted right → pivot left
        (``set_wheels(F, B)`` lean geometry). Negative drift → pivot right.
        """
        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        pivot_left = drift_deg > 0.0
        log.info(
            "hover IMU correction: drift=%+.2f deg >= %.2f deg threshold "
            "-> pause + pivot %s (blend=%s%%, max=%.2fs, deadband=%.2f deg)",
            drift_deg,
            self._imu_corr_threshold_deg,
            "left" if pivot_left else "right",
            self._imu_corr_pivot_blend_pct,
            self._imu_corr_pivot_max_sec,
            self._imu_corr_deadband_deg,
        )

        # 1. Brake to stop the primed forward/back motion.
        try:
            self._apply_goals(brake_goals)
        except Exception:
            pass
        if halt.wait(timeout=self._imu_corr_settle_sec):
            return True

        # 2. Apply pivot lean. Reuse the same geometry pipeline that the timed
        #    Turn left/right buttons use so any swap_turn_lr / calibrated pivot
        #    goals are honoured here too.
        blend = max(1, min(100, int(self._imu_corr_pivot_blend_pct)))
        if pivot_left:
            pivot_goals = self._goals_for_wheels(
                left_dir=self.DIR_FORWARD,
                left_speed=blend,
                right_dir=self.DIR_BACKWARD,
                right_speed=blend,
            )
        else:
            pivot_goals = self._goals_for_wheels(
                left_dir=self.DIR_BACKWARD,
                left_speed=blend,
                right_dir=self.DIR_FORWARD,
                right_speed=blend,
            )
        try:
            self._apply_goals(pivot_goals)
        except Exception:
            pass

        # 3. Closed-loop: poll IMU and exit as soon as drift is inside the
        #    deadband, or after the per-pivot cap. Keeps re-applying pivot_goals
        #    so the MX servos don't time out at large turn excursions.
        pivot_deadline = time.monotonic() + self._imu_corr_pivot_max_sec
        deadband = self._imu_corr_deadband_deg
        poll = max(0.01, float(self._imu_corr_poll_sec))
        last_yaw = drift_deg
        while True:
            if halt.is_set():
                return True
            now = time.monotonic()
            if now >= pivot_deadline:
                break
            yaw_now = self._imu_sample_drift_deg()
            if yaw_now is not None:
                last_yaw = yaw_now
                if abs(yaw_now) <= deadband:
                    break
                # If drift sign flipped strongly, the bot over-corrected — bail
                # immediately so we don't pivot back-and-forth chasing zero.
                if drift_deg * yaw_now < 0 and abs(yaw_now) >= deadband:
                    break
            try:
                self._apply_goals(pivot_goals)
            except Exception:
                pass
            remaining = pivot_deadline - now
            if halt.wait(timeout=min(poll, remaining)):
                return True

        log.info(
            "hover IMU correction: pivot complete (drift now %+.2f deg, "
            "exited via %s)",
            last_yaw,
            "deadband"
            if abs(last_yaw) <= deadband
            else "max-duration cap",
        )

        # 4. Brake to stop the pivot before we re-prime the straight lean.
        try:
            self._apply_goals(brake_goals)
        except Exception:
            pass
        if halt.wait(timeout=self._imu_corr_settle_sec):
            return True

        # 5. Re-prime the lean stack at neutral so the next primed forward /
        #    back resume starts from the same baseline as a fresh start.
        try:
            self._prime_straight_neutral()
        except Exception:
            pass
        return False

    def _imu_begin_straight(self) -> None:
        # Reset the correction cooldown so a fresh straight leg can sample IMU
        # immediately on its first hold tick (otherwise the user would wait up
        # to cooldown_sec after each new bench-button press before the first
        # correction could fire).
        self._imu_corr_next_sample_at = 0.0
        fn = self._imu_begin_fn
        if fn is None:
            return
        try:
            fn()
        except Exception:  # pragma: no cover - integrator bugs must not crash drive
            log.debug("imu begin_straight hook raised", exc_info=True)

    def _imu_end_straight(self) -> None:
        fn = self._imu_end_fn
        if fn is None:
            return
        try:
            fn()
        except Exception:  # pragma: no cover
            log.debug("imu end_straight hook raised", exc_info=True)

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
        t = self._pulse_series_thread
        active = t is not None and t.is_alive()
        if active:
            try:
                stack = "".join(traceback.format_stack(limit=8)[:-1])
                log.info(
                    "hover _halt_pulse_series: caller stack:\n%s",
                    stack.rstrip(),
                )
            except Exception:
                log.info("hover _halt_pulse_series: (stack capture failed)")
        self._pulse_halt.set()
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
        log.info(
            "hover forward pulse: priming (goal=%s, lean before back/forward) ...",
            _straight_prime_goal_ticks(),
        )
        self._prime_straight_neutral()
        self._post_prime_settle("fwd")
        self._imu_begin_straight()
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
        log.info(
            "hover backward pulse: priming (goal=%s, lean before back/forward) ...",
            _straight_prime_goal_ticks(),
        )
        self._prime_straight_neutral()
        self._post_prime_settle("back")
        self._imu_begin_straight()
        self._last_straight_key = "back"
        thr = threading.Thread(
            target=self._backward_pulse_loop,
            args=(sp,),
            name="nina_hover_back_pulse",
            daemon=True,
        )
        self._pulse_series_thread = thr
        thr.start()

    def _post_prime_settle(self, leg: str) -> None:
        """Optional visible dwell after the straight prime (see ``_post_turn_settle_sec``)."""
        dwell = _post_turn_settle_sec()
        if dwell <= 0.0:
            return
        log.info(
            "hover %s pulse: post-prime settle %.2fs (NINA_HOVER_POST_TURN_SETTLE_SEC) ...",
            leg,
            dwell,
        )
        # Apply brake goals again so the lean stack is held at neutral while we wait.
        try:
            self._apply_goals(
                {self._left_id: self._brake_left, self._right_id: self._brake_right}
            )
        except Exception:
            pass
        # Use the halt event so a fast stop() during settle still bails out quickly.
        self._pulse_halt.wait(timeout=dwell)

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

        loop_t0 = time.monotonic()
        log.info("hover forward pulse loop: ENTER series_max=%s", series_max)
        try:
            if not halt.is_set():
                self._pulse_ramp_goals_between(
                    brake_goals, coast_goals, eff_ramp, halt
                )
            prev_coast: Dict[int, int] = dict(coast_goals)
            for cycle_idx in range(series_max):
                cycle_t0 = time.monotonic()
                if halt.is_set():
                    log.info(
                        "hover fwd loop: halt SET before cycle %s/%s (t=%.2fs)",
                        cycle_idx, series_max, cycle_t0 - loop_t0,
                    )
                    break
                coast_dwell = coast_init
                self._pulse_ramp_goals_between(
                    prev_coast, goals, eff_ramp, halt
                )
                if halt.is_set():
                    log.info(
                        "hover fwd loop: halt SET after ramp-up cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                if self._imu_corrective_hold(
                    goals, main_hold, halt, is_forward=True
                ):
                    log.info(
                        "hover fwd loop: imu_hold returned True (=halt) in cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                self._pulse_ramp_goals_between(
                    goals, coast_goals, eff_ramp, halt
                )
                if halt.is_set():
                    log.info(
                        "hover fwd loop: halt SET after ramp-down cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                if self._imu_corrective_hold(
                    coast_goals, coast_dwell, halt, is_forward=True
                ):
                    log.info(
                        "hover fwd loop: imu_hold (coast) returned True in cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                prev_coast = dict(coast_goals)
                log.info(
                    "hover fwd cycle %s done dt=%.2fs total=%.2fs",
                    cycle_idx,
                    time.monotonic() - cycle_t0,
                    time.monotonic() - loop_t0,
                )

            if not halt.is_set():
                self._apply_goals(brake_goals)
        except Exception:
            log.exception(
                "hover fwd loop: EXCEPTION at t=%.2fs",
                time.monotonic() - loop_t0,
            )
            raise
        finally:
            log.info(
                "hover forward pulse loop: EXIT halt=%s total=%.2fs",
                halt.is_set(),
                time.monotonic() - loop_t0,
            )
            self._imu_end_straight()
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

        loop_t0 = time.monotonic()
        log.info("hover backward pulse loop: ENTER series_max=%s", series_max)
        try:
            if not halt.is_set():
                self._pulse_ramp_goals_between(
                    brake_goals, coast_goals, eff_ramp, halt
                )
            prev_coast: Dict[int, int] = dict(coast_goals)
            for cycle_idx in range(series_max):
                cycle_t0 = time.monotonic()
                if halt.is_set():
                    log.info(
                        "hover back loop: halt SET before cycle %s/%s (t=%.2fs)",
                        cycle_idx, series_max, cycle_t0 - loop_t0,
                    )
                    break
                coast_dwell = coast_init
                self._pulse_ramp_goals_between(
                    prev_coast, goals, eff_ramp, halt
                )
                if halt.is_set():
                    log.info(
                        "hover back loop: halt SET after ramp-up cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                if self._imu_corrective_hold(
                    goals, main_hold, halt, is_forward=False
                ):
                    log.info(
                        "hover back loop: imu_hold returned True in cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                self._pulse_ramp_goals_between(
                    goals, coast_goals, eff_ramp, halt
                )
                if halt.is_set():
                    log.info(
                        "hover back loop: halt SET after ramp-down cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                if self._imu_corrective_hold(
                    coast_goals, coast_dwell, halt, is_forward=False
                ):
                    log.info(
                        "hover back loop: imu_hold (coast) returned True in cycle %s (t=%.2fs)",
                        cycle_idx, time.monotonic() - loop_t0,
                    )
                    break
                prev_coast = dict(coast_goals)
                log.info(
                    "hover back cycle %s done dt=%.2fs total=%.2fs",
                    cycle_idx,
                    time.monotonic() - cycle_t0,
                    time.monotonic() - loop_t0,
                )

            if not halt.is_set():
                self._apply_goals(brake_goals)
        except Exception:
            log.exception(
                "hover back loop: EXCEPTION at t=%.2fs",
                time.monotonic() - loop_t0,
            )
            raise
        finally:
            log.info(
                "hover backward pulse loop: EXIT halt=%s total=%.2fs",
                halt.is_set(),
                time.monotonic() - loop_t0,
            )
            self._imu_end_straight()
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

    def stop(self, *, settle: bool = True) -> None:
        if not self._is_initialized:
            return
        self._halt_pulse_series(wait=True)
        self._apply_goals(
            {self._left_id: self._brake_left, self._right_id: self._brake_right}
        )
        if settle:
            time.sleep(float(getattr(self.config, "settle_delay_sec", 0.1)))
        if self._last_straight_key is not None:
            self._imu_end_straight()
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

        # Pivot: opposite leans. **Equal speeds at 100** ⇒ full calibrated pivot.
        # **Equal speeds below 100** ⇒ same blend on both axes (timed ~15° buttons).
        # **Unequal speeds** ⇒ per-axis blend (held D-pad: outer vs slow wheel).
        if left_speed > 0 and right_speed > 0 and lf != rf:
            turn_left_geom = bool(lf and not rf)
            ax = self._axis
            if (
                turn_left_geom
                and ax.turn_left_pos_left is not None
                and ax.turn_left_pos_right is not None
            ):
                l_tgt = self._dxl._clamp_pos(int(ax.turn_left_pos_left))
                r_tgt = self._dxl._clamp_pos(int(ax.turn_left_pos_right))
            elif (
                not turn_left_geom
                and ax.turn_right_pos_left is not None
                and ax.turn_right_pos_right is not None
            ):
                l_tgt = self._dxl._clamp_pos(int(ax.turn_right_pos_left))
                r_tgt = self._dxl._clamp_pos(int(ax.turn_right_pos_right))
            else:
                l_tgt, r_tgt = hover_computed_turn_pivot_goals(
                    ax, turn_left=turn_left_geom
                )
                l_tgt = self._dxl._clamp_pos(l_tgt)
                r_tgt = self._dxl._clamp_pos(r_tgt)
            if left_speed == right_speed:
                if left_speed >= 100:
                    lg, rg = l_tgt, r_tgt
                else:
                    u = max(0.0, min(1.0, left_speed / 100.0))
                    lg = int(round(nl + (l_tgt - nl) * u))
                    rg = int(round(nr + (r_tgt - nr) * u))
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
                # Reset the integrator each time we enter a fresh straight leg.
                self._imu_begin_straight()
            self._last_straight_key = sk
        else:
            if self._last_straight_key is not None:
                self._imu_end_straight()
            self._last_straight_key = None
        goals = self._goals_for_wheels(
            left_dir=left_dir,
            left_speed=left_speed,
            right_dir=right_dir,
            right_speed=right_speed,
        )
        # NOTE: we intentionally do not bias symmetric-straight goals here.
        # Continuous bias mid-motion was perceived as uncommanded fast turns.
        # Drift correction happens discretely inside the pulse loop holds via
        # ``_imu_corrective_hold`` (pause -> pivot -> re-prime -> resume).
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
        """Timed yaw: partial pivot lean (~15° default), hold, then brake.

        Blend toward calibrated pivot goals via ``_timed_turn_pivot_blend_pct``
        (not full 90° lean). Held D-pad pivots use asymmetric duties separately.
        """
        _ = speed_percent  # reserved; excursion is angle blend, not duty
        blend = _timed_turn_pivot_blend_pct()
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 0.0)
        )
        self.set_wheels(
            left_dir=self.DIR_FORWARD,
            left_speed=blend,
            right_dir=self.DIR_BACKWARD,
            right_speed=blend,
        )
        if dur > 0.0:
            time.sleep(dur)
        self.stop(settle=False)

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Timed yaw: partial pivot lean; mirror of :meth:`turn_left`."""
        _ = speed_percent
        blend = _timed_turn_pivot_blend_pct()
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 0.0)
        )
        self.set_wheels(
            left_dir=self.DIR_BACKWARD,
            left_speed=blend,
            right_dir=self.DIR_FORWARD,
            right_speed=blend,
        )
        if dur > 0.0:
            time.sleep(dur)
        self.stop(settle=False)

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
