"""Closed-loop 90° turn (``HoverboardAxisDrive.pulse_turn_90``).

The Drive screen "Turn left" / "Turn right" buttons used to be
open-loop timed pivots (``HoverboardAxisDrive.turn_left`` /
``turn_right``): a partial-pivot lean held for a fixed sleep. That
produced wildly inconsistent yaw across battery levels and floor
surfaces. The new ``pulse_turn_90`` reuses the same iterative
proportional micro-step machinery the forward straight-leg drift
correction has been tuned with — same per-step geometry, same per-step
duration formula, same brake-between-steps for clean IMU sampling —
but anchored to a yaw BUDGET (target ±90°) instead of correcting a
drift.

These tests pin the contract:

* the pivot direction (left wheel vs right wheel direction) on the
  *first* micro-step matches the requested turn direction, so a UI bug
  flipping the label can't silently swap directions on the chassis;
* the loop terminates inside ``NINA_HOVER_IMU_CORR_DEADBAND_DEG`` of
  the target when the IMU is reporting sensible numbers, returning
  ``True``;
* the loop bails at ``NINA_HOVER_TURN_MAX_STEPS`` when the IMU is
  stuck, returning ``False`` (so the operator's caller can log a
  warning without the bot rotating forever);
* an unwired IMU falls back to the legacy timed pivot so the button
  is never functionally dead;
* an IMU integrator that returns ``None`` for too long also falls
  back (a paused integrator must not strand the turn);
* ``_imu_begin_straight`` / ``_imu_end_straight`` bracket the turn so
  the integrator is zeroed at the start and left in a clean state on
  exit (regardless of the exit reason);
* an in-flight straight pulse series is halted before the turn fires,
  so pressing "Turn left" while the bot is in a forward pulse leg
  doesn't race the two motion programs.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import patch

import pytest

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    _imu_turn_max_steps,
    _imu_turn_post_settle_sec,
    _imu_turn_pre_settle_sec,
    _imu_turn_progress_check_steps,
    _imu_turn_progress_min_deg,
    _imu_turn_step_blend_pct,
    _imu_turn_step_rate_deg_per_sec,
    _imu_turn_swap_pivot_dir,
    _imu_turn_target_deg,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


class FakeDxl:
    def __init__(self) -> None:
        self.goal_writes: List[dict[int, int]] = []
        self._present: dict[int, int] = {12: 2048, 13: 2048}

    def _require_initialized(self) -> None:
        return None

    def _clamp_pos(self, value: int) -> int:
        return max(0, min(4095, int(value)))

    def sync_write_moving_speed_subset(self, *_a, **_k) -> None:
        return None

    def sync_write_goal_position(self, positions: dict[int, int]) -> None:
        self.goal_writes.append(dict(positions))
        self._present.update({int(k): int(v) for k, v in positions.items()})

    def read_reg(self, sid: int, addr: int, size: int):
        if addr == REG_PRESENT_POS[0] and size == REG_PRESENT_POS[1]:
            return self._present.get(int(sid), 2048)
        return None


def _axis() -> HoverboardAxisSettings:
    return HoverboardAxisSettings(
        id_left=12,
        id_right=13,
        brake_pos_left=2048,
        brake_pos_right=2048,
        forward_pos_left=2100,
        forward_pos_right=2100,
        backward_pos_left=2000,
        backward_pos_right=2000,
        swap_turn_lr=True,
        turn_push_ticks=0,
        tilt_deg=5.0,
        moving_speed=0,
        sign_left=1,
        sign_right=1,
        pulse_forward_enabled=True,
        pulse_forward_on_sec=0.04,
        pulse_forward_brake_sec=0.04,
        pulse_forward_return_ramp_sec=0.0,
        pulse_forward_coast_blend=0.2,
        pulse_forward_sync_present=False,
        pulse_forward_present_tol_ticks=4,
        pulse_forward_present_step_timeout_sec=0.25,
        pulse_ramp_profile="smoothstep",
        pulse_ramp_trap_edge=0.18,
        pulse_ramp_moving_speed=None,
        pulse_waveform="dual_ramp",
        pulse_series_max=2,
        pulse_series_fwd_sec=0.02,
        pulse_series_coast_initial_sec=0.01,
        pulse_series_min_transition_sec=0.015,
        pulse_series_back_sec=0.02,
        pulse_series_back_coast_initial_sec=0.01,
        pulse_backward_coast_blend=0.2,
        pulse_backward_return_ramp_sec=0.0,
    )


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.0,
        turn_duration_sec=0.2,
        invert_left_dir=False,
        invert_right_dir=False,
    )


def _fast_turn_env(**extras: str) -> dict[str, str]:
    """Tunables that make ``pulse_turn_90`` finish quickly in tests.

    Zeroes out the pre/post settle, deadband-tightens to 1°, sets a
    healthy step rate so the proportional clamp falls onto the
    explicitly-configured PIVOT_MAX_SEC cap (matches the test pattern
    in ``test_hover_imu_correction.py``).

    ``NINA_HOVER_TURN_SWAP_PIVOT_DIR=0`` keeps the legacy geometry
    contract (turn-right → L=BACK, R=FWD) valid for the existing
    direction tests; a dedicated test exercises swap=True separately.
    """
    env = {
        "NINA_HOVER_TURN_PRE_SETTLE_SEC": "0.0",
        "NINA_HOVER_TURN_POST_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT": "20",
        "NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC": "0.05",
        "NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC": "1000.0",
        "NINA_HOVER_IMU_CORR_STEP_MIN_SEC": "0.005",
        "NINA_HOVER_TURN_MAX_STEPS": "40",
        "NINA_HOVER_TURN_TARGET_DEG": "90.0",
        # Disable the no-progress safeguard by default; specific tests
        # opt back in to exercise it.
        "NINA_HOVER_TURN_PROGRESS_CHECK_STEPS": "0",
        "NINA_HOVER_TURN_PROGRESS_MIN_DEG": "100.0",
        "NINA_HOVER_IMU_CORR_INVERT_SIGN": "0",
        # Keep the conventional label-to-geometry mapping for the
        # baseline direction / sign tests. Swap-enabled behaviour is
        # covered by its own test.
        "NINA_HOVER_TURN_SWAP_PIVOT_DIR": "0",
    }
    env.update(extras)
    return env


def _step_ticks_for_blend(blend: int = 20) -> tuple[int, int]:
    """Per-step goals produced by ``_goals_for_wheels`` at ``blend``%%.

    With the test axis the pivot pipeline picks the *computed* pivot
    goals (no per-corner overrides in ``_axis()``):
    ``hover_computed_turn_pivot_goals(turn_left=True)`` returns
    ``(1900, 2200)`` for an L=FWD R=BACK micro-step (left-pivot
    geometry); the mirror returns ``(2200, 1900)``. At ``u=blend/100``:
    left = brake + (target − brake) * u.

    Returns ``(pivot_left_goal, pivot_right_goal)`` for the LEFT servo.
    The right servo mirrors it around brake.
    """
    # Hard-coded for blend=20 to match the assertion arithmetic in the
    # IMU correction tests: brake=2048, target=1900/2200, u=0.2 →
    # 2048 + (-148*0.2) = 2018; 2048 + (152*0.2) = 2078.
    assert blend == 20, "helper currently only computes the 20% blend goals"
    return 2018, 2078


def _pivot_left_goals_20pct() -> dict[int, int]:
    """L=FWD, R=BACK at 20% blend → L=2018, R=2078 (yaw left)."""
    lg, rg = _step_ticks_for_blend(20)
    return {12: lg, 13: rg}


def _pivot_right_goals_20pct() -> dict[int, int]:
    """L=BACK, R=FWD at 20% blend → L=2078, R=2018 (yaw right)."""
    lg, rg = _step_ticks_for_blend(20)
    return {12: rg, 13: lg}


def _make_drive(yaw_fn=None, begin_fn=None, end_fn=None) -> HoverboardAxisDrive:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    if yaw_fn is not None or begin_fn is not None or end_fn is not None:
        drv.set_imu_hooks(
            yaw_drift_fn=yaw_fn,
            begin_straight_fn=begin_fn,
            end_straight_fn=end_fn,
        )
    return drv


class _FakeImuIntegrator:
    """Programmable yaw integrator for closed-loop turn tests.

    Three modes simulate the three IMU behaviours that matter to
    :meth:`HoverboardAxisDrive.pulse_turn_90`:

    * ``"converge"`` — accumulator progresses toward ``target_yaw_deg``
      by ``step_deg`` per :meth:`yaw_fn` call, then snaps to and holds
      the target. Models a healthy chassis rotating toward the
      requested heading; the loop should terminate inside the deadband
      and return ``True``.
    * ``"stuck"`` — accumulator never moves. Models a chassis that
      can't rotate (stuck wheel, no torque); the loop should bail at
      ``max_steps`` (or earlier if the no-progress safeguard is on)
      and return ``False``.
    * ``"none"`` — :meth:`yaw_fn` always returns ``None``. Models a
      paused integrator; the readiness poll should give up and the
      caller should fall back to the timed pivot. ``returns_none_for``
      caps how many leading ``None`` reads any mode emits before its
      real behaviour takes over (used to test the
      ``_poll_yaw_until_ready`` bridge).

    The :meth:`begin` / :meth:`end` counters let tests assert the
    integrator was bracketed cleanly even when ``pulse_turn_90``
    exits via a non-deadband path.
    """

    def __init__(
        self,
        *,
        mode: str = "converge",
        target_yaw_deg: float = 0.0,
        step_deg: float = 20.0,
        returns_none_for: int = 0,
    ) -> None:
        assert mode in ("converge", "stuck", "none"), mode
        self._mode = mode
        self._yaw = 0.0
        self._target = float(target_yaw_deg)
        self._step = abs(float(step_deg))
        self._none_left = int(returns_none_for)
        self.begin_calls = 0
        self.end_calls = 0
        self.samples: List[Optional[float]] = []

    def begin(self) -> None:
        self.begin_calls += 1
        self._yaw = 0.0

    def end(self) -> None:
        self.end_calls += 1

    def yaw_fn(self) -> Optional[float]:
        if self._none_left > 0:
            self._none_left -= 1
            self.samples.append(None)
            return None
        if self._mode == "none":
            self.samples.append(None)
            return None
        out = self._yaw
        self.samples.append(out)
        if self._mode == "converge":
            diff = self._target - self._yaw
            if abs(diff) <= self._step:
                self._yaw = self._target
            else:
                self._yaw += self._step if diff > 0 else -self._step
        return out


# ----------------------------------------------------------------------
# Goal-write inspection helpers
#
# ``DynamixelManager.initialize`` writes the brake pose before the test
# even starts (``apply_hoverboard_brake_positions``), and
# ``pulse_turn_90`` itself begins with a pre-brake apply, so the FIRST
# pivot lives at ``goal_writes[2]``. Tests assert on the first
# non-brake write rather than a fragile fixed index so the prefix is
# free to grow without breaking the contract.
# ----------------------------------------------------------------------

_BRAKE = {12: 2048, 13: 2048}


def _first_pivot_goal(goal_writes: List[dict[int, int]]) -> Optional[dict[int, int]]:
    """First write that isn't the brake pose, or ``None`` if no pivot fired."""
    for w in goal_writes:
        if w != _BRAKE:
            return w
    return None


# ----------------------------------------------------------------------
# Direction / geometry contract
# ----------------------------------------------------------------------


def test_right_turn_first_step_uses_pivot_right_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A "right" command must lean L=BACK / R=FWD on the very first
    micro-step (per the operator-facing convention: clockwise viewed
    from above). UI swapping the labels would otherwise silently
    invert the chassis rotation.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(os.environ, _fast_turn_env(), clear=False):
        drv.pulse_turn_90("right")
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_right_goals_20pct(), (
        f"first micro-step for right turn must use L=BACK/R=FWD geometry; "
        f"saw {pivot}"
    )


def test_left_turn_first_step_uses_pivot_left_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the right-turn contract: "left" → L=FWD / R=BACK."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=-90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(os.environ, _fast_turn_env(), clear=False):
        drv.pulse_turn_90("left")
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_left_goals_20pct(), (
        f"first micro-step for left turn must use L=FWD/R=BACK geometry; "
        f"saw {pivot}"
    )


# ----------------------------------------------------------------------
# Success / termination
# ----------------------------------------------------------------------


def test_right_turn_reaches_target_inside_deadband(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the IMU reports the chassis is rotating toward the target,
    the loop must terminate inside the deadband and return ``True``.
    The converging fake rotates 20°/step until it reaches +90° and
    then holds there, which is what a real chassis with damping looks
    like after the proportional micro-steps shrink.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(os.environ, _fast_turn_env(), clear=False):
        ok = drv.pulse_turn_90("right")
    assert ok is True, (
        "pulse_turn_90 must return True when it exits via the deadband"
    )


def test_left_turn_reaches_target_inside_deadband(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same as the right-turn success case, mirrored."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=-90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(os.environ, _fast_turn_env(), clear=False):
        ok = drv.pulse_turn_90("left")
    assert ok is True


def test_turn_bails_at_max_steps_when_imu_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the IMU reports the same yaw forever the loop must hit
    ``NINA_HOVER_TURN_MAX_STEPS``, return ``False``, and brake out.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(mode="stuck")
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(
            NINA_HOVER_TURN_MAX_STEPS="3",
            NINA_HOVER_TURN_PROGRESS_CHECK_STEPS="0",
        ),
        clear=False,
    ):
        ok = drv.pulse_turn_90("right")
    assert ok is False, (
        "pulse_turn_90 must return False when it exits via max-steps"
    )
    # Final write must be brake so the bot is left stationary.
    assert drv._dxl.goal_writes[-1] == _BRAKE


def test_turn_bails_on_no_progress_safeguard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the no-progress check is enabled, a stuck chassis must
    short-circuit at ``PROGRESS_CHECK_STEPS`` instead of burning all
    the max-steps budget on ineffective pulses.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(mode="stuck")
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(
            NINA_HOVER_TURN_MAX_STEPS="40",
            NINA_HOVER_TURN_PROGRESS_CHECK_STEPS="3",
            NINA_HOVER_TURN_PROGRESS_MIN_DEG="5.0",
        ),
        clear=False,
    ):
        ok = drv.pulse_turn_90("right")
    assert ok is False
    pivot_writes = [w for w in drv._dxl.goal_writes if w != _BRAKE]
    assert len(pivot_writes) < 20, (
        f"no-progress safeguard should have bailed long before MAX_STEPS; "
        f"saw {len(pivot_writes)} pivot writes"
    )


# ----------------------------------------------------------------------
# Sign / invert
# ----------------------------------------------------------------------


def test_turn_respects_invert_sign_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``NINA_HOVER_IMU_CORR_INVERT_SIGN=1`` flips both the integrator
    reading AND the target so the pivot decision stays correct. With
    invert on, a "right" command on an IMU that reports positive-for-
    left must still pivot the chassis correctly (first step geometry
    matches the requested chassis-frame direction).
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # IMU mount is flipped: it reports NEGATIVE numbers as the chassis
    # rotates right. The converging fake's "target_yaw_deg" is what
    # the IMU will report, so for a chassis-frame right turn under
    # invert=1 the IMU should converge to -90.
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=-90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_IMU_CORR_INVERT_SIGN="1"),
        clear=False,
    ):
        ok = drv.pulse_turn_90("right")
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_right_goals_20pct(), (
        f"first step under invert=1 must still use right-pivot geometry "
        f"in the chassis frame; saw {pivot}"
    )
    assert ok is True


# ----------------------------------------------------------------------
# IMU integrator bracketing
# ----------------------------------------------------------------------


def test_turn_brackets_with_imu_begin_end_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integrator must be zeroed at the start and ended on the way
    out — even when the turn exits early (max-steps, no-progress).
    Without this the next straight-leg drift sample would start with
    a stale yaw equal to ~90°, immediately triggering a bogus drift
    correction.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(mode="stuck")
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_TURN_MAX_STEPS="2"),
        clear=False,
    ):
        drv.pulse_turn_90("right")
    assert imu.begin_calls == 1, (
        f"begin_straight hook must fire exactly once per turn; "
        f"saw {imu.begin_calls}"
    )
    assert imu.end_calls == 1, (
        f"end_straight hook must fire exactly once per turn; "
        f"saw {imu.end_calls}"
    )


def test_turn_ends_imu_even_when_step_apply_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a Dynamixel write raises mid-turn, ``_imu_end_straight`` must
    still fire so the integrator is left in a clean state. The
    ``finally`` block in ``pulse_turn_90`` owns this.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    # Apply succeeds for the pre-brake, then blows up on every later
    # write. ``_apply_goals`` swallows the exception internally so the
    # loop continues; the test simply confirms the bracketing.
    original_apply = drv._apply_goals
    state = {"calls": 0}

    def flaky_apply(goals: dict[int, int]) -> None:
        state["calls"] += 1
        if state["calls"] >= 3:
            raise RuntimeError("dxl bus wedged")
        original_apply(goals)

    monkeypatch.setattr(drv, "_apply_goals", flaky_apply)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_TURN_MAX_STEPS="2"),
        clear=False,
    ):
        drv.pulse_turn_90("right")
    assert imu.end_calls == 1


# ----------------------------------------------------------------------
# Fallback paths
# ----------------------------------------------------------------------


def test_turn_falls_back_to_timed_when_no_imu_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``yaw_drift_fn`` wired → must call ``turn_left`` / ``turn_right``
    (the legacy timed pivot) instead of silently doing nothing.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    drv = _make_drive()  # no IMU hooks
    called = {"left": 0, "right": 0}
    monkeypatch.setattr(
        drv, "turn_right",
        lambda *a, **k: called.__setitem__("right", called["right"] + 1),
    )
    monkeypatch.setattr(
        drv, "turn_left",
        lambda *a, **k: called.__setitem__("left", called["left"] + 1),
    )
    ok = drv.pulse_turn_90("right")
    assert ok is False, "fallback path must report 'not a clean close'"
    assert called["right"] == 1 and called["left"] == 0


def test_turn_falls_back_when_imu_returns_none_throughout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused integrator (``yaw_fn`` keeps returning ``None``) must
    fall back to the timed pivot rather than spinning indefinitely
    inside the readiness poll.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(mode="none")
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    called = {"left": 0, "right": 0}
    monkeypatch.setattr(
        drv, "turn_left",
        lambda *a, **k: called.__setitem__("left", called["left"] + 1),
    )
    ok = drv.pulse_turn_90("left")
    assert ok is False
    assert called["left"] == 1
    # The integrator was still begun (we have to call begin before we
    # know whether it will produce samples) AND ended cleanly.
    assert imu.begin_calls == 1 and imu.end_calls == 1


def test_turn_recovers_from_transient_none_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brief integrator stall (a couple of None reads) followed by
    real samples must NOT fall back to the timed pivot — the
    ``_poll_yaw_until_ready`` helper covers exactly this case.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge",
        target_yaw_deg=90.0,
        step_deg=20.0,
        returns_none_for=3,
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    called = {"left": 0, "right": 0}
    monkeypatch.setattr(
        drv, "turn_left",
        lambda *a, **k: called.__setitem__("left", called["left"] + 1),
    )
    monkeypatch.setattr(
        drv, "turn_right",
        lambda *a, **k: called.__setitem__("right", called["right"] + 1),
    )
    ok = drv.pulse_turn_90("right")
    assert called == {"left": 0, "right": 0}, (
        "transient None samples must not trigger the timed fallback"
    )
    assert ok is True


# ----------------------------------------------------------------------
# Halts in-flight pulse series
# ----------------------------------------------------------------------


def test_turn_halts_inflight_pulse_series_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing "Turn left" while a forward pulse series is running
    must signal the pulse halt event so the two motion programs don't
    race. ``_halt_pulse_series`` owns the halt event manipulation.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    halted = {"count": 0}
    original_halt = drv._halt_pulse_series

    def spy_halt(*, wait: bool = True) -> None:
        halted["count"] += 1
        original_halt(wait=wait)

    monkeypatch.setattr(drv, "_halt_pulse_series", spy_halt)
    with patch.dict(os.environ, _fast_turn_env(), clear=False):
        drv.pulse_turn_90("right")
    assert halted["count"] >= 1, (
        "pulse_turn_90 must call _halt_pulse_series before starting"
    )


# ----------------------------------------------------------------------
# Misc safety
# ----------------------------------------------------------------------


def test_turn_unknown_direction_returns_false_without_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in the direction must not move the bot."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    pre_writes = len(drv._dxl.goal_writes)
    ok = drv.pulse_turn_90("sideways")
    assert ok is False
    assert len(drv._dxl.goal_writes) == pre_writes, (
        "unknown direction must not write any goals"
    )
    assert imu.begin_calls == 0 and imu.end_calls == 0


def test_turn_brakes_before_returning_even_on_max_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever the exit reason, the last write to the Dynamixel bus
    must be the brake pose so the bot is left stationary.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(mode="stuck")
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_TURN_MAX_STEPS="2"),
        clear=False,
    ):
        drv.pulse_turn_90("right")
    assert drv._dxl.goal_writes[-1] == _BRAKE


# ----------------------------------------------------------------------
# Env getters (defaults / clamps)
# ----------------------------------------------------------------------


def test_turn_env_getters_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh deploy (no env vars set) must report the operator-locked
    defaults so a typo upstream can't silently change the behaviour.
    """
    for var in (
        "NINA_HOVER_TURN_TARGET_DEG",
        "NINA_HOVER_TURN_MAX_STEPS",
        "NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC",
        "NINA_HOVER_TURN_STEP_BLEND_PCT",
        "NINA_HOVER_TURN_PRE_SETTLE_SEC",
        "NINA_HOVER_TURN_POST_SETTLE_SEC",
        "NINA_HOVER_TURN_PROGRESS_CHECK_STEPS",
        "NINA_HOVER_TURN_PROGRESS_MIN_DEG",
        "NINA_HOVER_TURN_SWAP_PIVOT_DIR",
        # Reuse-from-forward defaults shouldn't bleed test overrides in.
        "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT",
    ):
        monkeypatch.delenv(var, raising=False)
    assert _imu_turn_target_deg() == 90.0
    assert _imu_turn_max_steps() == 40
    # The two reuse-from-forward getters: defaults match the forward
    # IMU correction defaults (30 dps / 20 % blend).
    assert _imu_turn_step_rate_deg_per_sec() == 30.0
    assert _imu_turn_step_blend_pct() == 20
    assert _imu_turn_pre_settle_sec() == 0.20
    assert _imu_turn_post_settle_sec() == 0.30
    assert _imu_turn_progress_check_steps() == 6
    assert _imu_turn_progress_min_deg() == 3.0
    # Swap defaults to False after the fleet hall FWD/REV swap.
    # Operator sets NINA_HOVER_TURN_SWAP_PIVOT_DIR=1 if the mount
    # still needs the inverted label-to-goals mapping.
    assert _imu_turn_swap_pivot_dir() is False


def test_turn_env_overrides_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out-of-range overrides must be clamped, not crash."""
    monkeypatch.setenv("NINA_HOVER_TURN_TARGET_DEG", "9999")
    assert _imu_turn_target_deg() == 180.0
    monkeypatch.setenv("NINA_HOVER_TURN_TARGET_DEG", "-50")
    assert _imu_turn_target_deg() == 5.0
    monkeypatch.setenv("NINA_HOVER_TURN_TARGET_DEG", "garbage")
    assert _imu_turn_target_deg() == 90.0

    monkeypatch.setenv("NINA_HOVER_TURN_MAX_STEPS", "0")
    assert _imu_turn_max_steps() == 1
    monkeypatch.setenv("NINA_HOVER_TURN_MAX_STEPS", "9999")
    assert _imu_turn_max_steps() == 200

    monkeypatch.setenv("NINA_HOVER_TURN_STEP_BLEND_PCT", "999")
    assert _imu_turn_step_blend_pct() == 100
    monkeypatch.setenv("NINA_HOVER_TURN_STEP_BLEND_PCT", "0")
    assert _imu_turn_step_blend_pct() == 1


# ----------------------------------------------------------------------
# Swap-pivot: chassis-physics fix for the reference chassis
# ----------------------------------------------------------------------


def test_turn_swap_pivot_dir_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Common truthy / falsy spellings of the turn swap env var."""
    for falsy in ("0", "false", "FALSE", "no", "off", ""):
        monkeypatch.setenv("NINA_HOVER_TURN_SWAP_PIVOT_DIR", falsy)
        assert _imu_turn_swap_pivot_dir() is False, falsy
    for truthy in ("1", "true", "yes", "ON", "anything"):
        monkeypatch.setenv("NINA_HOVER_TURN_SWAP_PIVOT_DIR", truthy)
        assert _imu_turn_swap_pivot_dir() is True, truthy


def test_right_turn_under_swap_uses_pivot_left_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SWAP_PIVOT_DIR=1, a "right" command must apply the
    L=FWD/R=BACK goals (the field-tested mapping on the reference
    chassis where the conventional L=BACK,R=FWD mechanically rotates
    the bot to the **left**). This is the same physics fix the
    standstill drift correction relies on; the closed-loop turn
    couldn't take advantage of it before because it ran the
    conventional mapping unconditionally.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # On the swapped chassis the chassis-frame "right" turn rotates
    # the IMU NEGATIVELY (L=FWD/R=BACK rotates the swapped chassis
    # right, but the integrator measures yaw with the conventional
    # sign, so a chassis-frame right rotation accumulates the
    # opposite sign that target_intent expects)... wait — the
    # integrator's sign convention is also chassis-frame. So a
    # chassis-frame right rotation accumulates POSITIVE yaw exactly
    # like the un-swapped case. Use the same converge target as the
    # baseline test.
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_TURN_SWAP_PIVOT_DIR="1"),
        clear=False,
    ):
        drv.pulse_turn_90("right")
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    # Under swap=1, "right" applies the LEFT geometry.
    assert pivot == _pivot_left_goals_20pct(), (
        f"first step for 'right' under swap=1 must use the L=FWD/R=BACK "
        f"geometry (the inverted label mapping); saw {pivot}"
    )


def test_left_turn_under_swap_uses_pivot_right_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the swap-enabled right-turn contract: with swap=1, a
    "left" command applies the L=BACK/R=FWD geometry.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=-90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    with patch.dict(
        os.environ,
        _fast_turn_env(NINA_HOVER_TURN_SWAP_PIVOT_DIR="1"),
        clear=False,
    ):
        drv.pulse_turn_90("left")
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_right_goals_20pct(), (
        f"first step for 'left' under swap=1 must use the L=BACK/R=FWD "
        f"geometry (the inverted label mapping); saw {pivot}"
    )


def _timed_turn_env(**extras: str) -> dict[str, str]:
    """Env for the timed turn_left / turn_right tests.

    Forces the timed-pivot blend to 20% so the recorded Dynamixel
    goals match the ``_pivot_*_goals_20pct()`` helpers. The
    timed-turn path defaults to 17% via ``NINA_DRIVE_TURN_PIVOT_DEG=15``
    (15° of nominal 90°); ``18`` degrees lands exactly on 20%.
    """
    env = {
        "NINA_DRIVE_TURN_PIVOT_DEG": "18",
    }
    env.update(extras)
    return env


def test_timed_turn_left_honours_swap_pivot_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timed-pivot fallback :meth:`HoverboardAxisDrive.turn_left`
    must honour the same swap. Otherwise pressing Turn Left on a
    chassis whose IMU is paused (and falls back to the timed pivot)
    would still rotate the wrong direction.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    drv = _make_drive()
    with patch.dict(
        os.environ,
        _timed_turn_env(NINA_HOVER_TURN_SWAP_PIVOT_DIR="1"),
        clear=False,
    ):
        drv.turn_left(duration=0.0)
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    # Under swap=1, "turn_left" must mechanically rotate the
    # swapped chassis LEFT — that's the L=BACK, R=FWD geometry.
    assert pivot == _pivot_right_goals_20pct(), (
        f"timed turn_left under swap=1 must use the L=BACK/R=FWD "
        f"geometry; saw {pivot}"
    )


def test_timed_turn_right_honours_swap_pivot_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror for :meth:`HoverboardAxisDrive.turn_right` — under
    swap=1, a "turn right" request must apply the L=FWD/R=BACK
    geometry (the mechanical right-rotation on the swapped chassis).
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    drv = _make_drive()
    with patch.dict(
        os.environ,
        _timed_turn_env(NINA_HOVER_TURN_SWAP_PIVOT_DIR="1"),
        clear=False,
    ):
        drv.turn_right(duration=0.0)
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_left_goals_20pct(), (
        f"timed turn_right under swap=1 must use the L=FWD/R=BACK "
        f"geometry; saw {pivot}"
    )


def test_timed_turn_left_swap_zero_uses_conventional_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With swap=0, the timed fallback keeps the legacy mapping
    (turn_left → L=FWD, R=BACK). Required for chassis where the
    conventional mapping is correct.
    """
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    drv = _make_drive()
    with patch.dict(
        os.environ,
        _timed_turn_env(NINA_HOVER_TURN_SWAP_PIVOT_DIR="0"),
        clear=False,
    ):
        drv.turn_left(duration=0.0)
    pivot = _first_pivot_goal(drv._dxl.goal_writes)
    assert pivot == _pivot_left_goals_20pct()


# ----------------------------------------------------------------------
# Aim-at-zero target formula
# ----------------------------------------------------------------------


def test_turn_step_duration_aims_at_zero_not_half_deadband_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-step proportional duration must scale with |remaining|
    directly (aim-at-zero) — not with ``|remaining| - 0.5·deadband``
    as the old formula did. Concretely: when the remaining yaw is
    just past the deadband, the step duration should reflect the
    full remaining magnitude, not a deadband-shrunk surrogate that
    underdrives the chassis.

    Verified by recording the per-step ``time.sleep`` call: with
    deadband=10°, step_rate=10dps, step_min=0.001, step_dur_cap=10s,
    a stuck-at-12° chassis with target=24° produces
    remaining=12° → aim-at-zero step_dur = 12/10 = 1.20 s. The old
    "0.5·deadband short" formula would have produced
    (12 - 5)/10 = 0.70 s.
    """
    sleeps: List[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))

    class _StuckAt12:
        """IMU stub that reports +12° on every yaw_fn call.

        Can't use ``_FakeImuIntegrator(mode="stuck")`` directly because
        its ``begin`` hook resets the accumulator to 0; the closed-
        loop turn calls begin first, so the test would see remaining =
        target_intent − 0 = 24°, not 12°.
        """

        def __init__(self) -> None:
            self.begin_calls = 0
            self.end_calls = 0

        def begin(self) -> None:
            self.begin_calls += 1

        def end(self) -> None:
            self.end_calls += 1

        def yaw_fn(self) -> Optional[float]:
            return 12.0

    imu = _StuckAt12()
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    env = _fast_turn_env(
        NINA_HOVER_TURN_TARGET_DEG="24.0",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="10.0",
        NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC="10.0",
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="10.0",
        NINA_HOVER_IMU_CORR_STEP_MIN_SEC="0.001",
        NINA_HOVER_TURN_MAX_STEPS="1",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.pulse_turn_90("right")
    pivot_sleep = max(sleeps) if sleeps else 0.0
    assert pivot_sleep == pytest.approx(1.20, abs=1e-3), (
        f"aim-at-zero expects step_dur ≈ 1.20s for |remaining|=12 at "
        f"10dps; saw {pivot_sleep}. Did the formula regress to the "
        f"old 'land 0.5·deadband short' target?"
    )


# ----------------------------------------------------------------------
# Active settle integration
# ----------------------------------------------------------------------


def test_turn_uses_active_settle_when_yaw_rate_fn_wired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a yaw_rate_fn is wired, the per-step settle must come from
    ``_active_settle_until_still`` (which returns "settled" the
    moment the rate is below threshold), not from the fixed
    NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC timer.
    """
    sleeps: List[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))
    imu = _FakeImuIntegrator(
        mode="converge", target_yaw_deg=90.0, step_deg=20.0
    )
    drv = _make_drive(yaw_fn=imu.yaw_fn, begin_fn=imu.begin, end_fn=imu.end)
    # Constant near-zero yaw rate — active settle should return
    # "settled" on the first sample inside its stable window. Pass
    # the existing drift / begin / end hooks too because
    # ``set_imu_hooks`` is a full-replace (None resets).
    drv.set_imu_hooks(
        yaw_drift_fn=imu.yaw_fn,
        begin_straight_fn=imu.begin,
        end_straight_fn=imu.end,
        yaw_rate_fn=lambda: 0.0,
    )
    env = _fast_turn_env(
        # Make the legacy step settle large so we can detect whether
        # it was actually used: 1 s is way bigger than active settle
        # could ever take with a 0.0-dps rate.
        NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC="1.0",
        # Active settle envelope: snappy stable window so the test
        # doesn't hang on the default.
        NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS="0.5",
        NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC="0.001",
        NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC="0.5",
        NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC="0.001",
        NINA_HOVER_TURN_MAX_STEPS="3",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.pulse_turn_90("right")
    # The legacy 1.0 s step settle must not appear in the recorded
    # sleeps — the active settle path returns without firing it.
    legacy_settle_seen = any(
        abs(s - 1.0) < 1e-6 for s in sleeps
    )
    assert not legacy_settle_seen, (
        f"active settle should preempt the legacy fixed-timer step "
        f"settle when yaw_rate_fn is wired; saw sleeps={sleeps}"
    )
