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
lean. **Timed** ``turn_left`` / ``turn_right`` use full pivot goals (100% blend).
**Held** D-pad L/R use ``pulse_turn_micro_step`` at :data:`_HELD_DPAD_PIVOT_BLEND_PCT`
(30% of full pivot by default). Legacy unequal-duty path uses
``NINA_HOVER_TURN_SLOW_WHEEL_PCT`` vs outer ``speed_percent``. **Turn right** mirrors left.

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
controller **stops the wheels**, runs an **iterative micro-step realign**: a
chain of small decisive pivots that each (a) sample drift, (b) re-evaluate
pivot direction from the latest sample, (c) apply a pivot lean of
**proportional duration** (``PIVOT_BLEND_PCT`` × per-step duration computed
from remaining drift), (d) brake-settle. The loop exits when
``|drift| <= NINA_HOVER_IMU_CORR_DEADBAND_DEG``, on overshoot past zero,
on the wrong-direction bail (see below), on the **no-progress safeguard**
(``PROGRESS_CHECK_STEPS`` steps with less than ``PROGRESS_MIN_DEG`` of
total improvement → exit cleanly, let the next event try fresh), or at the
``MAX_STEPS`` hard cap. Only then does the controller **brake**, **re-prime**
the lean stack, and resume the primed forward / back pulse. The previous single-shot pivot was replaced because it either
undershot (too weak to overcome natural drift) or, when pumped up, slammed
the bot 30°+ in one go; iterative steps with live re-sampling let total
correction time scale with the magnitude of the drift instead of being
decided up-front.

Bail behaviour (defends against an un-flipped ``INVERT_SIGN`` on a new
chassis without false-tripping on momentum or correct-direction overshoot):
- The first ``NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS`` samples are ignored
  for the bail — the bot is still bleeding off angular momentum from the
  pre-brake forward lean, so drift growth during this window does not
  imply a wrong pivot direction.
- The bail only fires when drift is **still in the same sign as the
  original drift**. If the realign over-corrects past zero, the
  ``overshoot guard`` handles it (cleanly exits with reason "over-shot
  zero"); the wrong-direction bail does not fire on opposite-sign growth
  (that would mis-attribute a too-strong-but-correct pivot as a wrong
  direction).
- Past warmup, with same-sign drift, the bail pins ``yaw_after_warmup``
  on the first qualifying sample and only fires once
  ``|drift| > |yaw_after_warmup| + NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG``
  for two consecutive same-sign samples. Margin defaults to 5° so a
  single noisy read or minor momentum overshoot can't kill an otherwise
  correct realign.

Tuning env vars (defaults make each step visibly authoritative on the
hoverboard chassis; lower them only if the realign is over-shooting):

  ``NINA_HOVER_IMU_CORR_ENABLE``                default 1
  ``NINA_HOVER_IMU_CORR_THRESHOLD_DEG``         default 4.0   (only fire when drift gets noticeable)
  ``NINA_HOVER_IMU_CORR_DEADBAND_DEG``          default 1.5   (stop realign when drift returns inside this)
  ``NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT``       (legacy; in-motion pivots use held-D-pad blend)
  ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC``         (legacy; in-motion uses ``NINA_HOVER_TURN_STEP_*``)
  ``NINA_HOVER_IMU_CORR_STEP_MIN_SEC``          (legacy; in-motion uses ``NINA_HOVER_TURN_STEP_*``)
  ``NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC`` (legacy; in-motion uses ``NINA_HOVER_TURN_STEP_RATE``)
  ``NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC``       (legacy; standstill drift-correct only)

  In-motion left/right yaw realign during forward/back pulse holds uses the **same**
  lean pacing as held D-pad L/R (``NINA_HOVER_TURN_STEP_DUR_CAP_SEC`` default 0.26 s,
  ``NINA_HOVER_TURN_STEP_MIN_SEC`` 0.10 s, ``NINA_HOVER_TURN_STEP_SETTLE_SEC`` 0.12 s,
  ``NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC`` 22 dps, 30% pivot blend,
  ``NINA_HOVER_TURN_PRE_SETTLE_SEC`` / ``NINA_HOVER_TURN_POST_SETTLE_SEC``).
  ``NINA_HOVER_IMU_CORR_MAX_STEPS``             default 15    (hard cap on micro-step iterations;
                                                                with proportional sizing typical
                                                                events terminate in 1-3 steps)
  ``NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS``  default 4     (check progress after N steps;
                                                                set 0 to disable)
  ``NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG``      default 1.0   (min cumulative improvement in
                                                                ``|drift|`` required by the
                                                                progress-check step; below this
                                                                the realign exits as "no progress"
                                                                so the cooldown + next event can
                                                                try fresh instead of burning
                                                                MAX_STEPS on a stuck loop)
  ``NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS``     default 3     (steps before wrong-direction bail
                                                                can fire — absorbs pre-brake momentum)
  ``NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG``       default 5.0   (drift growth past post-warmup baseline
                                                                required to actually bail; must be
                                                                exceeded on 2 consecutive samples)
  ``NINA_HOVER_IMU_CORR_SETTLE_SEC``            default 0.25  (brake settle BEFORE the first step AND
                                                                AFTER the loop, before re-priming)
  ``NINA_HOVER_IMU_CORR_COOLDOWN_SEC``          default 1.0   (rest period after each correction before
                                                                the next IMU sample; persists across
                                                                pulse cycles so corrections don't fire
                                                                back-to-back)
  ``NINA_HOVER_IMU_CORR_POLL_HZ``               default 5     (drift sample rate inside pulse holds)
  ``NINA_HOVER_IMU_CORR_INVERT_SIGN``           default 0     (flip pivot direction if the IMU mount
                                                                orientation and / or ``NINA_HOVER_SWAP_TURN_LR``
                                                                combination makes positive drift map to
                                                                "pivot right" on this chassis. Symptom:
                                                                after warmup the realign still grows
                                                                drift past ``BAIL_MARGIN_DEG``. Set to
                                                                ``1`` then.)

Backward-direction overrides (THRESHOLD and STEP_RATE ship with their own
backward-optimised defaults baked in after field testing; COOLDOWN still
inherits from forward unless explicitly set):

  ``NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG``     default 6.0
                                                                (vs forward 4.0; higher because the
                                                                chassis naturally drifts to a ~+8 deg
                                                                rest pose in reverse and the forward
                                                                threshold fires spuriously on that
                                                                bias. Lower this to react earlier;
                                                                raise to let small drift slide.)
  ``NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC``      default = forward cooldown
                                                                (1.0 s works for both directions per
                                                                field testing; lower this if backward
                                                                natural drift outpaces the gap)
  ``NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC`` default 60.0
                                                                (vs forward 30.0; higher because
                                                                backward pivots are physically more
                                                                authoritative on this chassis -- the
                                                                forward 30.0 calibration over-shoots
                                                                when reused in reverse. Raise this if
                                                                backward still over-corrects; lower
                                                                if backward under-corrects.)

Stuck detector (defends against a feedback loop where the chassis is
stationary, the pivot lean can't rotate stationary wheels, every realign
exits as "no progress", and the regular short cooldown immediately fires
another equally-doomed realign — locking the bot in place):

  ``NINA_HOVER_IMU_CORR_STUCK_STREAK``           default 3     (consecutive ``no progress`` exits before
                                                                the stuck cooldown kicks in; set to 0 to
                                                                disable the detector entirely)
  ``NINA_HOVER_IMU_CORR_STUCK_COOLDOWN_SEC``     default 8.0   (cooldown applied when the detector fires;
                                                                long enough that the pulse loop gets at
                                                                least one full hold cycle to build wheel
                                                                momentum and break the chassis out of the
                                                                stationary state. Resets back to the
                                                                regular direction-aware cooldown on the
                                                                first successful realign — any non-
                                                                no-progress exit clears the streak)

Drift-abort (catastrophic-drift safety stop with spoken alert; sits
above the realign loop so a sign-flipped IMU mount, a failed wheel, or
any condition that puts the bot into a runaway spin terminates the
pulse leg cleanly instead of spending pulse cycles snaking off course):

  ``NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG``        default 30.0  (abort the pulse series when ``|drift|``
                                                                first crosses this magnitude on a fresh
                                                                IMU sample. Brakes the lean stack, sets
                                                                the pulse halt event, and queues a
                                                                single TTS announcement via
                                                                :func:`nina.services.sensor_alert_audio.maybe_speak_cant_move_alert`
                                                                — the exact phrase is
                                                                :data:`_IMU_CORR_ABORT_PHRASE`. The
                                                                operator must issue a fresh drive
                                                                command to resume — there is no
                                                                automatic retry. Set to ``0`` to
                                                                disable. Fires on both the forward
                                                                and backward pulse loops.)

Logging convention: every IMU-correction log line emitted from inside the
pulse hold (``hover IMU correction (forward): ...`` or
``hover IMU correction (backward): ...``) carries the direction tag of the
pulse loop that triggered it. The correction geometry is identical for
both directions (the pivot command is decided from the SIGN of the world-
frame drift sample, not from the chassis motion direction), but tagging
lets the operator confirm via ``grep`` that the backward pulse loop is
actually firing corrections — and at what rate / magnitude relative to
forward — without needing to cross-reference timestamps against the
``hover backward pulse series`` banner. The realign banner reports the
*effective* threshold / step_rate for the direction in which the
correction is firing, so a backward correction running under
``NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG=3.0`` will print ``>= 3.00 deg
threshold`` even when forward is configured for 4.0.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
import traceback
from typing import Callable, Dict, Optional, Tuple

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS, DynamixelManager
from nina.services.sensor_alert_audio import maybe_speak_cant_move_alert

log = logging.getLogger("nina.hoverboard_axis")

# Spoken when the drift-abort fires; kept as a module constant so tests can
# assert on the exact phrase the operator will hear and so the bldc speech
# alerts cooldown (12 s default) coalesces repeated aborts within one leg.
_IMU_CORR_ABORT_PHRASE = "I can't move steadily any further, stopping now."

# Yaw drift sampler — returns signed degrees ``+`` = bot drifted right, ``-`` = left,
# or ``None`` when the integrator is paused / calibrating / unavailable.
ImuYawDriftFn = Callable[[], Optional[float]]
# Instantaneous yaw rate sampler — returns signed deg/s (sign convention matches
# the drift sampler) or ``None`` when the gyro is unavailable. Used by the new
# active-settle (wait-until-chassis-is-actually-still) helper in the forward
# straight loop; if not wired the loop falls back to a fixed timer.
ImuYawRateFn = Callable[[], Optional[float]]
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
    """Move *goal* *push* raw ticks away from *brake* (signed).

    Positive *push* moves further from brake (i.e. MORE lean, in
    whichever direction *goal* already sits relative to *brake*).
    Negative *push* pulls back toward brake (LESS lean). ``push == 0``
    is a no-op. If *goal* is exactly at *brake* there's no defined
    "away" direction so we return *goal* unchanged regardless of
    *push*.

    Used by:
    - the forward straight branch (per-side raw delta on ``forward_pos_*``,
      then ``_STRAIGHT_FWD_EXTRA_TICKS`` away from brake),
    - the turn pivot helper (positive ``turn_push_ticks`` /
      ``_TURN_PIVOT_GOAL_OFFSET_TICKS``),
    - the backward straight branch (signed per-side
      ``_STRAIGHT_BACK_{LEFT,RIGHT}_TICKS_OFFSET`` — operator can
      either reinforce the calibrated lean (positive) or trim it back
      toward neutral (negative) without re-tuning ``backward_pos_*``).
    """
    if push == 0:
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

# Per-side raw tick delta added to ``forward_pos_*`` before the straight
# FWD extra nudge. Matches operator bench language (±N ticks on the goal).
# New mechanical structure: motor 12 (left) −10, motor 13 (right) +10.
_STRAIGHT_FWD_LEFT_TICKS_OFFSET = -10
_STRAIGHT_FWD_RIGHT_TICKS_OFFSET = 10


def _straight_fwd_left_ticks_offset() -> int:
    """Raw tick delta on ``forward_pos_left`` for straight FWD (env:
    ``NINA_HOVER_STRAIGHT_FWD_LEFT_TICKS_OFFSET``, default −10)."""
    try:
        return max(
            -50,
            min(
                50,
                int(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_FWD_LEFT_TICKS_OFFSET",
                        str(_STRAIGHT_FWD_LEFT_TICKS_OFFSET),
                    )
                ),
            ),
        )
    except ValueError:
        return _STRAIGHT_FWD_LEFT_TICKS_OFFSET


def _straight_fwd_right_ticks_offset() -> int:
    """Signed trim on ``forward_pos_right`` for straight FWD (env:
    ``NINA_HOVER_STRAIGHT_FWD_RIGHT_TICKS_OFFSET``, default +10)."""
    try:
        return max(
            -50,
            min(
                50,
                int(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_FWD_RIGHT_TICKS_OFFSET",
                        str(_STRAIGHT_FWD_RIGHT_TICKS_OFFSET),
                    )
                ),
            ),
        )
    except ValueError:
        return _STRAIGHT_FWD_RIGHT_TICKS_OFFSET


# Per-side SIGNED nudge magnitudes for the straight BACK leg, applied
# via :func:`_nudge_goal_from_brake` (same mechanism the forward branch
# uses, just with independent per-side magnitudes). Positive value =
# MORE backward lean for that side; negative = LESS lean (pulled
# toward brake). This direction-of-lean-aware nudge is required
# because ``backward_pos_left`` and ``backward_pos_right`` can sit on
# OPPOSITE sides of brake on the same chassis — a raw integer
# ``goal + offset`` would push one side's lean further out and the
# other side's lean closer to neutral for the SAME positive offset,
# which is exactly the bug that produced the "motor 12 leans, motor 13
# doesn't get pushed, chassis spins" symptom in the field.
#
# Defaults are 0 / 0 — i.e. drive the bare calibrated
# ``backward_pos_*`` straight through with no per-side trim.
# Operator's bench-validated calibration on the reference chassis
# (``backward_pos_left = 2005``, ``backward_pos_right = 1995``, both
# BELOW brake=2048) already carries the asymmetric lean it needs
# baked into the calibrated source itself, so no additional per-side
# bias is needed by default.
#
# Operators can re-tune per-bot via
# ``NINA_HOVER_STRAIGHT_BACK_LEFT_TICKS_OFFSET`` and
# ``NINA_HOVER_STRAIGHT_BACK_RIGHT_TICKS_OFFSET`` (signed, clamped to
# ``[-50, 50]``). Positive values add lean (further from brake);
# negative trim the calibrated lean closer to neutral without
# re-tuning ``backward_pos_*`` itself.
_STRAIGHT_BACK_LEFT_TICKS_OFFSET = 0
_STRAIGHT_BACK_RIGHT_TICKS_OFFSET = 0


def _straight_back_left_ticks_offset() -> int:
    """Signed raw tick offset added to ``backward_pos_left`` for the
    straight BACK leg (env: ``NINA_HOVER_STRAIGHT_BACK_LEFT_TICKS_OFFSET``,
    default :data:`_STRAIGHT_BACK_LEFT_TICKS_OFFSET`).

    Clamped to ``[-50, 50]`` so a typo can't drive the lean stack into
    a saturated corner. Garbage falls back to the documented default.
    See the module-level comment near
    :data:`_STRAIGHT_BACK_LEFT_TICKS_OFFSET` for the wheel-asymmetry
    rationale.
    """
    try:
        return max(
            -50,
            min(
                50,
                int(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_BACK_LEFT_TICKS_OFFSET",
                        str(_STRAIGHT_BACK_LEFT_TICKS_OFFSET),
                    )
                ),
            ),
        )
    except ValueError:
        return _STRAIGHT_BACK_LEFT_TICKS_OFFSET


def _straight_back_right_ticks_offset() -> int:
    """Signed raw tick offset added to ``backward_pos_right`` for the
    straight BACK leg (env: ``NINA_HOVER_STRAIGHT_BACK_RIGHT_TICKS_OFFSET``,
    default :data:`_STRAIGHT_BACK_RIGHT_TICKS_OFFSET`).

    Clamped to ``[-50, 50]``. See the per-side asymmetric-trim notes
    near :data:`_STRAIGHT_BACK_LEFT_TICKS_OFFSET`.
    """
    try:
        return max(
            -50,
            min(
                50,
                int(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_BACK_RIGHT_TICKS_OFFSET",
                        str(_STRAIGHT_BACK_RIGHT_TICKS_OFFSET),
                    )
                ),
            ),
        )
    except ValueError:
        return _STRAIGHT_BACK_RIGHT_TICKS_OFFSET

# Pivot only (L/R yaw): extra nudge away from each side's brake (after
# ``turn_push_ticks``). Must use directional nudge — a raw +Δ on both goals
# can land on brake and wipe blended timed-turn motion for that axis.
_TURN_PIVOT_GOAL_OFFSET_TICKS = 100

# IMU drift correction + closed-loop 90° turns: full calibrated pivot goals.
_PIVOT_MICROSTEP_BLEND_PCT = 100

# Held D-pad left/right (``pulse_turn_micro_step``) and timed turn_* fallback:
# 30% = 70% reduction from full pivot (mechanical shock).
_HELD_DPAD_PIVOT_BLEND_PCT = 30


def hover_computed_turn_pivot_goals(
    axis: HoverboardAxisSettings,
    *,
    turn_left: bool,
) -> tuple[int, int]:
    """Full pivot (id_left, id_right) raw goals from FWD/REV + push ticks + pivot offset.

    Used by :meth:`HoverboardAxisDrive._goals_for_wheels` when optional
    per-turn overrides are unset.
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
    """At/above this |drift|, pause forward motion and run the incremental
    realign. Higher = less twitchy but each correction does more work.
    """
    try:
        return max(
            0.1,
            min(45.0, float(os.environ.get("NINA_HOVER_IMU_CORR_THRESHOLD_DEG", "4.0"))),
        )
    except ValueError:
        return 4.0


def _imu_corr_deadband_deg() -> float:
    """The micro-step realign exits once ``|drift| <= deadband``. Smaller =
    tighter heading hold but more micro-steps per correction.
    """
    try:
        return max(
            0.0,
            min(10.0, float(os.environ.get("NINA_HOVER_IMU_CORR_DEADBAND_DEG", "1.5"))),
        )
    except ValueError:
        return 1.5


def _imu_corr_pivot_blend_pct() -> int:
    """Per-micro-step pivot blend % — always full pivot (:data:`_PIVOT_MICROSTEP_BLEND_PCT`)."""
    return _PIVOT_MICROSTEP_BLEND_PCT


def _imu_corr_pivot_max_sec() -> float:
    """**Per-micro-step** pivot duration cap. Default 0.18 s so each step
    rotates the bot a visible 1.5-2° at the 20 % blend (the MX-28 needs
    >100 ms of commanded goal to actually slew there). The realign loop
    reads the live drift after every step, so total correction time
    auto-scales with the magnitude of the drift instead of being decided
    up-front.
    """
    try:
        return max(
            0.02,
            min(3.0, float(os.environ.get("NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC", "0.18"))),
        )
    except ValueError:
        return 0.18


def _imu_corr_step_settle_sec() -> float:
    """Brake dwell BETWEEN micro-steps so the IMU re-samples on a still bot.

    Smaller = faster total realign, but the integrator can be noisy if the
    chassis is still rotating from the last step. 0.08 s is the default.
    """
    try:
        return max(
            0.0,
            min(1.0, float(os.environ.get("NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC", "0.08"))),
        )
    except ValueError:
        return 0.08


def _imu_corr_max_steps() -> int:
    """Hard cap on micro-step iterations inside one realign. Worst-case the
    routine corrects ``max_steps * step_rotation`` of drift before giving
    up. Default 15 keeps the routine bounded to ~4 s at the default per-step
    timings while still giving ~20-30° of correction head-room.
    """
    try:
        return max(
            1,
            min(200, int(float(os.environ.get("NINA_HOVER_IMU_CORR_MAX_STEPS", "15")))),
        )
    except ValueError:
        return 15


def _imu_corr_step_rate_deg_per_sec() -> float:
    """Empirical chassis rotation rate (deg/s) at the configured per-step
    blend. Used to make the realign **proportional**: each step's duration
    is scaled by the remaining drift so small drifts get short pulses and
    big drifts get the full ``PIVOT_MAX_SEC`` cap. Default 30 deg/s
    matches the 20 % blend on the current hoverboard build (bench
    measured); tune up if the bot still over-shoots small drifts, or down
    if it visibly under-corrects.
    """
    try:
        return max(
            1.0,
            min(360.0, float(os.environ.get("NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC", "30.0"))),
        )
    except ValueError:
        return 30.0


def _imu_corr_step_min_sec() -> float:
    """Floor on the per-step duration. The MX-28 has ~50 ms of slew
    latency before any visible rotation; pulses below this floor are
    effectively no-ops. Default 0.08 s puts a little headroom above the
    slew latency so even the smallest proportional step produces measurable
    rotation.
    """
    try:
        return max(
            0.01,
            min(1.0, float(os.environ.get("NINA_HOVER_IMU_CORR_STEP_MIN_SEC", "0.08"))),
        )
    except ValueError:
        return 0.08


def _imu_corr_progress_check_steps() -> int:
    """Number of micro-steps the realign will run before checking that it's
    actually making progress. If, after this many steps, drift hasn't
    improved by at least ``PROGRESS_MIN_DEG``, the realign exits cleanly
    with reason "no progress" so the cooldown + next-event cycle can try
    fresh instead of burning ``MAX_STEPS`` worth of ineffective pulses.

    Set to 0 to disable the check entirely (legacy max-steps-only behaviour).
    """
    try:
        return max(
            0,
            min(50, int(float(os.environ.get("NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS", "4")))),
        )
    except ValueError:
        return 4


def _imu_corr_progress_min_deg() -> float:
    """Minimum improvement in ``|drift|`` (deg) the realign must show by
    step ``PROGRESS_CHECK_STEPS``, otherwise it bails as "no progress".

    Defaults to 1.0 — very lenient (only 0.25 deg/step on average after
    4 steps). Tighter than this would be too noisy.
    """
    try:
        return max(
            0.0,
            min(20.0, float(os.environ.get("NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG", "1.0"))),
        )
    except ValueError:
        return 1.0


def _imu_corr_bail_warmup_steps() -> int:
    """Micro-steps to skip at the START of a realign before the wrong-direction
    bail can fire. The bot has angular momentum from the forward lean when
    the realign brakes; the first 1-3 samples can show drift growth that
    has nothing to do with the pivot direction — they're just the chassis
    bleeding off its pre-brake rotation. Default 3 absorbs that, then the
    bail is allowed to fire on real sign-mismatch evidence.
    Set to a very large number to effectively disable the bail.
    """
    try:
        return max(
            0,
            min(50, int(float(os.environ.get("NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS", "3")))),
        )
    except ValueError:
        return 3


def _imu_corr_bail_margin_deg() -> float:
    """How far ``|drift|`` must exceed the post-warmup baseline before the
    wrong-direction bail fires. The 2-consecutive-growth check then has to
    BOTH be growing AND beyond this margin to bail, so a single noisy
    sample or a brief momentum overshoot can't kill an otherwise correct
    realign. Default 5° (much larger than the typical per-step rotation).
    """
    try:
        return max(
            0.5,
            min(45.0, float(os.environ.get("NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG", "5.0"))),
        )
    except ValueError:
        return 5.0


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


def _imu_corr_invert_sign() -> bool:
    """Flip the IMU drift sign before choosing a pivot direction.

    Defaults to off so behaviour matches the original convention
    ("positive drift = drifted right, pivot left"). Set to ``1`` on chassis
    where the MPU mount orientation and / or ``NINA_HOVER_SWAP_TURN_LR``
    combination cause every correction to **add** drift in the same
    direction (a sure sign the pivot is going the wrong way for that
    geometry).
    """
    raw = (os.environ.get("NINA_HOVER_IMU_CORR_INVERT_SIGN") or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _imu_corr_poll_hz() -> float:
    """How often the pulse hold samples yaw drift. Lower = calmer / fewer pivots."""
    try:
        return max(
            1.0,
            min(60.0, float(os.environ.get("NINA_HOVER_IMU_CORR_POLL_HZ", "5.0"))),
        )
    except ValueError:
        return 5.0


# ----------------------------------------------------------------------
# Backward-direction overrides
#
# The forward pulse and the backward pulse share the same IMU correction
# code path (see ``_imu_corrective_hold`` / ``_perform_pivot_correction``)
# because the pivot direction is derived from the SIGN of the world-frame
# drift sample, not from the chassis motion direction. The mechanics,
# however, are NOT symmetric — on a typical hoverboard build the natural
# yaw rate while reversing can be one to two orders of magnitude higher
# than while going forward (mismatched brake / coast lean per wheel in
# reverse, hub-motor freewheel asymmetry, weight transfer onto the
# trailing wheel, etc.). The forward-only tunables that work well at
# ~0.2 deg/s natural drift produce a visible "snake" pattern at the
# ~30 deg/s natural drift rate observed during reverse.
#
# THRESHOLD and STEP_RATE have their own backward-optimised defaults baked
# in after field testing on the hoverboard chassis (operator "Option B"
# run: bot translated cleanly with smooth correction cycles). COOLDOWN
# still inherits from forward because the forward 1.0 s default also gave
# healthy backward rhythm during field testing. The operator can override
# any of the three via env vars; these constants are only the fallback
# when the env is unset / unparseable.
_IMU_CORR_BACK_DEFAULT_THRESHOLD_DEG = 6.0       # vs forward 4.0
_IMU_CORR_BACK_DEFAULT_STEP_RATE_DPS = 60.0      # vs forward 30.0
# ----------------------------------------------------------------------


def _imu_corr_back_threshold_deg(default: float) -> float:
    """Backward drift threshold (env: ``NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG``).

    When unset or unparseable, returns *default*. The call site passes
    :data:`_IMU_CORR_BACK_DEFAULT_THRESHOLD_DEG` (6.0 — backward-optimised)
    rather than the forward threshold, because the backward leg behaves
    qualitatively differently: a forward-tuned 4.0 threshold fires
    spuriously at the chassis's natural backward rest pose (typically
    ~+8 deg) and locks the bot in a thrashing loop. Lower this to react
    to drift sooner; raise it to let small drift slide.

    Clamped to ``[0.1, 45]`` (same range as the forward helper).
    """
    raw = os.environ.get("NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG")
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0.1, min(45.0, float(raw)))
    except ValueError:
        return default


def _imu_corr_back_cooldown_sec(default: float) -> float:
    """Backward cooldown override (env: ``NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC``).

    Unlike threshold and step_rate, cooldown still defaults to the
    *forward* value (call site passes ``self._imu_corr_cooldown_sec``)
    because field testing showed forward's 1.0 s default also produces a
    healthy backward correction rhythm. Set this env var lower if the
    chassis's natural backward drift rate is so high that 1 s lets too
    much drift accumulate between corrections.

    Clamped to ``[0.0, 10.0]`` (same range as the forward helper).
    """
    raw = os.environ.get("NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC")
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(0.0, min(10.0, float(raw)))
    except ValueError:
        return default


def _imu_corr_back_step_rate_deg_per_sec(default: float) -> float:
    """Backward per-step rotation rate (env: ``NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC``).

    The proportional micro-step duration is computed as
    ``|drift| / step_rate``, so a HIGHER rate produces SHORTER pulses.
    The call site passes :data:`_IMU_CORR_BACK_DEFAULT_STEP_RATE_DPS`
    (60.0) rather than the forward 30.0, because backward pivots on this
    chassis are physically more authoritative than forward pivots — the
    forward calibration over-shoots when reused in reverse. Raise this
    if backward still over-corrects; lower it if backward under-corrects.

    Clamped to ``[1.0, 360.0]`` (same range as the forward helper).
    """
    raw = os.environ.get("NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC")
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(1.0, min(360.0, float(raw)))
    except ValueError:
        return default


# ----------------------------------------------------------------------
# Stuck detector
#
# The iterative realign assumes the pivot lean can actually rotate the
# chassis. That assumption breaks when the bot is STATIONARY (friction
# locks the wheels in place; commanded lean changes the goal positions
# but the chassis doesn't move). Symptom in the log: a streak of
# realigns that all exit with reason "no progress" and ``drift_now``
# essentially equal to ``drift_start``. With the default 1 s (forward)
# or 0.3 s (backward) cooldown, the next correction fires immediately
# after the failed one, never giving the pulse loop enough uninterrupted
# time to build wheel momentum and break the chassis free.
#
# The stuck detector counts consecutive "no progress" exits across
# realigns. When the count reaches ``STUCK_STREAK`` the next cooldown
# is replaced with ``STUCK_COOLDOWN_SEC`` (much longer) so the pulse
# loop gets a clean window to drive the chassis. Any non-no-progress
# exit (deadband reached, overshoot past zero, wrong-direction bail,
# max-steps cap) resets the streak immediately. The streak is also
# reset at the start of every fresh straight leg (see
# ``_imu_begin_straight``).
# ----------------------------------------------------------------------


def _imu_corr_stuck_streak() -> int:
    """Consecutive ``no progress`` exits before the stuck cooldown kicks in.

    Read from ``NINA_HOVER_IMU_CORR_STUCK_STREAK``. Default 3 — a single
    no-progress exit is usually a transient drift burst, but 3 in a row
    is a strong signal the pivots aren't rotating the chassis at all
    (typically because the bot is stationary). Set to 0 to disable the
    stuck detector and fall back to the legacy "always use the
    direction-aware cooldown" behaviour.

    Clamped to ``[0, 50]``.
    """
    try:
        return max(
            0,
            min(50, int(float(os.environ.get("NINA_HOVER_IMU_CORR_STUCK_STREAK", "3")))),
        )
    except ValueError:
        return 3


def _imu_corr_stuck_cooldown_sec() -> float:
    """Cooldown applied when the stuck detector fires (seconds).

    Read from ``NINA_HOVER_IMU_CORR_STUCK_COOLDOWN_SEC``. Default 8.0 —
    long enough for the pulse loop to complete at least one full hold
    cycle (typically 1-2 s per cycle on the default ``pulse_series_*``
    settings) so the chassis can actually build wheel momentum and
    break out of the stationary state. Field testing showed 8 s lets
    the bot translate noticeably further between stuck cycles than the
    previous 5 s default. Lower this if the bot ends up drifting too
    far during the stuck window; raise it if the bot still can't break
    free.

    Clamped to ``[0.0, 60.0]``.
    """
    try:
        return max(
            0.0,
            min(60.0, float(os.environ.get("NINA_HOVER_IMU_CORR_STUCK_COOLDOWN_SEC", "8.0"))),
        )
    except ValueError:
        return 8.0


# ----------------------------------------------------------------------
# Drift-corrected straight forward (replaces the pulse-series + in-motion
# realign for forward motion). The chassis drives in fixed-duration legs
# (NINA_HOVER_STRAIGHT_LEG_SEC each) separated by brake + settle dwells.
# Drift is read at standstill after every leg — much more reliable than
# during motion because the integrator is no longer racing with active
# wheel rotation. Above ABORT_DRIFT_DEG the bot speaks the cant_move
# alert and halts.
# ----------------------------------------------------------------------


def _straight_leg_sec() -> float:
    """Forward-leg duration (seconds) between brake + drift-check pauses.

    Each press of Straight forward (or D-pad forward in pulse mode)
    drives the chassis at full FWD lean for this duration, brakes,
    samples drift, optionally corrects, and repeats until the operator
    releases the button.

    Default 0.5 s. The reference chassis accumulates ~10–25° of drift
    in a 1 s leg (the 0.5 s default halves the per-cycle drift the
    correction loop has to chase, keeps each unsupervised excursion
    short, and roughly doubles the rate of drift sampling). Clamped
    to ``[0.1, 5.0]`` — override with
    ``NINA_HOVER_STRAIGHT_LEG_SEC=<seconds>``.
    """
    try:
        return max(
            0.1,
            min(5.0, float(os.environ.get("NINA_HOVER_STRAIGHT_LEG_SEC", "0.5"))),
        )
    except ValueError:
        return 0.5


def _straight_back_leg_sec() -> float:
    """Backward-leg duration (seconds) between brake + drift-check pauses.

    Mirror of :func:`_straight_leg_sec` but scoped to *backward*
    motion. Default **0.5 s** (matches forward) — once the per-side
    asymmetric trim
    (:func:`_straight_back_left_ticks_offset` /
    :func:`_straight_back_right_ticks_offset`) compensates for the
    BLDC-side wheel asymmetry, backward travels at a similar
    rate to forward and there's no reason to make legs longer.

    Independent of :func:`_straight_leg_sec` — overriding one direction
    must not bleed into the other. Clamped to ``[0.1, 5.0]``. Override
    with ``NINA_HOVER_STRAIGHT_BACK_LEG_SEC=<seconds>``.
    """
    try:
        return max(
            0.1,
            min(
                5.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_BACK_LEG_SEC", "0.5")
                ),
            ),
        )
    except ValueError:
        return 0.5


def _straight_brake_settle_sec() -> float:
    """Brake settle dwell after each forward leg, before sampling drift.

    Lets the chassis stop rotating from any residual motion so the IMU
    integrator sample is a clean post-leg drift measurement. Default
    0.30 s. Clamped to ``[0.0, 2.0]``.
    """
    try:
        return max(
            0.0,
            min(
                2.0,
                float(os.environ.get("NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC", "0.30")),
            ),
        )
    except ValueError:
        return 0.30


def _straight_corr_swap_pivot_dir() -> bool:
    """Swap the pivot-direction-to-goals mapping in standstill correction.

    Field-observed on the reference chassis: commanding what the code
    labels "pivot LEFT" (L=FWD, R=BACK at the dynamixel-lean level)
    physically rotates the chassis to the **RIGHT** (CW), not the left
    as the label suggests. With an at-standstill correction (and the
    yaw-rate active settle confirming the chassis is genuinely
    stopped before the pivot fires), the mapping is the only
    remaining unknown — so we hardcode the swap as the default.

    When True, a "pivot_left" decision uses ``L=BACK, R=FWD`` goals and
    a "pivot_right" decision uses ``L=FWD, R=BACK`` goals — i.e. the
    label-to-goals mapping is inverted relative to the conventional
    convention. Default **off** after the fleet hall FWD/REV swap in
    ``settings.py``; set ``NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR=1``
    if standstill correction still pivots the wrong way. Affects ONLY
    the at-standstill
    drift correction; the closed-loop 90° turn and the backward
    in-motion correction are unchanged.

    Unrelated to ``NINA_HOVER_IMU_CORR_INVERT_SIGN`` — that knob
    flips the **drift sign interpretation** (whether positive drift
    means CCW or CW). This knob flips the **pivot command mapping**
    (whether a "pivot LEFT" decision uses one set of goals or the
    other). On chassis with both inversions you'd set both.
    """
    val = os.environ.get("NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR", "0")
    return val.strip().lower() not in ("0", "false", "no", "off", "")


def _straight_settle_rate_dps() -> float:
    """Yaw-rate threshold (deg/s) below which the chassis is "still".

    The drift-corrected forward loop polls the IMU's instantaneous yaw
    rate after braking; once ``|rate|`` drops below this for
    ``SETTLE_STABLE_SEC`` continuous, the chassis is treated as
    stationary and we can sample drift / start a correction step.
    Default **3.0 °/s** — small enough that any residual rotation
    won't corrupt the drift sample over the next sampling window,
    large enough not to chase gyro noise on a quiet IMU. Clamped to
    ``[0.1, 30.0]``.
    """
    try:
        return max(
            0.1,
            min(
                30.0,
                float(os.environ.get("NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS", "3.0")),
            ),
        )
    except ValueError:
        return 3.0


def _straight_settle_stable_sec() -> float:
    """Continuous window (seconds) the yaw rate must stay below the
    settle threshold before the chassis is declared still.

    Default **0.10 s** — a 100 ms quiet window filters out brief gyro
    noise dips without forcing the loop to wait excessively after a
    real stop. Clamped to ``[0.0, 2.0]`` (set to 0 to declare stillness
    on a single sub-threshold sample).
    """
    try:
        return max(
            0.0,
            min(
                2.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC", "0.10")
                ),
            ),
        )
    except ValueError:
        return 0.10


def _straight_settle_max_sec() -> float:
    """Hard cap (seconds) on the active-settle wait before bailing.

    If the chassis hasn't settled within this many seconds after a
    brake command, the loop logs a warning, skips the drift sample
    for this cycle, and moves on to the next forward leg rather than
    sampling stale / moving-chassis drift. Default **1.50 s** —
    several times longer than a normal hoverboard stops but short
    enough that a stuck wheel or runaway condition doesn't block the
    loop indefinitely. Clamped to ``[0.05, 10.0]``.
    """
    try:
        return max(
            0.05,
            min(
                10.0,
                float(os.environ.get("NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC", "1.50")),
            ),
        )
    except ValueError:
        return 1.50


def _straight_settle_poll_sec() -> float:
    """Polling interval (seconds) for the active-settle yaw-rate loop.

    Default **0.02 s** = 50 Hz. Matches the typical MPU-9250 sample
    cadence so we don't oversample stale values nor under-sample and
    miss a brief sub-threshold window. Clamped to ``[0.001, 0.5]``.
    """
    try:
        return max(
            0.001,
            min(
                0.5,
                float(os.environ.get("NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC", "0.02")),
            ),
        )
    except ValueError:
        return 0.02


def _straight_corr_blend_pct() -> int:
    """Standstill drift-correction micro-step blend % — always full pivot."""
    return _PIVOT_MICROSTEP_BLEND_PCT


def _straight_corr_step_dur_cap_sec() -> float:
    """Per-step pivot duration cap (seconds) for drift correction.

    Each correction step pivots at the configured blend for
    ``min(cap, max(step_min, |drift| / step_rate_dps))`` seconds.

    Default **0.030 s** — matches ``step_min`` so every correction
    step is the same controlled magnitude. Bigger drifts converge
    by doing *more* equal-sized kicks (with active-settle + drift
    re-sample between each) rather than one over-strong kick. The
    earlier 0.05 s cap meant a 7° drift fired one 0.05 s kick that,
    with the chassis's pre-correction momentum getting released,
    produced 10–26° of rotation and badly overshot. Forcing every
    kick to 0.030 s caps that release energy regardless of drift
    magnitude.

    The legacy ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC`` (0.18 s, for
    the 20%-blend in-motion backward correction) is **not** changed
    by this knob. Bump back up if a chassis pivots so slowly that
    even 0.030 s isn't enough to make progress; reduce further if
    it over-rotates per step. Clamped to ``[0.005, 1.0]``.
    """
    try:
        return max(
            0.005,
            min(
                1.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC", "0.030")
                ),
            ),
        )
    except ValueError:
        return 0.030


def _straight_corr_step_min_sec() -> float:
    """Per-step pivot duration floor (seconds) for drift correction.

    Default **0.030 s** — empirically the smallest single kick that
    reliably overcomes chassis static friction + asymmetric
    forward-leg residual on the reference chassis. Field log showed
    a 0.044 s kick achieving 5.23° of clean rotation (cycle 1 of a
    test run), while clusters of 0.015 s kicks rocked the chassis
    unpredictably — some steps moved the right way, others the wrong
    way, often netting a *worse* drift after 15 attempts. The 0.030 s
    floor sits in the proven-reliable regime.

    Paired with the target-zero step-duration formula and the 3.0°
    deadband, every correction that fires is a single decisive kick
    (or at most two for drifts >7°) instead of a 15-step chase that
    fights the chassis bias. Slight overshoot into the opposite-
    direction half of the deadband is bounded and absorbed on the
    next leg's drift sample.

    The legacy ``NINA_HOVER_IMU_CORR_STEP_MIN_SEC`` (0.08 s, tuned
    for 20%-blend in-motion correction) is **not** changed by this
    knob. Clamped to ``[0.005, 1.0]``.
    """
    try:
        return max(
            0.005,
            min(
                1.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC", "0.030")
                ),
            ),
        )
    except ValueError:
        return 0.030


def _straight_corr_back_step_dur_cap_sec() -> float:
    """Per-step pivot duration cap (seconds) for **backward** drift correction.

    Mirror of :func:`_straight_corr_step_dur_cap_sec` but scoped to
    correction cycles that fire after a *backward* leg. Default
    **0.020 s** vs the forward default **0.030 s** — field testing
    on the reference chassis showed the same 0.030 s kick that
    produces a clean ~2.5° rotation when correcting from a forward
    leg produces 5–8° of rotation when correcting from a backward
    leg (same step blend, step rate, brake settle — only the prior
    leg direction differs). The mechanical explanation: the
    pre-pivot momentum profile is asymmetric on this chassis, so
    the calibrated 84 dps step rate underestimates the actual
    backward-correction rotation rate, and the empirical floor of
    0.030 s ends up over-pushing per step on backward.

    Independent of :func:`_straight_corr_step_dur_cap_sec` so the
    operator can tune forward and backward correction separately —
    the forward path is unaffected by overrides to this knob, and
    vice versa. Forward defaults stay 0.030 s; backward defaults
    drop to 0.020 s.

    Pair with :func:`_straight_corr_back_step_min_sec` (default
    0.015 s) so the proportional formula
    ``max(min, min(cap, |drift|/rate))`` actually shrinks for small
    drifts on backward (with the forward 0.030/0.030 the formula
    floors and caps at the same value, defeating proportional
    scaling). Clamped to ``[0.005, 1.0]``.
    """
    try:
        return max(
            0.005,
            min(
                1.0,
                float(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC", "0.020"
                    )
                ),
            ),
        )
    except ValueError:
        return 0.020


def _straight_corr_back_step_min_sec() -> float:
    """Per-step pivot duration floor (seconds) for **backward** drift correction.

    Mirror of :func:`_straight_corr_step_min_sec` but scoped to
    correction cycles that fire after a *backward* leg. Default
    **0.015 s** vs the forward default **0.030 s**. See
    :func:`_straight_corr_back_step_dur_cap_sec` for the chassis-
    asymmetry rationale; in short, the backward correction
    over-pushes at the forward floor, so backward needs a lower
    floor to let the proportional formula actually scale small
    drifts down.

    Independent of :func:`_straight_corr_step_min_sec` — the forward
    path is unaffected by overrides here. Clamped to ``[0.005, 1.0]``.
    """
    try:
        return max(
            0.005,
            min(
                1.0,
                float(
                    os.environ.get(
                        "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC", "0.015"
                    )
                ),
            ),
        )
    except ValueError:
        return 0.015


def _straight_corr_step_rate_dps() -> float:
    """Expected chassis pivot rate (deg/s) used to size each step's duration.

    The standstill correction picks each step's duration as
    ``|drift| / step_rate_dps`` (clamped to ``[step_min, step_dur_cap]``).
    Default **84 °/s** — empirical 140 °/s at 100% lean scaled by
    the 60% default blend (``140 × 0.60 = 84``). Tune per chassis
    *and* per blend: do one full-blend pivot pulse at the blend
    you're using, measure ``Δyaw / pulse_sec``, plug it in.

    Note this value is mostly cosmetic now that ``step_min`` and
    ``step_dur_cap`` are both 0.030 s by default — every kick is
    clamped to 0.030 s regardless of what the formula computes. The
    rate still matters if you raise the cap or lower the floor.

    The legacy ``NINA_HOVER_IMU_CORR_STEP_RATE_DPS`` (30 °/s, tuned
    for 20%-blend in-motion backward correction) is **not** changed
    by this knob. Clamped to ``[1.0, 500.0]``.
    """
    try:
        return max(
            1.0,
            min(
                500.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_CORR_STEP_RATE_DPS", "84.0")
                ),
            ),
        )
    except ValueError:
        return 84.0


def _straight_corr_residual_deg() -> float:
    """Drift magnitude (deg) at which the correction loop EXITS.

    Decouples the *trigger* threshold (``deadband``) from the *exit*
    threshold so the bot doesn't stop correcting at the deadband
    edge and leave 2–3° of residual heading drift that compounds
    cycle-after-cycle into a mild curve. Once correction fires
    (because ``|drift| > deadband``), it continues until
    ``|drift| <= residual`` — i.e. all the way back to near zero.

    Default **1.0°**. This is hysteresis: drift can wander up to
    ``deadband`` (3.0°) before correction fires, then is pulled
    back inside ``residual`` (1.0°) — giving a 2° "buffer" before
    the next correction can fire and preventing chattering at the
    boundary.

    Should be < ``deadband`` (otherwise correction enters then
    immediately exits). Clamped to ``[0.1, 30.0]``.

    Override via ``NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG=<deg>``.
    """
    try:
        return max(
            0.1,
            min(
                30.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG", "1.0")
                ),
            ),
        )
    except ValueError:
        return 1.0


def _straight_corr_deadband_deg() -> float:
    """Drift magnitude (deg) below which no correction step fires.

    Default **3.0°** — a compromise between responsiveness (catch
    drifts before they're visible to the operator: steady-state
    per-leg drift on the reference chassis is ±1–2°, so 3.0° is
    well above noise) and not chasing noise.

    Paired with the **target-zero** step-duration formula (aim to
    land at zero drift, not at half-deadband short of zero) the
    minimum auto-computed step duration at deadband-edge is now
    3.0° / 140 dps = 0.021 s — comfortably above the 0.015 s floor,
    so every correction that DOES fire is a single decisive kick
    that overcomes chassis stiction. Slight overshoot past zero is
    bounded by the deadband (next sample exits naturally).

    Historical defaults: legacy in-motion 1.5°; this knob shipped at
    2.5° (chased noise); then 3.5° (catch the wobble at fag-end);
    now 3.0° + zero-target formula.

    The legacy ``NINA_HOVER_IMU_CORR_DEADBAND_DEG`` (1.5°) is **not**
    changed by this knob — backward and the in-motion correction
    keep their finer deadband. Clamped to ``[0.1, 30.0]``.
    """
    try:
        return max(
            0.1,
            min(
                30.0,
                float(
                    os.environ.get("NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG", "3.0")
                ),
            ),
        )
    except ValueError:
        return 3.0


def _straight_bench_cycles() -> int:
    """Forward-leg cycle count used to size the Straight bench watchdog.

    The drift-corrected forward loop has no natural upper bound — it
    cycles for as long as the operator holds the button. The bench
    "Straight forward" command still needs a max-duration watchdog,
    so we cap it at ``NINA_HOVER_STRAIGHT_BENCH_CYCLES``
    (default **12**) × ``(leg + brake_settle)``. Clamped to
    ``[1, 60]``.
    """
    try:
        return max(
            1,
            min(
                60,
                int(os.environ.get("NINA_HOVER_STRAIGHT_BENCH_CYCLES", "12")),
            ),
        )
    except ValueError:
        return 12


def _straight_abort_drift_deg() -> float:
    """Drift magnitude (deg) above which the straight loop halts + speaks.

    When the post-leg drift sample crosses this threshold, the loop
    brakes the chassis, queues the bundled ``cant_move.mp3`` alert via
    :func:`nina.services.sensor_alert_audio.maybe_speak_cant_move_alert`,
    sets the pulse halt event, and exits. The operator must issue a
    fresh drive command to resume. Default 90° — the bot can't
    realistically come back from a half-rotation accumulated in one
    1 s leg, and continuing would only spin further. Set 0 to disable
    the abort. Clamped to ``[0.0, 180.0]``.
    """
    try:
        return max(
            0.0,
            min(
                180.0,
                float(os.environ.get("NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG", "90.0")),
            ),
        )
    except ValueError:
        return 90.0


def _imu_corr_abort_drift_deg() -> float:
    """Drift magnitude (deg) above which a pulse leg aborts with a spoken alert.

    Read from ``NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG``. Default 30.0 — well
    past the largest drift the iterative realign has been observed to
    recover from on the production chassis (~26°), but small enough to
    catch a runaway spin within one or two correction cycles. Set to
    ``0`` (or any non-positive value) to disable the abort entirely and
    fall back to the legacy "let the realign keep retrying" behaviour.

    When the abort fires, ``_imu_corrective_hold`` brakes, sets the pulse
    halt event so the pulse loop exits cleanly, and queues a single
    :data:`_IMU_CORR_ABORT_PHRASE` TTS announcement via
    :func:`nina.services.sensor_alert_audio.maybe_speak_cant_move_alert` (which
    enforces its own per-message cooldown so back-to-back aborts in the
    same leg do not chatter). The operator must issue a fresh drive
    command to resume — there is no automatic retry.

    Clamped to ``[0.0, 180.0]``.
    """
    try:
        return max(
            0.0,
            min(
                180.0,
                float(
                    os.environ.get("NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG", "30.0")
                ),
            ),
        )
    except ValueError:
        return 30.0


def _hover_turn_slow_wheel_pct() -> int:
    """Backward-side lean for held D-pad pivots (1–100, default 8)."""
    try:
        return max(1, min(100, int(os.environ.get("NINA_HOVER_TURN_SLOW_WHEEL_PCT", "8"))))
    except ValueError:
        return 8


def _drive_turn_micro_step_deg() -> float:
    """Yaw budget (deg) for one held D-pad L/R step or one Turn left/right click."""
    deg_raw = (os.environ.get("NINA_DRIVE_TURN_PIVOT_DEG") or "15").strip()
    if deg_raw:
        try:
            return max(1.0, min(45.0, float(deg_raw)))
        except ValueError:
            pass
    return 15.0


def _timed_turn_pivot_blend_pct() -> int:
    """Timed ``turn_left`` / ``turn_right`` blend % — always full pivot."""
    return _PIVOT_MICROSTEP_BLEND_PCT


# ----------------------------------------------------------------------
# Closed-loop 90° turn (Drive screen "Turn left" / "Turn right" buttons)
#
# These knobs configure :meth:`HoverboardAxisDrive.pulse_turn_90`, which
# reuses the iterative proportional micro-step algorithm from
# :meth:`_perform_pivot_correction` — same per-step geometry
# (``_goals_for_wheels`` with opposite leans), same per-step duration
# formula (``rotation_target / step_rate_dps`` clamped to
# ``[step_min, step_dur_cap]``), same brake-between-steps for clean IMU
# re-sampling — but DRIVEN BY A YAW BUDGET (target ±90°) rather than
# straight-line drift correction.
#
# Defaults are intentionally close to the forward straight-leg correction
# values so a fresh deploy inherits the tuning the operator has already
# validated for forward motion; only knobs that are genuinely
# turn-specific (target deg, step cap headroom, settle dwells, progress
# / fallback) get their own env name.
# ----------------------------------------------------------------------


def _imu_turn_target_deg() -> float:
    """Yaw budget (deg) the closed-loop turn must traverse.

    Default 90.0 — the operator-facing "Turn left" / "Turn right" buttons
    are documented as 90° turns. Override only for special calibration
    runs (e.g. 45° staircase profiles); clamped to ``[5.0, 180.0]`` so a
    typo can't request more than half a full rotation.
    """
    try:
        return max(
            5.0,
            min(180.0, float(os.environ.get("NINA_HOVER_TURN_TARGET_DEG", "90.0"))),
        )
    except ValueError:
        return 90.0


def _imu_turn_max_steps() -> int:
    """Hard cap on micro-steps inside one closed-loop 90° turn.

    A 90° target at the default 30 deg/s step rate × 0.18 s cap rotates
    up to ~5.4°/step at saturation, so ~17 saturated steps would cover
    90° before any deceleration / fine-tuning at the end. Default 40
    leaves comfortable headroom for proportional slow-down near the
    target plus a few extra for noisy IMU samples or a sticky chassis.
    """
    try:
        return max(
            1,
            min(200, int(float(os.environ.get("NINA_HOVER_TURN_MAX_STEPS", "40")))),
        )
    except ValueError:
        return 40


def _imu_turn_step_rate_deg_per_sec() -> float:
    """Empirical chassis rotation rate (deg/s) for closed-loop turns.

    Default **22 dps** (gentler than in-motion IMU correction's 30 dps).
    Override with ``NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC``.
    """
    raw = (os.environ.get("NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC") or "").strip()
    if raw:
        try:
            return max(1.0, min(360.0, float(raw)))
        except ValueError:
            pass
    return 22.0


def _turn_step_dur_cap_sec() -> float:
    """Per-micro-step lean hold cap for D-pad / Turn closed-loop pivots.

    Default **0.26 s** (vs ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC`` 0.18 s for
    in-motion correction). Override ``NINA_HOVER_TURN_STEP_DUR_CAP_SEC``.
    """
    try:
        return max(
            0.02,
            min(3.0, float(os.environ.get("NINA_HOVER_TURN_STEP_DUR_CAP_SEC", "0.26"))),
        )
    except ValueError:
        return 0.26


def _turn_step_min_sec() -> float:
    """Floor on per-step lean hold for closed-loop turns. Default **0.10 s**."""
    try:
        return max(
            0.01,
            min(1.0, float(os.environ.get("NINA_HOVER_TURN_STEP_MIN_SEC", "0.10"))),
        )
    except ValueError:
        return 0.10


def _turn_step_settle_sec() -> float:
    """Brake dwell between closed-loop turn micro-steps. Default **0.12 s**."""
    try:
        return max(
            0.0,
            min(1.0, float(os.environ.get("NINA_HOVER_TURN_STEP_SETTLE_SEC", "0.12"))),
        )
    except ValueError:
        return 0.12


def _imu_turn_step_blend_pct() -> int:
    """Closed-loop 90° turn micro-step blend % — always full pivot."""
    return _PIVOT_MICROSTEP_BLEND_PCT


def _held_dpad_pivot_blend_pct() -> int:
    """Blend % for held D-pad L/R micro-steps (``pulse_turn_micro_step``)."""
    return _HELD_DPAD_PIVOT_BLEND_PCT


def _imu_turn_pre_settle_sec() -> float:
    """Brake dwell BEFORE the first micro-step fires.

    Lets any forward / backward pulse leg we just halted bleed off
    chassis momentum so the integrator anchors a stable zero. Default
    0.28 s — long enough for the MX-28 to land on brake before the first
    lean step without feeling sluggish on held D-pad turns.
    """
    try:
        return max(
            0.0,
            min(2.0, float(os.environ.get("NINA_HOVER_TURN_PRE_SETTLE_SEC", "0.28"))),
        )
    except ValueError:
        return 0.28


def _imu_turn_post_settle_sec() -> float:
    """Brake dwell AFTER the last micro-step fires.

    Holds the bot stationary after reaching the target so the chassis
    doesn't keep rotating from residual lean momentum (the lean servos
    are commanded to brake but the wheels coast a touch). Default
    0.40 s.
    """
    try:
        return max(
            0.0,
            min(2.0, float(os.environ.get("NINA_HOVER_TURN_POST_SETTLE_SEC", "0.40"))),
        )
    except ValueError:
        return 0.40


def _imu_turn_progress_check_steps() -> int:
    """Steps to run before the closed-loop turn checks for progress.

    Same idea as :func:`_imu_corr_progress_check_steps` but tuned for
    the much larger angular budget (90° vs ~5°): default 6 lets the
    realign do real work before bailing on "no progress" exits. Set 0
    to disable the check entirely.
    """
    try:
        return max(
            0,
            min(50, int(float(os.environ.get("NINA_HOVER_TURN_PROGRESS_CHECK_STEPS", "6")))),
        )
    except ValueError:
        return 6


def _imu_turn_progress_min_deg() -> float:
    """Minimum |yaw| advance (deg) required by ``PROGRESS_CHECK_STEPS``.

    Default 3.0° — at the saturated 5.4°/step rotation rate this is half
    a step's worth; if the chassis hasn't moved that much in 6 steps the
    pivots aren't actually rotating it (stuck wheel, no torque, etc.).
    """
    try:
        return max(
            0.0,
            min(45.0, float(os.environ.get("NINA_HOVER_TURN_PROGRESS_MIN_DEG", "3.0"))),
        )
    except ValueError:
        return 3.0


def _imu_turn_swap_pivot_dir() -> bool:
    """Swap the pivot-direction-to-goals mapping in the closed-loop 90° turn.

    Same chassis-physics fix as :func:`_straight_corr_swap_pivot_dir`,
    but scoped to :meth:`HoverboardAxisDrive.pulse_turn_90` and the
    timed :meth:`HoverboardAxisDrive.turn_left` /
    :meth:`HoverboardAxisDrive.turn_right` fallback. On the reference
    chassis, commanding what the code labels "pivot LEFT" (L=FWD,
    R=BACK at the dynamixel-lean level) physically rotates the
    chassis to the **RIGHT** (CW), not the left as the label
    suggests. The 90° turn is in-place, so the same mapping question
    applies — without the swap, pressing "Turn Left" on the operator
    UI commands ``L=FWD, R=BACK`` and the chassis rotates right
    (then the no-progress safeguard bails the turn).

    When True, a turn-LEFT request applies ``L=BACK, R=FWD`` goals and a
    turn-RIGHT request applies ``L=FWD, R=BACK`` goals — i.e. the
    label-to-goals mapping is inverted relative to the conventional
    convention. Default **off** after the fleet hall FWD/REV swap;
    set ``NINA_HOVER_TURN_SWAP_PIVOT_DIR=1`` if timed / closed-loop
    turns still rotate the wrong way.

    Independent of :func:`_straight_corr_swap_pivot_dir` so the
    operator can tune turn vs standstill correction separately;
    defaults match because the same chassis-physics fix applies to
    both.

    Unrelated to ``NINA_HOVER_IMU_CORR_INVERT_SIGN`` — that knob
    flips the **integrator sign interpretation** (whether positive
    yaw means CW or CCW). This knob flips the **pivot command
    mapping** (whether a chassis-frame "right" turn uses one set of
    goals or the other). On chassis with both inversions you'd set
    both.
    """
    val = os.environ.get("NINA_HOVER_TURN_SWAP_PIVOT_DIR", "0")
    return val.strip().lower() not in ("0", "false", "no", "off", "")


def estimate_forward_pulse_series_duration_sec(axis: HoverboardAxisSettings) -> float:
    """Bench watchdog upper-bound for the drift-corrected forward loop.

    The new :meth:`HoverboardAxisDrive.start_pulse_straight_forward`
    has no fixed series count — it cycles forward-leg → brake → drift
    check until the operator releases the button (or the 90° abort
    fires). For bench tests we still need a finite watchdog, so this
    helper returns::

        prime_timeout + bench_cycles × (leg_sec + brake_settle_sec)

    using the new ``NINA_HOVER_STRAIGHT_*`` env getters. Increase
    ``NINA_HOVER_STRAIGHT_BENCH_CYCLES`` (default 12) for a longer
    run, or set ``NINA_STRAIGHT_TEST_MS`` to bypass this estimate
    entirely from the bench UI.

    The ``axis`` argument is unused (kept for signature compatibility
    with :func:`estimate_backward_pulse_series_duration_sec`, which
    now uses the same drift-corrected algorithm and the same formula).
    """
    _ = axis  # reserved for compatibility
    cycles = _straight_bench_cycles()
    leg = _straight_leg_sec()
    settle = _straight_brake_settle_sec()
    prime = _straight_prime_timeout_sec()
    return prime + float(cycles) * (leg + settle)


def estimate_backward_pulse_series_duration_sec(axis: HoverboardAxisSettings) -> float:
    """Upper-bound seconds for ``start_pulse_straight_backward`` until the loop exits.

    The backward path now uses the **exact same drift-corrected
    algorithm** as forward (cumulative IMU tracking, drive-then-check
    cycles with at-standstill micro-step pivot correction), so the
    duration model is identical to
    :func:`estimate_forward_pulse_series_duration_sec`. The ``axis``
    argument is kept for signature compatibility but is no longer
    consulted.
    """
    _ = axis  # reserved for compatibility
    cycles = _straight_bench_cycles()
    leg = _straight_leg_sec()
    settle = _straight_brake_settle_sec()
    prime = _straight_prime_timeout_sec()
    return prime + float(cycles) * (leg + settle)


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
        # Held D-pad L/R and single-click Turn buttons share one IMU session.
        self._hold_turn_session_dir: Optional[str] = None
        self._hold_turn_yaw_current: float = 0.0

        # IMU yaw-correction hooks (wired from NinaService when the MPU-9250
        # monitor is enabled). All three may be None on dev hosts without IMU.
        self._imu_yaw_drift_fn: Optional[ImuYawDriftFn] = None
        self._imu_yaw_rate_fn: Optional[ImuYawRateFn] = None
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
        self._imu_corr_invert_sign: bool = _imu_corr_invert_sign()
        self._imu_corr_step_settle_sec: float = _imu_corr_step_settle_sec()
        self._imu_corr_max_steps: int = _imu_corr_max_steps()
        self._imu_corr_step_rate_dps: float = _imu_corr_step_rate_deg_per_sec()
        self._imu_corr_step_min_sec: float = _imu_corr_step_min_sec()
        self._imu_corr_progress_check_steps: int = _imu_corr_progress_check_steps()
        self._imu_corr_progress_min_deg: float = _imu_corr_progress_min_deg()
        self._imu_corr_bail_warmup_steps: int = _imu_corr_bail_warmup_steps()
        self._imu_corr_bail_margin_deg: float = _imu_corr_bail_margin_deg()
        self._imu_corr_poll_sec: float = 1.0 / _imu_corr_poll_hz()
        # Backward-direction overrides. THRESHOLD and STEP_RATE have their
        # own backward-optimised defaults (baked in after field testing);
        # COOLDOWN still inherits from forward because the forward 1.0 s
        # default works fine for backward too. All three can still be
        # overridden via NINA_HOVER_IMU_CORR_BACK_* env vars.
        self._imu_corr_back_threshold_deg: float = _imu_corr_back_threshold_deg(
            _IMU_CORR_BACK_DEFAULT_THRESHOLD_DEG
        )
        self._imu_corr_back_cooldown_sec: float = _imu_corr_back_cooldown_sec(
            self._imu_corr_cooldown_sec
        )
        self._imu_corr_back_step_rate_dps: float = (
            _imu_corr_back_step_rate_deg_per_sec(
                _IMU_CORR_BACK_DEFAULT_STEP_RATE_DPS
            )
        )
        # Stuck detector: counts consecutive "no progress" realign exits
        # so a long ``stuck_cooldown_sec`` can be applied once the count
        # reaches ``stuck_streak`` (typically the chassis is stationary
        # and the pivots aren't rotating it; the longer cooldown gives
        # the pulse loop a clean window to build wheel momentum).
        self._imu_corr_stuck_streak_max: int = _imu_corr_stuck_streak()
        self._imu_corr_stuck_cooldown_sec: float = _imu_corr_stuck_cooldown_sec()
        self._imu_corr_no_progress_streak: int = 0
        # Drift-abort: catastrophic drift past this magnitude halts the pulse
        # series and announces via TTS. 0 disables.
        self._imu_corr_abort_drift_deg: float = _imu_corr_abort_drift_deg()
        # Earliest monotonic time at which the next IMU sample is allowed.
        # Set by every pivot to ``now + cooldown_sec`` so back-to-back
        # corrections can't fire. Reset to 0 by ``_imu_begin_straight`` so a
        # fresh straight leg can sample immediately.
        self._imu_corr_next_sample_at: float = 0.0

    # ------------------------------------------------------------------
    # IMU yaw-correction hooks
    # ------------------------------------------------------------------
    def set_imu_hooks(
        self,
        *,
        yaw_drift_fn: Optional[ImuYawDriftFn],
        begin_straight_fn: Optional[ImuStraightHook] = None,
        end_straight_fn: Optional[ImuStraightHook] = None,
        yaw_rate_fn: Optional[ImuYawRateFn] = None,
    ) -> None:
        """Wire the MPU-9250 (or any) yaw sampler into the straight pulses.

        *yaw_drift_fn* should return signed degrees (positive = bot drifted to the
        right) or ``None`` while the integrator is paused / calibrating. The
        optional begin / end hooks are invoked when the drive enters / leaves a
        straight leg (forward or backward pulse series, plus symmetric straight
        ``set_wheels``) so the integrator can be reset and stopped without UI
        plumbing.

        *yaw_rate_fn* (optional) returns the instantaneous yaw rate in deg/s
        (sign convention matches ``yaw_drift_fn``), or ``None`` when the gyro
        is unavailable. The new forward straight loop uses it as an
        **active settle** — after braking, the loop polls the rate until the
        chassis is actually still (``|rate| < SETTLE_RATE_DPS`` for
        ``SETTLE_STABLE_SEC`` continuous) before sampling drift, so drift
        corrections never run while the chassis is still rotating from the
        previous forward leg. When ``yaw_rate_fn`` is not wired, the loop
        falls back to the fixed ``brake_settle`` timer for backwards
        compatibility.

        Correction is **discrete**: the pulse loop polls the sampler at
        ``NINA_HOVER_IMU_CORR_POLL_HZ`` and, when drift crosses the threshold,
        brakes, pivots in place until drift returns inside the deadband (or the
        per-pivot cap elapses), brakes again, re-primes the lean stack, and only
        then lets the primed forward / back pulse resume.
        """
        self._imu_yaw_drift_fn = yaw_drift_fn
        self._imu_yaw_rate_fn = yaw_rate_fn
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
        self._imu_corr_invert_sign = _imu_corr_invert_sign()
        self._imu_corr_step_settle_sec = _imu_corr_step_settle_sec()
        self._imu_corr_max_steps = _imu_corr_max_steps()
        self._imu_corr_step_rate_dps = _imu_corr_step_rate_deg_per_sec()
        self._imu_corr_step_min_sec = _imu_corr_step_min_sec()
        self._imu_corr_progress_check_steps = _imu_corr_progress_check_steps()
        self._imu_corr_progress_min_deg = _imu_corr_progress_min_deg()
        self._imu_corr_bail_warmup_steps = _imu_corr_bail_warmup_steps()
        self._imu_corr_bail_margin_deg = _imu_corr_bail_margin_deg()
        self._imu_corr_poll_sec = 1.0 / _imu_corr_poll_hz()
        self._imu_corr_back_threshold_deg = _imu_corr_back_threshold_deg(
            _IMU_CORR_BACK_DEFAULT_THRESHOLD_DEG
        )
        self._imu_corr_back_cooldown_sec = _imu_corr_back_cooldown_sec(
            self._imu_corr_cooldown_sec
        )
        self._imu_corr_back_step_rate_dps = _imu_corr_back_step_rate_deg_per_sec(
            _IMU_CORR_BACK_DEFAULT_STEP_RATE_DPS
        )
        self._imu_corr_stuck_streak_max = _imu_corr_stuck_streak()
        self._imu_corr_stuck_cooldown_sec = _imu_corr_stuck_cooldown_sec()
        self._imu_corr_no_progress_streak = 0
        self._imu_corr_abort_drift_deg = _imu_corr_abort_drift_deg()
        # Allow the first sample of the next straight leg to fire immediately.
        self._imu_corr_next_sample_at = 0.0
        # Show the backward overrides only when they actually differ from
        # forward, so the banner stays compact on chassis that don't need
        # direction-specific tuning.
        back_threshold_tag = (
            ""
            if self._imu_corr_back_threshold_deg == self._imu_corr_threshold_deg
            else f" back_threshold={self._imu_corr_back_threshold_deg:.2f}"
        )
        back_cooldown_tag = (
            ""
            if self._imu_corr_back_cooldown_sec == self._imu_corr_cooldown_sec
            else f" back_cooldown={self._imu_corr_back_cooldown_sec:.2f}s"
        )
        back_step_rate_tag = (
            ""
            if self._imu_corr_back_step_rate_dps == self._imu_corr_step_rate_dps
            else f" back_step_rate={self._imu_corr_back_step_rate_dps:.1f}dps"
        )
        stuck_tag = (
            ""
            if self._imu_corr_stuck_streak_max <= 0
            else (
                f" stuck_streak={self._imu_corr_stuck_streak_max}"
                f" stuck_cooldown={self._imu_corr_stuck_cooldown_sec:.2f}s"
            )
        )
        abort_tag = (
            ""
            if self._imu_corr_abort_drift_deg <= 0
            else f" abort_drift={self._imu_corr_abort_drift_deg:.2f} deg"
        )
        log.info(
            "hoverboard IMU hooks: drift=%s begin=%s end=%s enabled=%s "
            "threshold=%.2f deg deadband=%.2f deg step_blend=%s%% "
            "step_dur=%.2fs step_min=%.2fs step_rate=%.1fdps "
            "step_settle=%.2fs max_steps=%d "
            "progress_check=%d progress_min=%.2f deg "
            "bail_warmup=%d bail_margin=%.2f deg "
            "outer_settle=%.2fs cooldown=%.2fs poll=%.2fHz invert_sign=%s"
            "%s%s%s%s%s "
            "(iterative proportional micro-step realign)",
            "set" if yaw_drift_fn else "off",
            "set" if begin_straight_fn else "off",
            "set" if end_straight_fn else "off",
            self._imu_corr_enabled_static,
            self._imu_corr_threshold_deg,
            self._imu_corr_deadband_deg,
            self._imu_corr_pivot_blend_pct,
            self._imu_corr_pivot_max_sec,
            self._imu_corr_step_min_sec,
            self._imu_corr_step_rate_dps,
            self._imu_corr_step_settle_sec,
            self._imu_corr_max_steps,
            self._imu_corr_progress_check_steps,
            self._imu_corr_progress_min_deg,
            self._imu_corr_bail_warmup_steps,
            self._imu_corr_bail_margin_deg,
            self._imu_corr_settle_sec,
            self._imu_corr_cooldown_sec,
            _imu_corr_poll_hz(),
            self._imu_corr_invert_sign,
            back_threshold_tag,
            back_cooldown_tag,
            back_step_rate_tag,
            stuck_tag,
            abort_tag,
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
        # ``is_forward`` is not used to alter pivot geometry (pivot direction
        # comes from the SIGN of the drift sample, which is in the world frame
        # and therefore the same regardless of whether the chassis was moving
        # forward or backward). It is only used to tag log lines so the
        # operator can tell from a single ``grep`` whether a given correction
        # fired during forward-primed or backward-primed motion.
        direction_tag = "forward" if is_forward else "backward"
        if duration_sec <= 0.0:
            return False
        if self._imu_yaw_drift_fn is None or not self._imu_corr_enabled_static:
            return halt.wait(timeout=duration_sec)
        step = max(0.01, float(self._imu_corr_poll_sec))
        end = time.monotonic() + duration_sec
        # Direction-aware threshold + cooldown so operators can tune the
        # backward leg more aggressively without affecting forward. When
        # the BACK_* env vars are unset both attrs hold the same value, so
        # this is a no-op for chassis that haven't opted in.
        threshold = (
            self._imu_corr_threshold_deg
            if is_forward
            else self._imu_corr_back_threshold_deg
        )
        cooldown_sec = (
            self._imu_corr_cooldown_sec
            if is_forward
            else self._imu_corr_back_cooldown_sec
        )
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
                # Drift-abort: when the magnitude exceeds the configured
                # abort threshold the iterative realign has no realistic
                # chance of recovering (the largest drift it has ever been
                # observed to close out on this chassis is ~26°). Brake,
                # halt the pulse series, and tell the operator via TTS
                # rather than burning more pulse cycles producing snake
                # motion. A non-positive ``abort_drift_deg`` (i.e. 0)
                # disables the abort and falls back to the legacy
                # "keep retrying" behaviour.
                if (
                    yaw is not None
                    and self._imu_corr_abort_drift_deg > 0
                    and abs(yaw) >= self._imu_corr_abort_drift_deg
                ):
                    log.warning(
                        "hover IMU correction (%s): drift=%+.2f deg >= "
                        "%.2f deg ABORT threshold -> halting pulse series "
                        "and announcing (operator must issue a fresh "
                        "drive command to resume)",
                        direction_tag,
                        yaw,
                        self._imu_corr_abort_drift_deg,
                    )
                    try:
                        self._apply_goals(
                            {
                                self._left_id: self._brake_left,
                                self._right_id: self._brake_right,
                            }
                        )
                    except Exception:
                        log.debug(
                            "drift abort: brake apply failed", exc_info=True
                        )
                    try:
                        maybe_speak_cant_move_alert()
                    except Exception:
                        log.debug(
                            "drift abort: bldc speech alert failed",
                            exc_info=True,
                        )
                    halt.set()
                    return True
                if yaw is not None and abs(yaw) >= threshold:
                    # Drift exceeded — pause the primed motion, pivot-correct,
                    # re-prime, then enforce the cooldown before next sample.
                    if self._perform_pivot_correction(
                        yaw, halt, is_forward=is_forward
                    ):
                        return True
                    try:
                        self._apply_goals(base_goals)
                    except Exception:
                        pass
                    # If the realign has been failing to make progress for
                    # ``stuck_streak`` events in a row, the pivots are not
                    # rotating the chassis (typically because the bot is
                    # stationary and friction is holding it). The regular
                    # 0.3-1.0 s cooldown isn't enough time for the pulse
                    # loop to build the wheel momentum needed to break
                    # the chassis free, so apply a much longer cooldown
                    # to give the primed-only motion a clean window. The
                    # streak resets on the first successful realign (any
                    # non-no-progress exit) so we automatically return to
                    # the regular cooldown as soon as the bot is moving
                    # again.
                    if (
                        self._imu_corr_stuck_streak_max > 0
                        and self._imu_corr_no_progress_streak
                        >= self._imu_corr_stuck_streak_max
                    ):
                        effective_cooldown = self._imu_corr_stuck_cooldown_sec
                        log.warning(
                            "hover IMU correction (%s): stuck detected "
                            "(%d consecutive no-progress events) — "
                            "extending cooldown to %.2fs to let the bot "
                            "regain motion",
                            direction_tag,
                            self._imu_corr_no_progress_streak,
                            effective_cooldown,
                        )
                    else:
                        effective_cooldown = cooldown_sec
                        log.info(
                            "hover IMU correction (%s): cooldown %.2fs "
                            "(primed-only motion)",
                            direction_tag,
                            effective_cooldown,
                        )
                    self._imu_corr_next_sample_at = (
                        time.monotonic() + effective_cooldown
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
        *,
        is_forward: bool = True,
    ) -> bool:
        """Brake → iterative PROPORTIONAL micro-step realign → brake → re-prime.

        The correction is **no longer a single pivot** — it's a closed-loop
        chain of small pivots whose individual duration is **proportional
        to the remaining drift** (clamped to
        ``[STEP_MIN_SEC, PIVOT_MAX_SEC]``). Big drifts use full-duration
        pulses to close in fast; small drifts use brief pulses so we don't
        blow past zero on the final step.

        Each iteration:

            1. Sample current drift (with the configured ``invert_sign`` flip).
            2. If ``|drift| <= deadband`` we're done.
            3. Re-evaluate pivot direction from the *latest* sample (not the
               original ``drift_deg``) so a slight overshoot in step N flips
               direction for step N+1 instead of compounding.
            4. Apply a pivot lean (``step_blend`` × ``this_step_dur``) where
               ``this_step_dur = clamp(|last_yaw| / step_rate_dps,
               [STEP_MIN_SEC, PIVOT_MAX_SEC])`` aiming to land ~½ deadband
               short of zero.
            5. Brake-settle ``step_settle_sec`` so the integrator re-reads
               on a still bot.

        Bails on three failure modes:
        - ``|drift|`` grows past ``|initial_drift| + deadband`` for two
          consecutive samples (sign-mismatch — toggle ``INVERT_SIGN``).
        - Drift crosses zero past deadband (overshoot — stop chasing).
        - ``max_steps`` iterations reached (safety cap, ~25° head-room at
          the calm defaults).

        Returns ``True`` if the pulse halt event fires during the routine.
        Sign convention is unchanged: positive *drift_deg* = bot drifted
        right under the default (``NINA_HOVER_IMU_CORR_INVERT_SIGN=0``)
        convention, negate drift before direction lookup when set to 1.

        ``is_forward`` is informational only — it does NOT change the pivot
        geometry (drift is measured in the world frame, so the correction
        command is identical for forward-primed and backward-primed motion).
        It is threaded through purely so every log line emitted by this
        routine is tagged ``(forward)`` or ``(backward)``, letting the
        operator confirm via ``grep`` that backward corrections actually
        fire (and how often) without having to cross-reference timestamps
        against the pulse-series banner.
        """
        direction_tag = "forward" if is_forward else "backward"
        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        invert = bool(self._imu_corr_invert_sign)
        deadband = self._imu_corr_deadband_deg
        # Same lean pacing as held D-pad L/R / closed-loop turns.
        step_blend = _held_dpad_pivot_blend_pct()
        step_dur_cap = _turn_step_dur_cap_sec()
        step_min_sec = _turn_step_min_sec()
        step_rate_dps = _imu_turn_step_rate_deg_per_sec()
        step_settle = _turn_step_settle_sec()
        pre_settle = _imu_turn_pre_settle_sec()
        post_settle = _imu_turn_post_settle_sec()
        max_steps = max(1, int(self._imu_corr_max_steps))

        warmup_steps = max(0, int(self._imu_corr_bail_warmup_steps))
        bail_margin = max(0.0, float(self._imu_corr_bail_margin_deg))
        progress_check_step = max(0, int(self._imu_corr_progress_check_steps))
        progress_min_deg = max(0.0, float(self._imu_corr_progress_min_deg))
        effective_threshold = (
            self._imu_corr_threshold_deg
            if is_forward
            else self._imu_corr_back_threshold_deg
        )

        log.info(
            "hover IMU correction (%s): drift=%+.2f deg (invert=%s) >= "
            "%.2f deg threshold -> brake + iterative realign "
            "(step_blend=%s%%, step_dur_cap=%.2fs, step_min=%.2fs, "
            "step_rate=%.1fdps, step_settle=%.2fs, "
            "max_steps=%d, progress_check=%d, progress_min=%.2f, "
            "bail_warmup=%d, bail_margin=%.2f, deadband=%.2f deg)",
            direction_tag,
            drift_deg,
            invert,
            effective_threshold,
            step_blend,
            step_dur_cap,
            step_min_sec,
            step_rate_dps,
            step_settle,
            max_steps,
            progress_check_step,
            progress_min_deg,
            warmup_steps,
            bail_margin,
            deadband,
        )

        # 1. Brake to stop the primed forward/back motion before chasing zero.
        try:
            self._apply_goals(brake_goals)
        except Exception:
            pass
        if halt.wait(timeout=pre_settle):
            return True

        # 2. Iterative micro-step realign. The direction is re-decided every
        #    iteration from the latest IMU sample so over-shoots correct
        #    themselves on the next step instead of compounding.
        #
        #    Bail logic:
        #    - For the first ``warmup_steps`` samples the bot is still bleeding
        #      off angular momentum from the pre-brake forward lean. We don't
        #      let the wrong-direction bail fire during this window — early
        #      drift growth here is momentum, not a sign mismatch.
        #    - After warmup, record ``yaw_after_warmup`` and only bail if drift
        #      has grown by MORE than ``bail_margin_deg`` AND is still growing
        #      for two consecutive samples.
        last_yaw = drift_deg
        yaw_after_warmup: Optional[float] = None
        growing_streak = 0
        steps_taken = 0
        exit_reason = "max-steps cap"
        for step in range(max_steps):
            steps_taken = step + 1
            if halt.is_set():
                return True

            yaw_now = self._imu_sample_drift_deg()
            if yaw_now is not None:
                last_yaw = yaw_now
                if abs(yaw_now) <= deadband:
                    exit_reason = "deadband reached"
                    break
                # Over-correction guard: drift crossed zero past the deadband.
                if drift_deg * yaw_now < 0 and abs(yaw_now) >= deadband:
                    exit_reason = "over-shot zero"
                    break
                if step < warmup_steps:
                    # Still bleeding off pre-brake momentum — don't allow the
                    # bail to fire on this sample.
                    growing_streak = 0
                elif drift_deg * yaw_now <= 0.0:
                    # Drift has crossed zero (or sits exactly on it). That's
                    # over-correction, not wrong-direction — the overshoot
                    # guard above handles the "past deadband" case; here we
                    # just reset the streak so a clean overshoot+settle
                    # sequence can't trip the wrong-direction bail.
                    growing_streak = 0
                else:
                    # Same sign as the original drift → still in the original
                    # direction. Pin the baseline on the first post-warmup
                    # SAME-SIGN sample, then watch for monotonic growth past
                    # the margin (two consecutive samples) to call it a wrong
                    # pivot direction.
                    if yaw_after_warmup is None:
                        yaw_after_warmup = yaw_now
                    elif (
                        bail_margin > 0.0
                        and abs(yaw_now) > abs(yaw_after_warmup) + bail_margin
                    ):
                        growing_streak += 1
                        if growing_streak >= 2:
                            log.warning(
                                "hover IMU correction (%s): drift grew by "
                                ">%.2f deg across 2 post-warmup samples with "
                                "the same sign as the initial drift "
                                "(|%+.2f| > |%+.2f| + %.2f) — bailing. Try "
                                "toggling NINA_HOVER_IMU_CORR_INVERT_SIGN on "
                                "this chassis.",
                                direction_tag,
                                bail_margin,
                                yaw_now,
                                yaw_after_warmup,
                                bail_margin,
                            )
                            exit_reason = "wrong-direction bail"
                            break
                    else:
                        growing_streak = 0

                # No-progress safeguard: if we've spent enough effort and
                # ``|drift|`` hasn't dropped by ``progress_min_deg`` from
                # ``drift_deg`` (the value that triggered the realign),
                # exit cleanly. Natural drift rate has temporarily exceeded
                # our per-step correction; the next event will get a fresh
                # chance after the cooldown rather than us burning
                # ``MAX_STEPS`` worth of ineffective pulses.
                if (
                    progress_check_step > 0
                    and step >= progress_check_step
                ):
                    improvement = abs(drift_deg) - abs(yaw_now)
                    if improvement < progress_min_deg:
                        log.info(
                            "hover IMU correction (%s): realign making no "
                            "progress (start=%+.2f, now=%+.2f, "
                            "improvement=%+.2f deg after %d steps, "
                            "threshold=%.2f deg) — exiting to let the next "
                            "event try fresh",
                            direction_tag,
                            drift_deg,
                            yaw_now,
                            improvement,
                            step + 1,
                            progress_min_deg,
                        )
                        exit_reason = "no progress"
                        break

            # Same blend / step timing as held D-pad; drift sign picks direction.
            effective = -last_yaw if invert else last_yaw
            pivot_left = effective > 0.0
            if pivot_left:
                step_goals = self._goals_for_wheels(
                    left_dir=self.DIR_FORWARD,
                    left_speed=step_blend,
                    right_dir=self.DIR_BACKWARD,
                    right_speed=step_blend,
                )
            else:
                step_goals = self._goals_for_wheels(
                    left_dir=self.DIR_BACKWARD,
                    left_speed=step_blend,
                    right_dir=self.DIR_FORWARD,
                    right_speed=step_blend,
                )
            # Proportional step duration: target rotating ``|last_yaw|`` deg
            # at the calibrated ``step_rate_dps`` rotation rate, clamped to
            # [step_min_sec, step_dur_cap]. Small drifts get short pulses
            # so we don't blow past zero; big drifts use the full cap.
            # Aim to land ~0.5 deg short of zero so the next sample exits
            # cleanly via the deadband rather than always over-shooting.
            target_rotation_deg = max(0.5, abs(last_yaw) - 0.5 * deadband)
            this_step_dur = max(
                step_min_sec,
                min(step_dur_cap, target_rotation_deg / step_rate_dps),
            )
            try:
                self._apply_goals(step_goals)
            except Exception:
                pass
            if halt.wait(timeout=this_step_dur):
                return True
            # Brake between steps so the IMU re-samples on a still bot.
            try:
                self._apply_goals(brake_goals)
            except Exception:
                pass
            if step_settle > 0.0 and halt.wait(timeout=step_settle):
                return True

        # Update the stuck-detector streak. Consecutive "no progress" exits
        # are evidence that the pivots aren't actually rotating the chassis
        # (e.g. it's stationary and friction is holding the wheels). Any
        # other exit (deadband reached, overshoot past zero, wrong-direction
        # bail, max-steps cap) means the pivots ARE producing rotation, so
        # reset the streak immediately.
        if exit_reason == "no progress":
            self._imu_corr_no_progress_streak += 1
        else:
            self._imu_corr_no_progress_streak = 0

        log.info(
            "hover IMU correction (%s): realign complete after %d "
            "micro-step%s (drift now %+.2f deg, exited via %s)",
            direction_tag,
            steps_taken,
            "" if steps_taken == 1 else "s",
            last_yaw,
            exit_reason,
        )

        # 3. Brake to make sure the bot is fully stopped before re-priming.
        try:
            self._apply_goals(brake_goals)
        except Exception:
            pass
        if halt.wait(timeout=post_settle):
            return True

        # 4. Re-prime the lean stack at neutral so the next primed forward /
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
        # Reset the stuck-detector streak so a fresh leg doesn't inherit a
        # stale "stuck" state from the previous leg (the previous leg's
        # stuck condition may have been specific to its motion direction
        # or chassis pose).
        self._imu_corr_no_progress_streak = 0
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
        """Signal the straight pulse-series worker to exit and optionally join it.

        When the caller interrupts a *live* pulse-series thread, the truncated
        caller stack is logged at DEBUG level so ``NINA_LOG_LEVEL=DEBUG`` can
        re-enable the diagnostic that helped track the kiosk-vs-bench double-
        instance early-stop without spamming normal INFO operation.
        """
        t = self._pulse_series_thread
        active = t is not None and t.is_alive()
        if active and log.isEnabledFor(logging.DEBUG):
            try:
                stack = "".join(traceback.format_stack(limit=8)[:-1])
                log.debug(
                    "hover _halt_pulse_series: caller stack:\n%s",
                    stack.rstrip(),
                )
            except Exception:
                log.debug("hover _halt_pulse_series: (stack capture failed)")
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
        """Drift-corrected forward motion (Straight bench + D-pad forward).

        Replaces the legacy pulse-series + in-motion IMU correction with
        a simpler "drive-then-check" cycle:

        1. Prime the lean stack at neutral (``NINA_HOVER_STRAIGHT_PRIME_POS``).
        2. Reset the IMU integrator ONCE at the top of the entire
           motion (``_imu_begin_straight``) so drift samples report
           CUMULATIVE heading deviation from the operator-defined
           start heading, not per-leg drift.
        3. Command full forward lean for ``NINA_HOVER_STRAIGHT_LEG_SEC``
           (default **0.5 s**) using ``forward_pos_left`` /
           ``forward_pos_right`` + the ``_STRAIGHT_FWD_EXTRA_TICKS`` nudge.
        4. Brake; active-settle on the IMU yaw rate (or fall back to
           ``NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC``) so the chassis is
           truly still before sampling.
        5. Read cumulative drift from the running IMU integrator — the
           sample reflects yaw accumulated since the motion STARTED
           (not since the last leg started).
        6. If ``|drift| >= NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG``
           (default **90°**), brake, play
           :func:`nina.services.sensor_alert_audio.maybe_speak_cant_move_alert`,
           halt the thread and return.
        7. If ``|drift| > NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG``, run
           an iterative proportional micro-step correction at standstill
           that drives the cumulative integrator back inside
           ``NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG`` of zero (i.e. back
           toward the operator's start heading).
        8. Loop back to step 3 — repeat until the operator releases the
           button (which triggers ``_halt_pulse_series``) or the abort
           fires.

        Cumulative tracking (steps 2 + 5) is what bounds the bot's
        maximum heading deviation to the deadband regardless of how
        small the per-leg yaw bias is. Per-leg tracking misses small
        same-sign biases that accumulate over many cycles into a curve.

        Drift is sampled at STANDSTILL (not during motion) so the
        integrator isn't racing with active wheel rotation, which was
        the root cause of the wrong-direction realigns the in-motion
        algorithm produced. If the IMU sampler is unwired or returns
        ``None``, the loop still cycles forward legs but skips the
        correction step.

        ``speed_percent`` is preserved for API compatibility but is
        not used — the lean magnitude comes entirely from the
        operator-validated ``forward_pos_*`` tune.
        """
        if not self._is_initialized:
            return
        _ = speed_percent  # reserved; speed is encoded in the calibrated lean
        self._halt_pulse_series(wait=True)
        log.info(
            "hover forward straight: priming (goal=%s, lean before drive) ...",
            _straight_prime_goal_ticks(),
        )
        self._prime_straight_neutral()
        self._post_prime_settle("fwd")
        self._last_straight_key = "fwd"
        thr = threading.Thread(
            target=self._drift_correct_loop,
            args=(True,),
            name="nina_hover_fwd_straight",
            daemon=True,
        )
        self._pulse_series_thread = thr
        thr.start()

    def start_pulse_straight_backward(self, speed_percent: int) -> None:
        """Drift-corrected backward motion (Straight back bench + D-pad back).

        Uses the **exact same algorithm** as
        :meth:`start_pulse_straight_forward` — just with backward drive
        goals — because at-standstill micro-step pivot correction is
        direction-of-motion agnostic (in-place rotation) and the IMU
        yaw integrator is direction-agnostic too. Most
        ``NINA_HOVER_STRAIGHT_*`` tuning knobs (deadband, residual,
        blend, active settle thresholds, swap_pivot) are shared with
        the forward path; a handful are direction-specific:

        - Leg duration: ``NINA_HOVER_STRAIGHT_BACK_LEG_SEC`` (default
          0.5 s; independent from forward's
          ``NINA_HOVER_STRAIGHT_LEG_SEC``).
        - Correction step caps: ``NINA_HOVER_STRAIGHT_CORR_BACK_STEP_*``
          (softer defaults for the chassis-asymmetric backward
          pre-pivot momentum profile).
        - Per-side asymmetric trim:
          ``NINA_HOVER_STRAIGHT_BACK_LEFT_TICKS_OFFSET`` /
          ``NINA_HOVER_STRAIGHT_BACK_RIGHT_TICKS_OFFSET`` (signed raw
          tick offsets added to the calibrated ``backward_pos_*``;
          defaults +7 / +8 on the reference build to compensate for
          BLDC wheel asymmetry that yawed the chassis at the bare
          calibrated lean).

        1. Prime the lean stack at neutral
           (``NINA_HOVER_STRAIGHT_PRIME_POS``).
        2. Reset the IMU integrator ONCE
           (``_imu_begin_straight``) — drift samples track CUMULATIVE
           heading deviation from the operator-defined start heading.
        3. Loop: backward lean for
           ``NINA_HOVER_STRAIGHT_BACK_LEG_SEC`` → brake → active
           settle → sample cumulative drift → either abort (>=90°),
           correct (>deadband), or continue.
        4. Correction is identical to the forward path: iterative
           proportional micro-step in-place pivots that drive
           ``|drift|`` back inside
           ``NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG`` of zero.

        ``speed_percent`` is preserved for API compatibility but is
        not used — the lean magnitude comes entirely from the
        operator-validated ``backward_pos_*`` tune.

        """
        if not self._is_initialized:
            return
        _ = speed_percent  # reserved; speed is encoded in the calibrated lean
        self._halt_pulse_series(wait=True)
        log.info(
            "hover backward straight: priming (goal=%s, lean before drive) ...",
            _straight_prime_goal_ticks(),
        )
        self._prime_straight_neutral()
        self._post_prime_settle("back")
        self._last_straight_key = "back"
        thr = threading.Thread(
            target=self._drift_correct_loop,
            args=(False,),
            name="nina_hover_bwd_straight",
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

    def _active_settle_until_still(
        self,
        halt: threading.Event,
        *,
        context: str,
    ) -> Tuple[str, float, float]:
        """Wait until the chassis is actually still (or bail at the timeout).

        Returns a ``(status, elapsed_sec, last_rate_dps)`` tuple where
        ``status`` is one of:

        - ``"settled"``  — ``|yaw_rate|`` stayed below ``SETTLE_RATE_DPS``
          for a continuous ``SETTLE_STABLE_SEC`` window. Chassis is
          confirmed still; safe to sample drift / start a correction step.
        - ``"timeout"``  — hard cap (``SETTLE_MAX_SEC``) hit before the
          chassis settled. Caller should skip the drift sample for this
          cycle / step and try again on the next leg.
        - ``"halted"``   — ``halt`` was set during the wait (operator
          released the button). Caller should exit cleanly.
        - ``"no_rate"``  — no ``yaw_rate_fn`` was wired into the drive;
          caller should fall back to the fixed-timer settle. ``elapsed``
          is 0 and ``last_rate`` is 0 in this case.

        Polls at ``SETTLE_POLL_SEC`` (~50 Hz default) and resets the
        stable-window timer any time ``|rate|`` exceeds the threshold so
        a single noisy sample doesn't falsely declare stillness. Brief
        ``None`` samples from the rate fn (gyro temporarily unavailable)
        also reset the window without aborting — the next valid sample
        decides.
        """
        rate_fn = self._imu_yaw_rate_fn
        if rate_fn is None:
            return ("no_rate", 0.0, 0.0)
        rate_thr = _straight_settle_rate_dps()
        stable_sec = _straight_settle_stable_sec()
        max_sec = _straight_settle_max_sec()
        poll_sec = _straight_settle_poll_sec()
        deadline = time.monotonic() + max_sec
        stable_until = 0.0
        started = time.monotonic()
        last_rate = 0.0
        while True:
            if halt.is_set():
                return ("halted", time.monotonic() - started, last_rate)
            now = time.monotonic()
            if now >= deadline:
                log.warning(
                    "hover settle (%s): BAILED after %.2fs — "
                    "chassis never went below %.1f deg/s (last rate "
                    "%+.2f deg/s). Skipping drift sample for this cycle.",
                    context,
                    now - started,
                    rate_thr,
                    last_rate,
                )
                return ("timeout", now - started, last_rate)
            try:
                sample = rate_fn()
            except Exception:
                sample = None
            if sample is None:
                # Sampler hiccup; reset the stable window so a transient
                # ``None`` doesn't get treated as a "still" reading.
                stable_until = 0.0
            else:
                last_rate = float(sample)
                if abs(last_rate) < rate_thr:
                    if stable_until == 0.0:
                        stable_until = now + stable_sec
                    elif now >= stable_until:
                        elapsed = now - started
                        log.debug(
                            "hover settle (%s): settled in %.3fs "
                            "(last rate %+.2f deg/s, threshold %.2f)",
                            context, elapsed, last_rate, rate_thr,
                        )
                        return ("settled", elapsed, last_rate)
                else:
                    stable_until = 0.0
            if halt.wait(timeout=poll_sec):
                return ("halted", time.monotonic() - started, last_rate)

    def _drift_correct_loop(self, is_forward: bool) -> None:
        """Drive-then-check loop with at-standstill drift correction.

        Direction-agnostic implementation shared by both
        :meth:`start_pulse_straight_forward` (``is_forward=True``) and
        :meth:`start_pulse_straight_backward` (``is_forward=False``).
        The two motions use the exact same algorithm — only the drive
        direction (FWD vs BACK goals) differs. All tuning knobs
        (``NINA_HOVER_STRAIGHT_*``) are shared because the chassis's
        physical pivot geometry and IMU mount are direction-invariant.

        The IMU integrator starts ONCE at the top of the motion, so the
        drift samples taken at each cycle reflect CUMULATIVE heading
        deviation from the operator-defined start heading — not the
        rotation accumulated during the last leg only. This bounds the
        bot's maximum heading deviation to the deadband regardless of
        how small the per-leg yaw bias is.

        Each cycle: drive (forward or backward) for ``leg_sec`` →
        brake → wait for the chassis to actually stop (active settle
        on the IMU yaw rate, or a fixed timer fallback if no rate hook
        is wired) → sample cumulative drift → optionally correct with
        in-place micro-step pivots that rotate the chassis back toward
        the start heading → repeat. See
        :meth:`start_pulse_straight_forward` for the full algorithm
        rationale.
        """
        direction_label = "forward" if is_forward else "backward"
        drive_dir = self.DIR_FORWARD if is_forward else self.DIR_BACKWARD
        drive_short = "FWD" if is_forward else "REV"

        halt = self._pulse_halt
        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        drive_goals = self._goals_for_wheels(
            left_dir=drive_dir,
            left_speed=100,
            right_dir=drive_dir,
            right_speed=100,
        )

        # Forward and backward have independent leg durations. The
        # reference build wants a longer backward leg (default 1.0 s
        # vs forward's 0.5 s) because backward travel-per-second is
        # lower at the same lean magnitude and operators want a
        # perceptible amount of motion between standstill drift
        # samples. Forward path stays bit-identical — the
        # ``is_forward=True`` branch resolves the same getter it
        # always did.
        leg_sec = (
            _straight_leg_sec() if is_forward else _straight_back_leg_sec()
        )
        brake_settle = _straight_brake_settle_sec()
        abort_deg = _straight_abort_drift_deg()
        deadband = _straight_corr_deadband_deg()
        settle_rate = _straight_settle_rate_dps()
        settle_stable = _straight_settle_stable_sec()
        settle_max = _straight_settle_max_sec()
        have_rate_hook = self._imu_yaw_rate_fn is not None

        log.info(
            "hover %s straight loop: leg=%.2fs brake_settle=%.2fs "
            "abort_drift=%.1f deg deadband=%.2f deg residual=%.2f deg "
            "active_settle=%s (rate_thr=%.1f dps stable=%.2fs max=%.2fs) | "
            "%s L(id%s)=%s R(id%s)=%s",
            direction_label,
            leg_sec,
            brake_settle,
            abort_deg,
            deadband,
            _straight_corr_residual_deg(),
            "ON" if have_rate_hook else "OFF (no yaw_rate_fn hook)",
            settle_rate,
            settle_stable,
            settle_max,
            drive_short,
            self._left_id,
            drive_goals[self._left_id],
            self._right_id,
            drive_goals[self._right_id],
        )

        yaw_fn = self._imu_yaw_drift_fn
        cycle = 0
        # Start the IMU integrator ONCE at the top of the entire motion.
        # The post-leg drift samples then read CUMULATIVE drift from
        # movement-start (the operator-defined "straight" heading),
        # not per-leg drift. This catches small same-sign per-leg
        # biases (e.g. +1° / leg) that otherwise compound across many
        # cycles into a noticeable curve while each individual leg
        # stays inside the per-leg deadband and never triggers
        # correction.
        self._imu_begin_straight()
        try:
            while not halt.is_set():
                cycle += 1

                log.info(
                    "hover %s straight: cycle %d — %s leg %.2fs",
                    direction_label,
                    cycle,
                    direction_label,
                    leg_sec,
                )
                # 1. Drive leg (forward or backward)
                try:
                    self._apply_goals(drive_goals)
                except Exception:
                    log.debug(
                        "%s straight: leg apply failed",
                        direction_label, exc_info=True,
                    )
                if halt.wait(timeout=leg_sec):
                    break

                # 2. Brake + active settle (chassis MUST be still before
                # we sample drift, otherwise we'd be correcting a
                # still-rotating chassis and the brief pivot can't punch
                # through the ongoing rotation — the exact bug field
                # testing surfaced when corrections kept making drift
                # worse). Fallback to fixed brake_settle timer when no
                # yaw_rate_fn is wired (e.g. dev hosts without an IMU).
                try:
                    self._apply_goals(brake_goals)
                except Exception:
                    log.debug(
                        "%s straight: brake apply failed",
                        direction_label, exc_info=True,
                    )
                status, elapsed, last_rate = self._active_settle_until_still(
                    halt, context=f"{direction_label} cycle {cycle} post-leg"
                )
                if status == "halted":
                    break
                # Track whether the chassis settled cleanly. Even when it
                # didn't (timeout — usually a runaway-spin caused by
                # wheel asymmetry past the lean-stack stiction breakaway
                # point), we still want to sample drift and evaluate the
                # ABORT threshold so the loop self-halts the runaway
                # instead of silently launching another drive leg. We
                # just won't attempt fine correction on a chassis that's
                # still rotating (the brief pivot can't punch through
                # ongoing rotation).
                unsettled = False
                if status == "no_rate":
                    if brake_settle > 0.0 and halt.wait(timeout=brake_settle):
                        break
                elif status == "timeout":
                    log.warning(
                        "hover %s straight: cycle %d — settle TIMEOUT "
                        "(%.2fs, last rate %+.2f deg/s); sampling "
                        "cumulative drift anyway for abort check "
                        "(correction skipped — chassis not still)",
                        direction_label, cycle, elapsed, last_rate,
                    )
                    unsettled = True
                else:
                    log.info(
                        "hover %s straight: cycle %d — settled in "
                        "%.3fs (last rate %+.2f deg/s)",
                        direction_label, cycle, elapsed, last_rate,
                    )

                # 3. Cumulative drift sample (heading deviation from the
                # operator-defined start heading). The integrator runs
                # continuously so this value is meaningful even when
                # the chassis hasn't fully settled — we just can't
                # trust it for FINE correction in the unsettled case.
                drift = yaw_fn() if yaw_fn is not None else None
                if drift is None:
                    log.info(
                        "hover %s straight: cycle %d — no IMU sample, "
                        "skipping drift check",
                        direction_label, cycle,
                    )
                    continue

                log.info(
                    "hover %s straight: cycle %d — cumulative drift = "
                    "%+.2f deg%s (abort threshold %.1f deg)",
                    direction_label,
                    cycle,
                    drift,
                    " (UNSETTLED)" if unsettled else "",
                    abort_deg,
                )

                # 4. Abort check — too much cumulative drift to recover.
                # Evaluated whether or not the chassis settled cleanly:
                # the 90° default is coarse enough to be reliable even
                # during a runaway-spin, and the whole purpose of this
                # check is to catch runaways BEFORE launching another
                # drive leg that would compound the spin.
                if abort_deg > 0.0 and abs(drift) >= abort_deg:
                    log.warning(
                        "hover %s straight: cycle %d — drift %+.2f deg "
                        ">= %.1f deg ABORT threshold%s; halting and "
                        "announcing cant_move",
                        direction_label,
                        cycle,
                        drift,
                        abort_deg,
                        " (caught during unsettled spin)" if unsettled else "",
                    )
                    try:
                        self._apply_goals(brake_goals)
                    except Exception:
                        pass
                    try:
                        maybe_speak_cant_move_alert()
                    except Exception:
                        log.debug(
                            "%s straight: cant_move alert raised",
                            direction_label, exc_info=True,
                        )
                    halt.set()
                    break

                # If we got here from a settle TIMEOUT (chassis still
                # rotating, but drift below abort threshold), skip the
                # correction step — the standstill micro-step pivot
                # can't make a clean correction on a rotating chassis,
                # and attempting it can compound the rotation. Next leg
                # gets a fresh settle attempt.
                if unsettled:
                    continue

                # 5. Correct cumulative drift (micro-step pivot at
                # standstill). With cumulative tracking, each correction
                # physically rotates the chassis to drive the integrator
                # back toward zero — restoring the original heading
                # rather than zeroing the last leg's motion. Pivot
                # direction is direction-of-motion agnostic (in-place
                # rotation), so the same correction logic works for
                # both forward and backward.
                if abs(drift) > deadband:
                    self._correct_drift_at_standstill(
                        drift, brake_goals, halt,
                        direction_label=direction_label,
                    )
                else:
                    log.info(
                        "hover %s straight: cycle %d — drift %+.2f deg "
                        "within deadband %.2f deg, no correction needed",
                        direction_label,
                        cycle,
                        drift,
                        deadband,
                    )

            # Final brake on exit (operator release or abort).
            if not halt.is_set():
                try:
                    self._apply_goals(brake_goals)
                except Exception:
                    pass
        finally:
            # End the cumulative integrator that was started once at the
            # top of the motion. Always runs (halt, abort, exception).
            try:
                self._imu_end_straight()
            except Exception:
                pass

    def _correct_drift_at_standstill(
        self,
        initial_drift: float,
        brake_goals: Dict[int, int],
        halt: threading.Event,
        *,
        direction_label: str = "forward",
    ) -> None:
        """Iterative proportional micro-step pivot to drive ``|drift|`` to zero.

        Direction-agnostic: the chassis is braked at standstill before
        this routine is entered, so the in-place pivot geometry is
        identical whether the previous leg drove forward or backward.
        The ``direction_label`` is purely cosmetic, woven into log
        messages so an operator reading the log can trace each
        correction back to the motion that triggered it.

        Same step math as :meth:`_perform_pivot_correction` but driven
        at standstill between drive legs (chassis is already braked
        when this is called). Each step:

        1. Choose pivot direction from the sign of the latest drift
           sample (respecting ``NINA_HOVER_IMU_CORR_INVERT_SIGN``).
        2. Compute step duration ∝ ``|drift|``, clamped to
           ``[step_min_sec, step_dur_cap]``.
        3. Apply the pivot goals via :meth:`_goals_for_wheels`, wait,
           brake, settle.
        4. Resample drift from the integrator and decide whether to
           continue (residual / max-steps cap).

        Trigger and exit thresholds are decoupled via hysteresis: the
        caller fires this routine when ``|drift| > deadband``, but the
        loop continues until ``|drift| <= residual`` (typically much
        tighter than the deadband). This drives drift back near zero
        rather than just inside the deadband edge, eliminating the
        cycle-after-cycle compounding residual that produces a mild
        curve over time.

        Returns when ``|drift| <= residual``, after ``max_steps``
        iterations, or when ``halt`` is set.
        """
        # Use the new dedicated standstill knobs so the backward + legacy
        # in-motion correction (which run at 20% blend) keep their own
        # tuning. The "residual" threshold is the tight exit target;
        # "deadband" is only relevant for the caller's trigger decision
        # and the wrong-direction safety slop.
        deadband = _straight_corr_deadband_deg()
        residual = _straight_corr_residual_deg()
        # If residual >= deadband the loop would exit immediately on the
        # first sample post-step (which already passes the trigger), so
        # clamp residual to be strictly tighter than the deadband.
        if residual >= deadband:
            residual = max(0.1, deadband * 0.5)
        invert = _imu_corr_invert_sign()
        swap_pivot = _straight_corr_swap_pivot_dir()
        step_blend = _straight_corr_blend_pct()
        # Step duration cap + floor diverge between forward and backward:
        # the same 0.030 s kick that produces ~2.5° clean rotation when
        # correcting after a forward leg over-pushes (5–8°) when
        # correcting after a backward leg on the reference chassis
        # (chassis-asymmetric pre-pivot momentum profile). Backward gets
        # softer defaults (0.020 s cap / 0.015 s floor) so the
        # proportional formula actually shrinks per-step rotation for
        # small drifts. Forward path is bit-identical to before — its
        # branch resolves the same two getters it always did.
        if direction_label == "backward":
            step_dur_cap = _straight_corr_back_step_dur_cap_sec()
            step_min = _straight_corr_back_step_min_sec()
        else:
            step_dur_cap = _straight_corr_step_dur_cap_sec()
            step_min = _straight_corr_step_min_sec()
        step_rate = _straight_corr_step_rate_dps()
        step_settle = _imu_corr_step_settle_sec()
        max_steps = _imu_corr_max_steps()
        yaw_fn = self._imu_yaw_drift_fn

        current = initial_drift
        log.info(
            "hover %s drift-correct: start=%+.2f deg deadband=%.2f deg "
            "residual=%.2f deg invert=%s swap_pivot=%s step_blend=%d%% "
            "step_rate=%.1fdps step_dur_cap=%.2fs step_min=%.2fs "
            "step_settle=%.2fs max_steps=%d",
            direction_label,
            current,
            deadband,
            residual,
            invert,
            swap_pivot,
            step_blend,
            step_rate,
            step_dur_cap,
            step_min,
            step_settle,
            max_steps,
        )

        for step in range(max_steps):
            if halt.is_set():
                return
            if abs(current) <= residual:
                log.info(
                    "hover %s drift-correct: reached residual at step "
                    "%d (drift %+.2f deg within residual %.2f deg, "
                    "%d steps used)",
                    direction_label,
                    step,
                    current,
                    residual,
                    step,
                )
                return

            # Pivot direction: positive drift = drifted right → pivot left
            # to correct (and vice versa). ``invert`` flips the drift sign
            # interpretation for chassis with the opposite IMU mount
            # convention. ``swap_pivot`` flips the pivot-label-to-goals
            # mapping for chassis where the conventional
            # "L=FWD,R=BACK → rotate LEFT" doesn't hold (field-observed
            # default on the reference chassis is the swapped mapping;
            # see :func:`_straight_corr_swap_pivot_dir`).
            effective = -current if invert else current
            pivot_left_decision = effective > 0.0
            apply_left_goals = (
                not pivot_left_decision if swap_pivot else pivot_left_decision
            )
            if apply_left_goals:
                step_goals = self._goals_for_wheels(
                    left_dir=self.DIR_FORWARD,
                    left_speed=step_blend,
                    right_dir=self.DIR_BACKWARD,
                    right_speed=step_blend,
                )
            else:
                step_goals = self._goals_for_wheels(
                    left_dir=self.DIR_BACKWARD,
                    left_speed=step_blend,
                    right_dir=self.DIR_FORWARD,
                    right_speed=step_blend,
                )

            # Proportional duration: aim to land at **zero** drift. The
            # old "land at ½ deadband short of zero" target weakens the
            # per-step kick for drifts just past the deadband edge — a
            # 3.6° drift with a 3.5° deadband targets only 1.85° of
            # rotation, hits the 0.015 s step floor, and gets defeated
            # by chassis stiction (the user-observed "bot doesn't seem
            # to correct" pattern). Aiming for zero gives the step a
            # full deadband-worth more torque budget; if it overshoots,
            # the next leg's sample exits via the deadband.
            target_rotation = max(0.5, abs(current))
            this_step_dur = max(
                step_min, min(step_dur_cap, target_rotation / step_rate)
            )

            try:
                self._apply_goals(step_goals)
            except Exception:
                log.debug(
                    "%s drift-correct: step apply failed",
                    direction_label, exc_info=True,
                )
            if halt.wait(timeout=this_step_dur):
                return
            try:
                self._apply_goals(brake_goals)
            except Exception:
                pass
            # Active settle between correction steps so the next drift
            # sample isn't taken while the chassis is still rotating
            # from the pivot we just commanded. Falls back to the legacy
            # step_settle timer when no yaw_rate_fn is wired.
            settle_status, settle_elapsed, _ = self._active_settle_until_still(
                halt, context=f"{direction_label} corr step {step + 1}"
            )
            if settle_status == "halted":
                return
            if settle_status == "no_rate":
                if step_settle > 0.0 and halt.wait(timeout=step_settle):
                    return
            elif settle_status == "timeout":
                log.warning(
                    "hover %s drift-correct: step %d settle BAILED "
                    "(elapsed %.2fs); exiting correction so the next "
                    "leg can sample fresh.",
                    direction_label, step + 1, settle_elapsed,
                )
                return

            sample = yaw_fn() if yaw_fn is not None else None
            if sample is None:
                log.debug(
                    "%s drift-correct: step %d IMU returned None, "
                    "holding previous drift estimate",
                    direction_label, step + 1,
                )
                continue
            log.info(
                "hover %s drift-correct: step %d — decision=pivot %s "
                "applied=%s_goals this_dur=%.3fs drift was %+.2f → %+.2f deg",
                direction_label,
                step + 1,
                "LEFT" if pivot_left_decision else "RIGHT",
                "LEFT" if apply_left_goals else "RIGHT",
                this_step_dur,
                current,
                sample,
            )

            # Safety net: if this step INCREASED |drift| by more than a
            # noise slop, the pivot is going the wrong way (operator has
            # the wrong INVERT_SIGN, the IMU just glitched, or a wheel
            # stalled while the other spun). Don't compound the error —
            # exit immediately so the next drive leg can sample fresh
            # rather than keep spinning into a 400° runaway.
            wrong_dir_slop = max(0.5, deadband)
            if abs(sample) > abs(current) + wrong_dir_slop:
                log.warning(
                    "hover %s drift-correct: step %d INCREASED |drift| "
                    "(|%+.2f| -> |%+.2f|, slop=%.2f deg) — pivot direction "
                    "is wrong (check NINA_HOVER_IMU_CORR_INVERT_SIGN) or "
                    "sensor glitch. Aborting correction; next leg will "
                    "sample fresh.",
                    direction_label,
                    step + 1,
                    current,
                    sample,
                    wrong_dir_slop,
                )
                return

            current = sample

        log.warning(
            "hover %s drift-correct: max steps (%d) reached, drift "
            "now %+.2f deg (next leg will sample fresh)",
            direction_label,
            max_steps,
            current,
        )

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
            fl_cal = int(self._axis.forward_pos_left) + _straight_fwd_left_ticks_offset()
            fr_cal = int(self._axis.forward_pos_right) + _straight_fwd_right_ticks_offset()
            fl = self._dxl._clamp_pos(
                _nudge_goal_from_brake(fl_cal, nl, _STRAIGHT_FWD_EXTRA_TICKS)
            )
            fr = self._dxl._clamp_pos(
                _nudge_goal_from_brake(fr_cal, nr, _STRAIGHT_FWD_EXTRA_TICKS)
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
            # Per-side SIGNED nudges away from brake (see the
            # :data:`_STRAIGHT_BACK_LEFT_TICKS_OFFSET` block for the
            # wheel-asymmetry rationale). Positive = MORE backward
            # lean for that side regardless of whether the calibrated
            # ``backward_pos_*`` sits above or below brake; negative
            # = LESS lean (pulled toward brake). This is the same
            # nudge mechanism the forward branch uses, just with
            # independent per-side magnitudes so the operator can
            # bias against BLDC wheel asymmetry. (The earlier raw
            # ``goal + offset`` model was a bug: on a chassis where
            # ``backward_pos_right < brake`` it produced LESS lean
            # for a positive offset, leaving one wheel un-pushed and
            # spinning the chassis on the other wheel only.)
            bl_offset = _straight_back_left_ticks_offset()
            br_offset = _straight_back_right_ticks_offset()
            bl = self._dxl._clamp_pos(
                _nudge_goal_from_brake(
                    int(self._axis.backward_pos_left), nl, bl_offset
                )
            )
            br = self._dxl._clamp_pos(
                _nudge_goal_from_brake(
                    int(self._axis.backward_pos_right), nr, br_offset
                )
            )
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
        """Timed yaw: full pivot lean, hold, then brake.

        Uses full calibrated pivot goals on every micro-step. Held D-pad
        pivots use asymmetric duties separately.

        Honours :func:`_imu_turn_swap_pivot_dir`: under the swapped
        mapping (the reference-chassis default) a "turn left" request
        applies ``L=BACK, R=FWD`` geometry — that's what physically
        rotates the swapped chassis to the left.
        """
        _ = speed_percent  # reserved; excursion is angle blend, not duty
        blend = _held_dpad_pivot_blend_pct()
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 0.0)
        )
        if _imu_turn_swap_pivot_dir():
            left_dir, right_dir = self.DIR_BACKWARD, self.DIR_FORWARD
        else:
            left_dir, right_dir = self.DIR_FORWARD, self.DIR_BACKWARD
        goals = self._goals_for_wheels(
            left_dir=left_dir,
            left_speed=blend,
            right_dir=right_dir,
            right_speed=blend,
        )
        log.info(
            "hover timed turn_left: blend=%d%% goals L=%d R=%d (full pivot L/R via hover_computed_turn_pivot_goals)",
            blend,
            goals.get(self._left_id),
            goals.get(self._right_id),
        )
        self._apply_goals(goals)
        if dur > 0.0:
            time.sleep(dur)
        self.stop(settle=False)

    def turn_right(
        self,
        speed_percent: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> None:
        """Timed yaw: full pivot lean; mirror of :meth:`turn_left`.

        Honours :func:`_imu_turn_swap_pivot_dir`: under the swapped
        mapping (the reference-chassis default) a "turn right" request
        applies ``L=FWD, R=BACK`` geometry — that's what physically
        rotates the swapped chassis to the right.
        """
        _ = speed_percent
        blend = _held_dpad_pivot_blend_pct()
        dur = float(
            duration
            if duration is not None
            else getattr(self.config, "turn_duration_sec", 0.0)
        )
        if _imu_turn_swap_pivot_dir():
            left_dir, right_dir = self.DIR_FORWARD, self.DIR_BACKWARD
        else:
            left_dir, right_dir = self.DIR_BACKWARD, self.DIR_FORWARD
        goals = self._goals_for_wheels(
            left_dir=left_dir,
            left_speed=blend,
            right_dir=right_dir,
            right_speed=blend,
        )
        log.info(
            "hover timed turn_right: blend=%d%% goals L=%d R=%d",
            blend,
            goals.get(self._left_id),
            goals.get(self._right_id),
        )
        self._apply_goals(goals)
        if dur > 0.0:
            time.sleep(dur)
        self.stop(settle=False)

    def _hold_turn_brake_goals(self) -> Dict[int, int]:
        return {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }

    def _begin_hold_turn_session(self, direction: str) -> bool:
        """Anchor IMU for held D-pad L/R or a single Turn-button micro-step."""
        if not self._is_initialized:
            return False
        direction = (direction or "").strip().lower()
        if direction not in ("left", "right"):
            return False
        if self._hold_turn_session_dir == direction:
            return True

        self.end_hold_turn_session()
        self._halt_pulse_series(wait=True)
        brake_goals = self._hold_turn_brake_goals()
        try:
            self._apply_goals(brake_goals)
        except Exception:
            log.debug("hold turn: pre-brake apply failed", exc_info=True)

        turn_halt = threading.Event()
        pre_status, pre_elapsed, pre_rate = self._active_settle_until_still(
            turn_halt, context=f"hold turn ({direction}) pre"
        )
        if pre_status == "no_rate":
            pre_settle = _imu_turn_pre_settle_sec()
            if pre_settle > 0.0:
                time.sleep(pre_settle)
        elif pre_status == "settled":
            log.debug(
                "hover hold turn (%s): pre-settle done in %.3fs (last rate %+.2f deg/s)",
                direction,
                pre_elapsed,
                pre_rate,
            )

        yaw_fn = self._imu_yaw_drift_fn
        if yaw_fn is None:
            return False

        self._imu_begin_straight()
        initial = self._poll_yaw_until_ready(yaw_fn, timeout_sec=0.5)
        if initial is None:
            self._imu_end_straight()
            return False

        invert = _imu_corr_invert_sign()
        self._hold_turn_session_dir = direction
        self._hold_turn_yaw_current = (
            -float(initial) if invert else float(initial)
        )
        log.info(
            "hover hold turn (%s): session begin yaw=%+.2f deg",
            direction,
            self._hold_turn_yaw_current,
        )
        return True

    def end_hold_turn_session(self) -> None:
        """End IMU session after D-pad L/R release or one-shot Turn click."""
        if self._hold_turn_session_dir is None:
            return
        direction = self._hold_turn_session_dir
        try:
            try:
                self._apply_goals(self._hold_turn_brake_goals())
            except Exception:
                log.debug("hold turn: post-brake apply failed", exc_info=True)
            post_halt = threading.Event()
            post_status, _, _ = self._active_settle_until_still(
                post_halt, context=f"hold turn ({direction}) post"
            )
            if post_status == "no_rate":
                post_settle = _imu_turn_post_settle_sec()
                if post_settle > 0.0:
                    time.sleep(post_settle)
            self._imu_end_straight()
        finally:
            self._hold_turn_session_dir = None
            self._hold_turn_yaw_current = 0.0

    def _imu_turn_run_one_step(
        self,
        direction: str,
        remaining_deg: float,
        *,
        step_index: int,
    ) -> Optional[float]:
        """One closed-loop pivot step; returns updated yaw in intent frame."""
        step_blend = _held_dpad_pivot_blend_pct()
        step_rate = _imu_turn_step_rate_deg_per_sec()
        step_dur_cap = _turn_step_dur_cap_sec()
        step_min = _turn_step_min_sec()
        step_settle = _turn_step_settle_sec()
        swap_pivot = _imu_turn_swap_pivot_dir()
        brake_goals = self._hold_turn_brake_goals()
        turn_halt = threading.Event()

        pivot_right_decision = remaining_deg > 0.0
        apply_right_goals = (
            not pivot_right_decision if swap_pivot else pivot_right_decision
        )
        if apply_right_goals:
            step_goals = self._goals_for_wheels(
                left_dir=self.DIR_BACKWARD,
                left_speed=step_blend,
                right_dir=self.DIR_FORWARD,
                right_speed=step_blend,
            )
        else:
            step_goals = self._goals_for_wheels(
                left_dir=self.DIR_FORWARD,
                left_speed=step_blend,
                right_dir=self.DIR_BACKWARD,
                right_speed=step_blend,
            )

        rotation_target = max(0.5, abs(remaining_deg))
        this_step_dur = max(
            step_min,
            min(step_dur_cap, rotation_target / step_rate),
        )
        try:
            self._apply_goals(step_goals)
            log.info(
                "hover hold turn (%s): step %d blend=%d%% goals L=%d R=%d",
                direction,
                step_index,
                step_blend,
                step_goals.get(self._left_id),
                step_goals.get(self._right_id),
            )
        except Exception:
            log.debug(
                "hover hold turn (%s): step apply failed",
                direction,
                exc_info=True,
            )
        time.sleep(this_step_dur)
        try:
            self._apply_goals(brake_goals)
        except Exception:
            pass
        settle_status, settle_elapsed, _ = self._active_settle_until_still(
            turn_halt,
            context=f"hold turn ({direction}) step {step_index}",
        )
        if settle_status == "no_rate" and step_settle > 0.0:
            time.sleep(step_settle)
        elif settle_status == "settled":
            log.debug(
                "hover hold turn (%s): step %d settled in %.3fs",
                direction,
                step_index,
                settle_elapsed,
            )

        yaw_fn = self._imu_yaw_drift_fn
        if yaw_fn is None:
            return None
        invert = _imu_corr_invert_sign()
        raw = yaw_fn()
        if raw is None:
            return None
        return -float(raw) if invert else float(raw)

    def pulse_turn_micro_step(self, direction: str) -> bool:
        """One ~``NINA_DRIVE_TURN_PIVOT_DEG`` closed-loop pivot (held L/R or Turn click)."""
        if not self._is_initialized:
            return False
        direction = (direction or "").strip().lower()
        if direction not in ("left", "right"):
            log.warning(
                "pulse_turn_micro_step: unknown direction %r (expected 'left' / 'right')",
                direction,
            )
            return False
        if not self._begin_hold_turn_session(direction):
            log.warning(
                "pulse_turn_micro_step(%s): no IMU session — caller should use timed fallback",
                direction,
            )
            return False

        budget = _drive_turn_micro_step_deg()
        remaining = budget if direction == "right" else -budget
        step_idx = 1
        sample = self._imu_turn_run_one_step(
            direction, remaining, step_index=step_idx,
        )
        if sample is not None:
            self._hold_turn_yaw_current = sample
            log.debug(
                "hover hold turn (%s): micro-step %d yaw=%+.2f deg",
                direction,
                step_idx,
                sample,
            )
        return True

    def pulse_turn_degrees(self, direction: str, degrees: float) -> bool:
        """Closed-loop in-place turn by ``degrees`` using IMU micro-steps.

        Reuses the iterative proportional micro-step machinery that
        :meth:`_correct_drift_at_standstill` uses for straight-leg drift
        correction, but anchored to a yaw BUDGET (±``degrees``)
        instead of correcting a small drift back to zero. Each step:

        1. Sample remaining yaw (target − current).
        2. Choose pivot direction from the sign of remaining (positive
           remaining in intent frame → "pivot right" decision); the
           label-to-goals mapping then honours
           ``NINA_HOVER_TURN_SWAP_PIVOT_DIR`` so the geometry is
           correct on the reference chassis (where the conventional
           L=FWD,R=BACK → rotate-LEFT mapping is inverted).
        3. Compute step duration ∝ |remaining| (aim-at-zero), clamped
           to ``[step_min_sec, step_dur_cap]`` so big remainings get
           the full cap and the last few degrees fall through small,
           precise pulses. The next sample exits via the deadband if
           the step overshoots zero.
        4. Apply the pivot goals (built via :meth:`_goals_for_wheels`
           so they honour the operator's tuned forward / backward lean
           positions), wait, brake, wait for the chassis to actually
           stop (yaw-rate active settle, same mechanism the drift-
           corrected straight loop uses), re-sample.

        Halts any in-flight straight-pulse series first, brackets the
        turn with :meth:`_imu_begin_straight` / :meth:`_imu_end_straight`
        so the IMU integrator is reset to zero at the start (turning
        the integrator into a closed-loop angle measurement for the
        duration of the turn). The battery latch check is owned by
        :meth:`sirena_ui.workers.drive_controller.DriveController._do_turn_90`,
        which is the only caller wired from the Drive screen buttons.

        ``direction`` must be ``"left"`` or ``"right"``. ``degrees`` is
        clamped to ``[5, 360]``. Returns ``True`` when the turn completed
        cleanly inside the deadband, ``False`` when it bailed early.

        Falls back to the legacy timed :meth:`turn_left` /
        :meth:`turn_right` open-loop pivot when the IMU yaw sampler is
        not wired or is returning ``None`` (integrator paused).
        """
        if not self._is_initialized:
            return False

        direction = (direction or "").strip().lower()
        if direction not in ("left", "right"):
            log.warning(
                "pulse_turn_degrees: unknown direction %r (expected 'left' / 'right')",
                direction,
            )
            return False
        try:
            target_deg = max(5.0, min(360.0, abs(float(degrees))))
        except (TypeError, ValueError):
            log.warning("pulse_turn_degrees: invalid degrees %r", degrees)
            return False

        # 1. Halt any in-flight straight pulse so we start from a known
        # stationary pose; ``_halt_pulse_series`` joins the thread.
        self._halt_pulse_series(wait=True)

        brake_goals = {
            self._left_id: self._brake_left,
            self._right_id: self._brake_right,
        }
        # 2. Brake and wait for the chassis to actually stop before
        # we anchor the integrator. Yaw-rate active settle (same
        # mechanism the drift-corrected straight loop uses) ensures
        # the integrator zero corresponds to a genuinely stationary
        # chassis; the legacy fixed-timer settle is the fallback
        # when no yaw_rate_fn is wired (dev hosts without an IMU).
        try:
            self._apply_goals(brake_goals)
        except Exception:
            log.debug("pulse_turn_degrees: pre-brake apply failed", exc_info=True)
        # ``pulse_turn_degrees`` is synchronous (no daemon thread, no
        # external halt mechanism) — use a never-set local Event so
        # the active settle helper's halt-aware sleeps still work
        # without sharing the pulse halt event.
        turn_halt = threading.Event()
        pre_status, pre_elapsed, pre_rate = self._active_settle_until_still(
            turn_halt, context=f"turn ({direction}) pre"
        )
        if pre_status == "no_rate":
            pre_settle = _imu_turn_pre_settle_sec()
            if pre_settle > 0.0:
                time.sleep(pre_settle)
        elif pre_status == "settled":
            log.debug(
                "hover IMU turn (%s): pre-settle done in %.3fs "
                "(last rate %+.2f deg/s)",
                direction, pre_elapsed, pre_rate,
            )
        # "timeout" / "halted" still proceed — at worst we anchor the
        # integrator on a slowly-rotating chassis (the active settle
        # itself is best-effort; the no-progress safeguard catches
        # the case where the chassis is actually stuck).

        yaw_fn = self._imu_yaw_drift_fn
        if yaw_fn is None:
            # No IMU hook wired (autonomy off, dev box, unit test
            # without IMU fake) — fall back to the timed pivot so the
            # button still does *something*.
            log.warning(
                "pulse_turn_degrees(%s): no IMU yaw hook wired, falling back to timed turn",
                direction,
            )
            self._pulse_turn_timed_fallback(direction)
            return False

        # 3. Begin the integrator. This resets ``yaw_accum`` to 0 and
        # flips ``_track_straight`` on so subsequent ``yaw_fn()`` calls
        # return a live signed angle relative to the start of the turn.
        self._imu_begin_straight()
        try:
            # Make sure the integrator has actually started reporting
            # before we treat its samples as authoritative. A brief
            # poll-and-wait loop tolerates the snapshot lag without
            # blocking the Qt worker forever.
            initial = self._poll_yaw_until_ready(yaw_fn, timeout_sec=0.5)
            if initial is None:
                log.warning(
                    "pulse_turn_degrees(%s): IMU yaw sampler returned None for 0.5 s, "
                    "falling back to timed turn",
                    direction,
                )
                self._pulse_turn_timed_fallback(direction)
                return False

            base_steps = _imu_turn_max_steps()
            max_steps = max(5, min(200, int(round(base_steps * target_deg / 90.0))))
            step_blend = _imu_turn_step_blend_pct()
            step_rate = _imu_turn_step_rate_deg_per_sec()
            step_dur_cap = _turn_step_dur_cap_sec()
            step_min = _turn_step_min_sec()
            step_settle = _turn_step_settle_sec()
            deadband = _imu_corr_deadband_deg()
            invert = _imu_corr_invert_sign()
            swap_pivot = _imu_turn_swap_pivot_dir()
            progress_check = _imu_turn_progress_check_steps()
            progress_min = _imu_turn_progress_min_deg()

            # Intent-frame target: + = the chassis must rotate right
            # (clockwise viewed from above). ``invert`` flips both the
            # reading and the target so the pivot-direction decision
            # stays correct regardless of IMU mount sign.
            target_intent = target_deg if direction == "right" else -target_deg

            def yaw_intent() -> Optional[float]:
                raw = yaw_fn()
                if raw is None:
                    return None
                return -float(raw) if invert else float(raw)

            current = -float(initial) if invert else float(initial)
            log.info(
                "hover IMU turn (%s): begin target=%+.1f deg (intent frame, "
                "invert=%s, swap_pivot=%s), step_blend=%d%%, step_rate=%.1fdps, "
                "step_dur_cap=%.2fs, step_min=%.2fs, step_settle=%.2fs, "
                "deadband=%.2f deg, max_steps=%d, progress_check=%d/%.2f deg",
                direction,
                target_intent,
                invert,
                swap_pivot,
                step_blend,
                step_rate,
                step_dur_cap,
                step_min,
                step_settle,
                deadband,
                max_steps,
                progress_check,
                progress_min,
            )

            first_remaining_abs: Optional[float] = None
            steps_taken = 0
            exit_reason = "max-steps cap"
            for step in range(max_steps):
                remaining = target_intent - current
                if abs(remaining) <= deadband:
                    exit_reason = "deadband reached"
                    break

                if first_remaining_abs is None:
                    first_remaining_abs = abs(remaining)

                # No-progress bail: if after PROGRESS_CHECK_STEPS the
                # remaining hasn't shrunk by at least PROGRESS_MIN_DEG,
                # the pivots aren't actually rotating the chassis (stuck
                # wheel, no torque). Bail cleanly rather than burning
                # MAX_STEPS of ineffective pulses.
                if (
                    progress_check > 0
                    and step >= progress_check
                    and first_remaining_abs is not None
                ):
                    advance = first_remaining_abs - abs(remaining)
                    if advance < progress_min:
                        log.info(
                            "hover IMU turn (%s): no progress after %d steps "
                            "(advance=%+.2f deg, threshold=%.2f deg) — bailing",
                            direction,
                            step + 1,
                            advance,
                            progress_min,
                        )
                        exit_reason = "no progress"
                        break

                # Decide chassis-frame pivot direction from the sign of
                # remaining yaw budget. ``swap_pivot`` then flips the
                # label-to-goals mapping for chassis where the
                # conventional "L=BACK,R=FWD → rotate RIGHT" doesn't
                # hold (field-observed default on the reference
                # chassis is the swapped mapping; see
                # :func:`_imu_turn_swap_pivot_dir`).
                pivot_right_decision = remaining > 0.0
                apply_right_goals = (
                    not pivot_right_decision if swap_pivot else pivot_right_decision
                )
                if apply_right_goals:
                    step_goals = self._goals_for_wheels(
                        left_dir=self.DIR_BACKWARD,
                        left_speed=step_blend,
                        right_dir=self.DIR_FORWARD,
                        right_speed=step_blend,
                    )
                else:
                    step_goals = self._goals_for_wheels(
                        left_dir=self.DIR_FORWARD,
                        left_speed=step_blend,
                        right_dir=self.DIR_BACKWARD,
                        right_speed=step_blend,
                    )

                # Proportional duration: aim to land at **zero**
                # remaining (same change we made to standstill drift
                # correction). The old "land at ½ deadband short of
                # target" formula weakens the per-step kick for the
                # last few degrees of the budget; aiming for zero
                # gives the step a full deadband-worth more torque
                # budget and the next sample exits via the deadband
                # if it overshoots.
                rotation_target = max(0.5, abs(remaining))
                this_step_dur = max(
                    step_min,
                    min(step_dur_cap, rotation_target / step_rate),
                )

                try:
                    self._apply_goals(step_goals)
                except Exception:
                    log.debug(
                        "hover IMU turn (%s): step apply failed", direction,
                        exc_info=True,
                    )
                time.sleep(this_step_dur)

                # Brake then wait for the chassis to actually stop
                # before sampling. Active settle on the yaw rate
                # avoids the "brief pivot can't punch through the
                # ongoing rotation" failure mode that bit the straight
                # drift correction. Falls back to the legacy fixed
                # step_settle timer when no yaw_rate_fn is wired.
                try:
                    self._apply_goals(brake_goals)
                except Exception:
                    pass
                settle_status, settle_elapsed, _ = self._active_settle_until_still(
                    turn_halt, context=f"turn ({direction}) step {step + 1}"
                )
                if settle_status == "no_rate":
                    if step_settle > 0.0:
                        time.sleep(step_settle)
                elif settle_status == "settled":
                    log.debug(
                        "hover IMU turn (%s): step %d settled in %.3fs",
                        direction, step + 1, settle_elapsed,
                    )
                # "timeout" continues to the sample anyway — the loop's
                # next iteration will re-evaluate progress / max_steps.

                sample = yaw_intent()
                if sample is None:
                    # Transient None — don't update ``current``; the
                    # next iteration will re-sample. We still count the
                    # step so a permanently broken IMU eventually hits
                    # ``max_steps``.
                    log.debug(
                        "hover IMU turn (%s): step %d IMU returned None, "
                        "skipping update",
                        direction,
                        step + 1,
                    )
                    steps_taken = step + 1
                    continue

                current = sample
                steps_taken = step + 1
                log.debug(
                    "hover IMU turn (%s): step %d yaw=%+.2f deg remaining=%+.2f "
                    "deg this_dur=%.3fs",
                    direction,
                    steps_taken,
                    current,
                    target_intent - current,
                    this_step_dur,
                )

            final_remaining = target_intent - current
            log.info(
                "hover IMU turn (%s): complete after %d step%s "
                "(final yaw=%+.2f deg, remaining=%+.2f deg, exited via %s)",
                direction,
                steps_taken,
                "" if steps_taken == 1 else "s",
                current,
                final_remaining,
                exit_reason,
            )
            return exit_reason == "deadband reached"
        finally:
            # 4. Always brake + post-settle so the bot is left
            # stationary, then end the integrator. The ``_imu_end_straight``
            # call leaves the snapshot's last yaw intact in case the
            # autonomy stack wants to inspect it. Active settle
            # confirms the chassis actually came to rest before we
            # hand control back to the caller; falls back to the
            # legacy fixed-timer post-settle when no yaw_rate_fn is
            # wired.
            try:
                self._apply_goals(brake_goals)
            except Exception:
                log.debug(
                    "pulse_turn_degrees: post-brake apply failed", exc_info=True
                )
            post_halt = threading.Event()
            post_status, _, _ = self._active_settle_until_still(
                post_halt, context=f"turn ({direction}) post"
            )
            if post_status == "no_rate":
                post_settle = _imu_turn_post_settle_sec()
                if post_settle > 0.0:
                    time.sleep(post_settle)
            self._imu_end_straight()

    def pulse_turn_90(self, direction: str) -> bool:
        """Closed-loop ~90° turn (unchanged behaviour — delegates here)."""
        return self.pulse_turn_degrees(direction, _imu_turn_target_deg())

    def _poll_yaw_until_ready(
        self,
        yaw_fn: Callable[[], Optional[float]],
        *,
        timeout_sec: float,
        poll_interval_sec: float = 0.02,
    ) -> Optional[float]:
        """Poll ``yaw_fn`` until it returns a number or the timeout elapses.

        ``MpuDriftMonitor.begin_straight_leg`` flips ``_track_straight``
        immediately but the snapshot only starts returning a non-``None``
        ``drift_side`` once the next poll tick lands. This helper waits
        for that bridge so the turn doesn't false-fall-back to the
        timed pivot on a perfectly-fine integrator just because we
        sampled too early.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while True:
            value = yaw_fn()
            if value is not None:
                return float(value)
            if time.monotonic() >= deadline:
                return None
            time.sleep(max(0.001, poll_interval_sec))

    def _pulse_turn_timed_fallback(self, direction: str) -> None:
        """Open-loop timed pivot when the closed-loop turn can't run.

        Reuses the legacy :meth:`turn_left` / :meth:`turn_right` path so
        an IMU outage on the bot doesn't render the Drive screen turn
        buttons dead.
        """
        if direction == "left":
            self.turn_left()
        else:
            self.turn_right()

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
