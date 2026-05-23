"""Discrete IMU yaw-drift correction wired into ``HoverboardAxisDrive``.

The straight pulse series polls the IMU yaw integrator at ~20 Hz; when drift
crosses ``NINA_HOVER_IMU_CORR_THRESHOLD_DEG`` the controller stops the wheels,
runs an in-place pivot until drift returns inside
``NINA_HOVER_IMU_CORR_DEADBAND_DEG`` (or ``NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC``
elapses), brakes, re-primes the lean stack, and only then resumes the primed
forward / back pulse. Continuous asymmetric bias mid-pulse was intentionally
removed — the operator saw it as uncommanded fast turns.

These tests also cover the ``NINA_HOVER_POST_TURN_SETTLE_SEC`` knob that makes
the priming pause visible right after a Turn left/right → Straight sequence
(the original "feels like priming was skipped" complaint).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import patch

import pytest

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    _held_dpad_pivot_blend_pct,
)


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


def _fast_correction_env(**extras: str) -> dict[str, str]:
    """Tunables that make the iterative realign fast and easy to observe in tests.

    The hover module now does a chain of small ~1° pivots per correction
    (see :func:`HoverboardAxisDrive._perform_pivot_correction`); tests cap
    ``MAX_STEPS`` low and zero out the between-step settle so a stuck-drift
    realign returns in milliseconds. ``COOLDOWN_SEC`` is pinned to ``0`` so a
    single test can observe multiple corrections back-to-back where it needs
    to. Tests that exercise the cooldown itself override this.
    """
    env = {
        "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "3.0",
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT": "20",
        "NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC": "0.08",
        "NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_MAX_STEPS": "4",
        # Proportional step sizing is tested explicitly; default in the
        # shared env to a high rate + tiny floor so the proportional clamp
        # almost always picks the explicitly-configured PIVOT_MAX_SEC cap,
        # keeping legacy assertions stable.
        "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC": "1000.0",
        "NINA_HOVER_IMU_CORR_STEP_MIN_SEC": "0.005",
        # No-progress safeguard disabled by default in tests; the
        # progress-check test overrides it explicitly. Existing tests
        # assume MAX_STEPS or deadband control termination.
        "NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS": "0",
        "NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG": "100.0",
        # Bail warmup disabled in tests so wrong-direction sample streams
        # trigger the bail on the very first sample. The warmup-grace tests
        # override this explicitly.
        "NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS": "0",
        "NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_POLL_HZ": "60",
        # Drift-abort disabled by default in the shared test env. Several
        # legacy realign tests pump |drift| up to 30+ deg to exercise the
        # "stuck high" code paths, which would otherwise be short-circuited
        # by the 30 deg abort default that ships in production. The
        # dedicated abort tests in this file override this back to a small
        # positive value to exercise the safety stop explicitly.
        "NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG": "0",
        # In-motion yaw realign uses turn-step pacing (same as held D-pad).
        "NINA_HOVER_TURN_STEP_DUR_CAP_SEC": "0.08",
        "NINA_HOVER_TURN_STEP_MIN_SEC": "0.005",
        "NINA_HOVER_TURN_STEP_SETTLE_SEC": "0.0",
        "NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC": "1000.0",
        "NINA_HOVER_TURN_PRE_SETTLE_SEC": "0.0",
        "NINA_HOVER_TURN_POST_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_SWAP_PIVOT_DIR": "0",
    }
    env.update(extras)
    return env


def _pivot_left_goals_full() -> dict[int, int]:
    """Full-pivot goals for ``_goals_for_wheels(F, B)`` on the test axis."""
    from nina.controllers.hoverboard_axis_drive import hover_computed_turn_pivot_goals

    l, r = hover_computed_turn_pivot_goals(_axis(), turn_left=True)
    return {12: l, 13: r}


def _pivot_right_goals_full() -> dict[int, int]:
    """Full-pivot goals for ``_goals_for_wheels(B, F)`` on the test axis."""
    from nina.controllers.hoverboard_axis_drive import hover_computed_turn_pivot_goals

    l, r = hover_computed_turn_pivot_goals(_axis(), turn_left=False)
    return {12: l, 13: r}


def _pivot_goals_at_held_blend(*, turn_left: bool) -> dict[int, int]:
    """In-motion yaw realign goals at the same blend % as held D-pad L/R."""
    full = _pivot_left_goals_full() if turn_left else _pivot_right_goals_full()
    brk = {12: 2048, 13: 2048}
    u = _held_dpad_pivot_blend_pct() / 100.0
    return {
        12: int(round(brk[12] + (full[12] - brk[12]) * u)),
        13: int(round(brk[13] + (full[13] - brk[13]) * u)),
    }


def _counter_drift_goals_at_held_blend(
    *,
    drift_deg: float,
    invert: bool = False,
) -> dict[int, int]:
    """Physical counter-yaw goals for *drift_deg* (default swap=0 mount)."""
    from nina.controllers.hoverboard_axis_drive import _counter_drift_pivot_remaining_deg

    effective = -drift_deg if invert else drift_deg
    remaining = _counter_drift_pivot_remaining_deg(effective)
    return _pivot_goals_at_held_blend(turn_left=(remaining < 0.0))


# ----------------------------------------------------------------------
# Drift sampler plumbing
# ----------------------------------------------------------------------

def test_imu_sample_drift_returns_none_without_hook() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    assert drv._imu_sample_drift_deg() is None


def test_imu_sample_drift_swallows_sampler_exceptions() -> None:
    def boom() -> float:
        raise RuntimeError("imu wedged")

    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(yaw_drift_fn=boom)
    assert drv._imu_sample_drift_deg() is None


def test_imu_sample_drift_respects_disable_env() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ, {"NINA_HOVER_IMU_CORR_ENABLE": "0"}, clear=False
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)
    assert drv._imu_sample_drift_deg() is None


def test_imu_sample_drift_returns_value_when_wired() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(yaw_drift_fn=lambda: 4.2)
    assert drv._imu_sample_drift_deg() == 4.2


# ----------------------------------------------------------------------
# _imu_corrective_hold: no-correction paths
# ----------------------------------------------------------------------

def test_corrective_hold_no_hook_uses_plain_wait() -> None:
    """Without an IMU sampler the hold should behave like ``halt.wait``."""
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(drv._dxl.goal_writes)
    t0 = time.monotonic()
    assert drv._imu_corrective_hold(base, 0.05, halt, is_forward=True) is False
    assert time.monotonic() - t0 >= 0.04
    # plain wait → no extra goal writes
    assert len(drv._dxl.goal_writes) == pre


def test_corrective_hold_below_threshold_only_repplies_base_goals() -> None:
    """When drift stays inside the threshold the hold just keeps base lean."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.5)  # well below 3 deg threshold
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(dxl.goal_writes)
    assert drv._imu_corrective_hold(base, 0.06, halt, is_forward=True) is False
    after = dxl.goal_writes[pre:]
    # Every write inside the hold should equal base — no pivot writes appeared.
    assert after, "expected at least one base re-apply during the hold"
    for w in after:
        assert w == base, f"unexpected non-base write during quiet hold: {w}"


# ----------------------------------------------------------------------
# _imu_corrective_hold: pause-pivot-resume
# ----------------------------------------------------------------------

def test_corrective_hold_pivots_against_positive_drift() -> None:
    """Drift +5 → pause, pivot against drift (B,F at held blend), brake, re-prime."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.25, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    counter_drift = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    assert any(w == counter_drift for w in after), (
        f"expected counter-drift lean {counter_drift} in writes, saw {after}"
    )
    # Brake goals (2048, 2048) must appear between base and pivot.
    brake = {12: 2048, 13: 2048}
    assert any(w == brake for w in after)
    # Once the pivot completes the loop should resume the primed base lean.
    assert after[-1] == base, f"hold did not resume primed base lean; tail={after[-3:]}"


def test_corrective_hold_pivots_against_negative_drift() -> None:
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: -5.0)
    halt = threading.Event()
    base = {12: 1986, 13: 1986}  # ~primed backward
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.25, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    counter_drift = _counter_drift_goals_at_held_blend(drift_deg=-5.0)
    assert any(w == counter_drift for w in after), (
        f"expected counter-drift lean {counter_drift} in writes, saw {after}"
    )


def test_corrective_hold_exits_pivot_when_drift_returns_to_deadband() -> None:
    """Pivot should stop as soon as drift drops back inside the deadband."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # First call (trigger) returns +5 (above threshold); subsequent calls
    # return 0 so the closed-loop pivot exits immediately on the very first
    # poll inside the pivot.
    yaws = [5.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def sampler() -> float:
        return yaws.pop(0) if yaws else 0.0

    with patch.dict(
        os.environ,
        _fast_correction_env(NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.5"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre_writes = len(dxl.goal_writes)
    t0 = time.monotonic()
    drv._imu_corrective_hold(base, 0.4, halt, is_forward=True)
    elapsed = time.monotonic() - t0
    after = dxl.goal_writes[pre_writes:]
    # We capped pivot at 0.5s but the deadband should exit in well under 0.2s.
    # Total hold is 0.4s; ensure it took at least the hold duration but the
    # pivot itself was short (pivot_left only appears a small number of times).
    assert elapsed >= 0.35, "hold was cut short"
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    assert len(pivot_writes) <= 3, (
        f"expected closed-loop pivot to exit promptly; saw {len(pivot_writes)} pivot writes"
    )


def test_corrective_hold_pivot_capped_by_max_sec() -> None:
    """When drift never returns to deadband the pivot must still end by the cap."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.06"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 30.0)  # stuck high
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    t0 = time.monotonic()
    drv._imu_corrective_hold(base, 0.20, halt, is_forward=True)
    elapsed = time.monotonic() - t0
    # Hold ran for full 0.2s, but the pivot itself was capped at 0.06s and the
    # loop should have re-triggered another pivot on the next poll. Allow a
    # bit of slack for thread scheduling.
    assert elapsed >= 0.18, "hold returned early"


def test_corrective_hold_halt_during_pivot_aborts_promptly() -> None:
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="1.0"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 30.0)
    halt = threading.Event()

    def _later() -> None:
        time.sleep(0.04)
        halt.set()

    threading.Thread(target=_later, daemon=True).start()
    t0 = time.monotonic()
    aborted = drv._imu_corrective_hold({12: 2114, 13: 2114}, 5.0, halt, is_forward=True)
    elapsed = time.monotonic() - t0
    assert aborted is True
    assert elapsed < 0.5, "halt did not interrupt the pivot promptly"


# ----------------------------------------------------------------------
# set_wheels must NOT bias the symmetric-straight lean (no continuous turns)
# ----------------------------------------------------------------------

def test_set_wheels_does_not_apply_imu_bias_to_straight() -> None:
    """Continuous bias mid-motion was perceived as uncommanded turns. Gone."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)  # huge drift
    pre = len(dxl.goal_writes)
    drv.forward(10)  # symmetric straight
    after = dxl.goal_writes[pre:]
    expected = drv._goals_for_wheels(
        left_dir=drv.DIR_FORWARD,
        left_speed=10,
        right_dir=drv.DIR_FORWARD,
        right_speed=10,
    )
    last = after[-1]
    assert last == expected, (
        f"set_wheels symmetric straight should match FWD goals only; got {last}"
    )


# ----------------------------------------------------------------------
# Auto begin/end straight-leg hook plumbing (unchanged from prior tests)
# ----------------------------------------------------------------------

def test_set_wheels_calls_imu_begin_only_when_entering_straight() -> None:
    begins: List[int] = []
    ends: List[int] = []
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(
        yaw_drift_fn=lambda: 0.0,
        begin_straight_fn=lambda: begins.append(1),
        end_straight_fn=lambda: ends.append(1),
    )
    drv.forward(10)
    assert begins == [1]
    drv.forward(10)
    assert begins == [1]
    drv.backward(10)
    assert begins == [1, 1]
    assert ends == []
    drv.stop()
    assert ends == [1]


def test_pivot_after_straight_ends_then_resumes_on_next_straight() -> None:
    begins: List[int] = []
    ends: List[int] = []
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(
        yaw_drift_fn=lambda: 0.0,
        begin_straight_fn=lambda: begins.append(1),
        end_straight_fn=lambda: ends.append(1),
    )
    drv.forward(10)
    drv.set_wheels(
        left_dir=drv.DIR_FORWARD, left_speed=20,
        right_dir=drv.DIR_BACKWARD, right_speed=20,
    )
    assert ends == [1]
    drv.forward(10)
    assert begins == [1, 1]


# ----------------------------------------------------------------------
# Pulse-loop correction tests
# ----------------------------------------------------------------------
#
# Both the legacy in-motion forward test
# (``test_forward_pulse_main_hold_pivots_when_drift_exceeds_threshold``)
# and its backward twin
# (``test_backward_pulse_main_hold_pivots_when_drift_exceeds_threshold``)
# were removed when forward and now backward both switched from the
# legacy pulse-series + in-motion ``_imu_corrective_hold`` correction
# to the unified at-standstill ``_drift_correct_loop`` (with cumulative
# IMU tracking and micro-step pivot corrections between drive legs).
#
# Direction-of-correction and trigger behavior for both axes is now
# covered in :mod:`tests.test_hover_forward_pulse`:
# - ``test_forward_loop_positive_drift_pivots_left``
# - ``test_forward_loop_negative_drift_pivots_right``
# - ``test_forward_loop_invert_sign_flips_pivot_direction``
# - ``test_backward_loop_cumulative_drift_compounds_across_legs``
# - ``test_backward_loop_cycles_drive_then_brake``
#
# ``_imu_corrective_hold`` is still consumed by the 90° turn closed
# loop, which the tests below continue to validate.


def test_pulse_main_hold_skips_pivot_when_drift_is_quiet() -> None:
    """When drift stays inside the threshold the pulse loop must not pivot."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(
        dxl,
        threading.RLock(),
        replace(
            _axis(),
            pulse_series_max=1,
            pulse_series_back_sec=0.20,
            pulse_series_back_coast_initial_sec=0.0,
        ),
        _cfg(),
    )
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.5)  # well inside deadband

    drv.start_pulse_straight_backward(50)
    for _ in range(200):
        if not drv.is_forward_pulse_active():
            break
        time.sleep(0.02)
    drv.stop()
    pivot_left = _pivot_goals_at_held_blend(turn_left=True)
    pivot_right = _pivot_goals_at_held_blend(turn_left=False)
    for g in dxl.goal_writes:
        assert g != pivot_left and g != pivot_right, (
            f"unexpected pivot lean {g} while drift was quiet"
        )


# ----------------------------------------------------------------------
# Post-turn settle dwell after the straight prime
# ----------------------------------------------------------------------

def test_post_turn_settle_dwells_before_pulse_starts() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {"NINA_HOVER_POST_TURN_SETTLE_SEC": "0.15"},
        clear=False,
    ):
        t0 = time.monotonic()
        drv.start_pulse_straight_backward(50)
        elapsed = time.monotonic() - t0
    drv.stop()
    assert elapsed >= 0.12, (
        f"start_pulse_straight_backward returned in {elapsed:.3f}s; "
        f"expected to dwell at least ~0.15s for post-turn settle"
    )


def test_post_turn_settle_default_is_no_op() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    os.environ.pop("NINA_HOVER_POST_TURN_SETTLE_SEC", None)
    t0 = time.monotonic()
    drv.start_pulse_straight_backward(50)
    elapsed = time.monotonic() - t0
    drv.stop()
    assert elapsed < 0.10, (
        f"start_pulse_straight_backward should not pause by default; took {elapsed:.3f}s"
    )


def test_yaw_drift_none_falls_back_to_plain_wait() -> None:
    """A sampler returning None (calibrating / idle) must not trigger a pivot."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    sampler_calls: List[int] = []

    def sampler() -> Optional[float]:
        sampler_calls.append(1)
        return None

    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.05, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    pivot_left = _pivot_goals_at_held_blend(turn_left=True)
    pivot_right = _pivot_goals_at_held_blend(turn_left=False)
    assert all(w != pivot_left and w != pivot_right for w in after), (
        f"None-drift must not trigger any pivot lean; saw {after}"
    )
    assert sampler_calls, "sampler should still be consulted"


# ----------------------------------------------------------------------
# Cooldown: corrections must not fire back-to-back
# ----------------------------------------------------------------------

def test_cooldown_suppresses_back_to_back_pivots_in_single_hold() -> None:
    """One hold with stuck-high drift + long cooldown should realign ONCE only.

    The new iterative realign writes many pivot-left micro-steps per call,
    so this test counts ``_perform_pivot_correction`` invocations directly
    (the meaningful "correction event" unit) rather than counting goal-stream
    transitions.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_IMU_CORR_COOLDOWN_SEC="1.0",
            NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.02",
            NINA_HOVER_IMU_CORR_MAX_STEPS="2",
        ),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)  # stuck high
    halt = threading.Event()
    base = {12: 2114, 13: 2114}

    calls: list[float] = []
    original_pivot = drv._perform_pivot_correction

    def counting_pivot(
        drift: float,
        halt_ev: threading.Event,
        *,
        is_forward: bool = True,
    ) -> bool:
        calls.append(drift)
        return original_pivot(drift, halt_ev, is_forward=is_forward)

    with patch.object(drv, "_perform_pivot_correction", side_effect=counting_pivot):
        drv._imu_corrective_hold(base, 0.40, halt, is_forward=True)

    assert len(calls) == 1, (
        f"expected exactly one realign event with 1.0s cooldown; saw {len(calls)} "
        f"calls (drifts={calls!r})"
    )


def test_cooldown_persists_across_pulse_cycles() -> None:
    """Cooldown is instance-level → second pulse cycle inherits it."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_IMU_CORR_COOLDOWN_SEC="0.5",
            NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.04",
        ),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}

    # First hold triggers a pivot, then suppresses any more.
    drv._imu_corrective_hold(base, 0.15, halt, is_forward=True)
    pre_second = len(dxl.goal_writes)
    # Second hold starts immediately (no _imu_begin_straight in between) →
    # cooldown from the first pivot is still active and should suppress.
    drv._imu_corrective_hold(base, 0.10, halt, is_forward=True)
    after = dxl.goal_writes[pre_second:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    assert all(w != counter for w in after), (
        "cooldown from previous hold must carry into the next hold; "
        f"saw {after}"
    )


def test_imu_begin_straight_resets_cooldown() -> None:
    """A new straight leg must allow the first sample to fire immediately."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_IMU_CORR_COOLDOWN_SEC="10.0",
            NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.04",
        ),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}

    drv._imu_corrective_hold(base, 0.15, halt, is_forward=True)
    pre_second = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.05, halt, is_forward=True)
    after_before_reset = dxl.goal_writes[pre_second:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    assert all(w != counter for w in after_before_reset), (
        "before reset, cooldown should still block correction"
    )

    drv._imu_begin_straight()  # simulates entering a fresh straight leg

    pre_third = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.15, halt, is_forward=True)
    after_reset = dxl.goal_writes[pre_third:]
    assert any(w == counter for w in after_reset), (
        f"after _imu_begin_straight, the first sample must fire immediately "
        f"and trigger a pivot; saw {after_reset}"
    )


def test_default_tunables_are_conservative() -> None:
    """Smoke-test that the production defaults match the iterative profile.

    The iterative micro-step realign needs each step to be *visibly*
    authoritative on the MX-28 servos — 12 % × 0.10 s produced no
    perceptible rotation, so defaults are now 20 % × 0.18 s for ~1.5-2°
    per step. The bail logic gets a 3-step warmup so pre-brake angular
    momentum can't false-trip a sign-mismatch bail.
    """
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    for var in (
        "NINA_HOVER_IMU_CORR_THRESHOLD_DEG",
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT",
        "NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC",
        "NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC",
        "NINA_HOVER_IMU_CORR_STEP_MIN_SEC",
        "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC",
        "NINA_HOVER_IMU_CORR_MAX_STEPS",
        "NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS",
        "NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG",
        "NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS",
        "NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG",
        "NINA_HOVER_IMU_CORR_SETTLE_SEC",
        "NINA_HOVER_IMU_CORR_COOLDOWN_SEC",
        "NINA_HOVER_IMU_CORR_POLL_HZ",
    ):
        os.environ.pop(var, None)
    drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    assert drv._imu_corr_threshold_deg >= 3.0, "threshold should be calm by default"
    assert drv._imu_corr_pivot_blend_pct == 100
    assert 0.10 <= drv._imu_corr_pivot_max_sec <= 0.30, (
        "per-step duration cap should let the MX-28 actually slew to the goal"
    )
    assert drv._imu_corr_step_settle_sec <= 0.20, (
        "between-step settle should be brief by default"
    )
    assert 5 <= drv._imu_corr_max_steps <= 50, (
        "max-steps should give meaningful head-room without runaway loops"
    )
    assert drv._imu_corr_bail_warmup_steps >= 1, (
        "bail must wait for pre-brake angular momentum to bleed off"
    )
    assert drv._imu_corr_bail_margin_deg >= 2.0, (
        "bail margin must exceed typical per-step rotation to avoid false trips"
    )
    assert drv._imu_corr_step_min_sec >= 0.05, (
        "step min must exceed MX-28 slew latency floor"
    )
    assert drv._imu_corr_step_rate_dps >= 10.0, (
        "step rate calibration should match a real bench rotation rate"
    )
    assert drv._imu_corr_progress_check_steps >= 2, (
        "no-progress safeguard must be enabled by default"
    )
    assert drv._imu_corr_progress_min_deg > 0.0, (
        "progress threshold must be a positive degree value"
    )
    assert drv._imu_corr_settle_sec >= 0.20, "outer brake settle should be long by default"
    assert drv._imu_corr_cooldown_sec >= 0.5, "cooldown should be substantial by default"
    assert drv._imu_corr_poll_sec >= 1.0 / 10.0, "poll rate should be slow by default"
    assert drv._imu_corr_invert_sign is False, "invert flag must default off (per-bot opt-in)"


# ----------------------------------------------------------------------
# NINA_HOVER_IMU_CORR_INVERT_SIGN — per-bot pivot-direction flip
# ----------------------------------------------------------------------

def test_invert_sign_flips_pivot_direction_for_positive_drift() -> None:
    """With invert ON, drift +5 must use the opposite lean vs invert=0."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(NINA_HOVER_IMU_CORR_INVERT_SIGN="1")
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.25, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    no_invert = _counter_drift_goals_at_held_blend(drift_deg=5.0, invert=False)
    inverted = _counter_drift_goals_at_held_blend(drift_deg=5.0, invert=True)
    assert any(w == inverted for w in after), (
        f"invert=1 + drift=+5 should pivot opposite to invert=0, saw {after}"
    )
    assert not any(w == no_invert for w in after), (
        f"invert=1 + drift=+5 must NOT emit the invert=0 counter-drift lean, "
        f"saw {after}"
    )


def test_invert_sign_flips_pivot_direction_for_negative_drift() -> None:
    """With invert ON, drift -5 must use the opposite lean vs invert=0."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(NINA_HOVER_IMU_CORR_INVERT_SIGN="1")
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: -5.0)
    halt = threading.Event()
    base = {12: 1986, 13: 1986}
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.25, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    no_invert = _counter_drift_goals_at_held_blend(drift_deg=-5.0, invert=False)
    inverted = _counter_drift_goals_at_held_blend(drift_deg=-5.0, invert=True)
    assert any(w == inverted for w in after), (
        f"invert=1 + drift=-5 should pivot opposite to invert=0, saw {after}"
    )


def test_invert_sign_off_counters_positive_drift() -> None:
    """Default invert=off: positive drift → same lean as held D-pad left."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(NINA_HOVER_IMU_CORR_INVERT_SIGN="0")
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    pre = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.25, halt, is_forward=True)
    after = dxl.goal_writes[pre:]
    assert any(
        w == _counter_drift_goals_at_held_blend(drift_deg=5.0) for w in after
    ), "positive drift must pivot against drift (hold-right on swap=0 mount)"


# ----------------------------------------------------------------------
# Wrong-direction bail — defends against an un-flipped sign on a new chassis
# ----------------------------------------------------------------------

def test_pivot_bails_when_drift_grows_past_starting_magnitude() -> None:
    """Iterative realign must abort when drift grows past start on 2 samples.

    Mirrors the observed log on the real bot when the sign was wrong: drift
    started at +8.20° and grew on each subsequent micro-step. To avoid
    false-positive bails from a single noisy sample, the new code requires
    *two consecutive* samples beyond ``|initial_drift| + deadband`` before
    bailing. This test feeds exactly that pattern (samples 11.18° → 14.0°,
    both > 8.20 + 1.0) and asserts the realign stops well before its
    ``MAX_STEPS`` cap.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    yaws = [11.18, 14.0, 17.0, 20.0]  # all grow past 8.20 + 1.0

    def sampler() -> float:
        return yaws.pop(0) if yaws else 20.0

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.04",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="25",
        NINA_HOVER_IMU_CORR_POLL_HZ="200",
    )
    halt = threading.Event()
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
        pre = len(dxl.goal_writes)
        t0 = time.monotonic()
        drv._perform_pivot_correction(8.20, halt)
        elapsed = time.monotonic() - t0
        after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # Two consecutive growing samples must trigger the bail; only the first
    # 1-2 micro-steps may have fired before the bail short-circuits the loop.
    assert len(pivot_writes) <= 3, (
        f"wrong-direction bail must abort the realign quickly; saw "
        f"{len(pivot_writes)} counter-drift micro-step writes"
    )
    # Brake must appear after the bail so the wheels don't remain leaned in
    # the wrong direction (re-prime is the caller's responsibility).
    brake = {12: 2048, 13: 2048}
    assert any(w == brake for w in after), (
        "bail path must brake the pivot before returning to the caller"
    )
    # Sanity: did NOT burn the full max-steps × step_dur budget
    # (25 × 0.04s = 1.0s) going the wrong way.
    assert elapsed < 0.4, f"bail should be quick, elapsed={elapsed:.3f}s"


# ----------------------------------------------------------------------
# Iterative micro-step realign — exit on deadband, cap on max_steps,
# direction re-evaluated each step (replaces the single-shot pivot).
# ----------------------------------------------------------------------

def test_realign_exits_when_drift_reaches_deadband() -> None:
    """The loop must terminate as soon as |drift| dips inside the deadband.

    Feeds a decaying drift series: +6, +4, +2, +0.5 (deadband=1.0). The
    realign should fire ~3 micro-steps and exit cleanly without hitting
    the MAX_STEPS cap or the bail.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    yaws = [6.0, 4.0, 2.0, 0.5]  # last sample inside the 1.0° deadband

    def sampler() -> float:
        return yaws.pop(0) if yaws else 0.5

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.02",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="25",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)

    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(8.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # Initial drift +8 was used to TRIGGER the realign; the loop samples
    # +6 (act), +4 (act), +2 (act), +0.5 (exit). So ~3 actuated steps.
    assert 2 <= len(pivot_writes) <= 5, (
        f"realign should fire a small number of micro-steps before deadband "
        f"exits the loop; saw {len(pivot_writes)} counter-drift writes"
    )
    # Last write should be brake or prime-neutral (both are {12:2048,13:2048}),
    # never leave the wheels leaned.
    assert after[-1] == {12: 2048, 13: 2048}, (
        f"realign must leave wheels braked / re-primed; tail={after[-3:]!r}"
    )


def test_realign_caps_at_max_steps_under_stuck_drift() -> None:
    """Stuck drift (always above threshold, never inside deadband) must hit
    the ``MAX_STEPS`` safety cap — not loop forever.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="6",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)  # stuck above deadband, below bail
        pre = len(dxl.goal_writes)
        t0 = time.monotonic()
        drv._perform_pivot_correction(5.0, threading.Event())
        elapsed = time.monotonic() - t0
        after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # Constant +5° drift never trips the deadband, the overshoot guard, or
    # the wrong-direction bail (|5| is not > |initial 5| + deadband). Must
    # hit MAX_STEPS=6.
    assert pivot_writes == [counter] * 6, (
        f"stuck drift must fire exactly MAX_STEPS=6 micro-pivots; "
        f"saw {len(pivot_writes)} (writes={pivot_writes!r})"
    )
    # And the routine must return promptly (not block on something else).
    assert elapsed < 1.0, f"cap should bound the routine; elapsed={elapsed:.3f}s"


def test_warmup_grace_period_absorbs_pre_brake_momentum() -> None:
    """During warmup, drift growth must NOT trigger the wrong-direction bail.

    Simulates the real-bot scenario where the chassis still has angular
    momentum from the forward lean when the realign brakes: drift grows
    for the first few samples regardless of pivot direction. With warmup=3,
    those first 3 samples must be ignored by the bail logic. After warmup,
    if drift stops growing past the post-warmup baseline + margin, the
    realign should continue and eventually hit the max-steps cap (not bail).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # Mimic momentum-driven growth during warmup, then steady state. Once
    # warmup is over, samples hover around yaw_after_warmup so the bail
    # never trips.
    yaws = [
        9.0, 10.0, 11.0,  # warmup (steps 0-2): growth ignored
        11.2, 11.3, 11.2, 11.1,  # post-warmup: held inside +margin (5° default)
    ]

    def sampler() -> float:
        return yaws.pop(0) if yaws else 11.0

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="7",
        NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS="3",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="5.0",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)

    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(8.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # All 7 steps should run (no early bail) — drift in warmup ignored,
    # post-warmup samples stay inside the 5° margin so no bail trigger.
    assert len(pivot_writes) >= 5, (
        f"warmup must absorb momentum-driven growth and let the realign "
        f"run; saw only {len(pivot_writes)} counter-drift micro-step writes"
    )


def test_overcorrection_past_zero_exits_via_overshoot_not_wrong_direction_bail() -> None:
    """A correct-direction realign that overshoots zero must exit via the
    overshoot guard, NOT the wrong-direction bail.

    Regression: an earlier version of the bail compared ``|yaw_now|`` to
    ``|baseline| + margin``, which falsely fired when a correct pivot
    over-rotated the bot past zero (the magnitude grows after the sign
    flip, even though the pivot is doing exactly what it should). The fix
    is to require ``drift_deg * yaw_now > 0`` (same sign as the initial
    drift) before considering the bail.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # Initial drift +4. The realign aggressively rotates the bot: first
    # warmup sample crosses zero (+1 still inside warmup), then magnitudes
    # grow on the OPPOSITE sign (-5, -10, -15...). With the bail bug this
    # would trigger "wrong-direction bail"; with the fix it exits via
    # "over-shot zero" on the first negative sample past deadband.
    yaws = [1.0, -5.0, -10.0, -15.0, -20.0]

    def sampler() -> float:
        return yaws.pop(0) if yaws else -20.0

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="10",
        NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS="0",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="1.0",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(4.0, threading.Event())
    after = dxl.goal_writes[pre:]
    # Should exit on sample -5 (first sample whose sign differs from +4
    # and whose magnitude exceeds the 1° deadband). Only one micro-step
    # actuates before the next sample triggers the overshoot exit.
    pivot_writes = [
        w for w in after
        if w in (_pivot_goals_at_held_blend(turn_left=True), _pivot_goals_at_held_blend(turn_left=False))
    ]
    assert len(pivot_writes) <= 2, (
        f"over-shoot past zero must exit promptly via the overshoot guard, "
        f"not run until the wrong-direction bail; saw {len(pivot_writes)} "
        f"pivot writes"
    )


def test_bail_margin_must_be_exceeded_before_bail_fires() -> None:
    """A single noisy sample over the margin must NOT trigger the bail
    (needs two consecutive growths past the margin).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # warmup=0 → first sample becomes the post-warmup baseline.
    # Then one noisy spike past margin, then back inside margin, repeat.
    # No two consecutive samples both >baseline+margin, so no bail.
    yaws = [4.0, 11.0, 5.0, 11.0, 5.0, 11.0, 5.0]

    def sampler() -> float:
        return yaws.pop(0) if yaws else 5.0

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="7",
        NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS="0",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="3.0",  # baseline 4 + 3 = 7
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)

    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(3.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # No two-in-a-row growths past 7° → must reach max_steps=7, not bail.
    assert len(pivot_writes) == 7, (
        f"isolated noisy samples past margin must NOT bail the realign; "
        f"expected all 7 micro-steps, saw {len(pivot_writes)}"
    )


def test_realign_re_evaluates_direction_per_step() -> None:
    """If the drift sign flips mid-realign, the next micro-step must pivot
    the OTHER way instead of stubbornly continuing in the original direction.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # +6 → +3 → -3 (overshoot past zero past deadband → exits via overshoot guard)
    yaws = [6.0, 3.0, -3.0]

    def sampler() -> float:
        return yaws.pop(0) if yaws else -3.0

    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.02",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="25",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(7.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter_pos = _counter_drift_goals_at_held_blend(drift_deg=7.0)
    counter_neg = _counter_drift_goals_at_held_blend(drift_deg=-3.0)
    # Initial +7 drift -> first samples positive -> counter-drift (B,F) steps.
    # Then sample -3 triggers the overshoot guard and exits without
    # actuating a step for the opposite drift sign.
    assert any(w == counter_pos for w in after), (
        f"expected at least one counter-drift micro-step; saw {after}"
    )
    assert not any(w == counter_neg for w in after), (
        f"overshoot past zero must STOP the realign, not chase the other way; "
        f"saw counter_neg in {after}"
    )


# ----------------------------------------------------------------------
# Proportional step duration — small drifts → short pulses, big drifts
# → full PIVOT_MAX_SEC cap. Mirrors the user's "turn the robot in
# almost one degree at a time" spec.
# ----------------------------------------------------------------------

def test_proportional_step_uses_full_cap_for_large_drift() -> None:
    """When remaining drift is much larger than the cap × rate, the
    proportional step duration must saturate at ``PIVOT_MAX_SEC``.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # Big stuck drift; rate 30 dps × 0.18s = 5.4 deg per cap-duration step,
    # so a 30° drift demands far more than one step's worth → clamp to cap.
    env = _fast_correction_env(
        NINA_HOVER_TURN_STEP_DUR_CAP_SEC="0.18",
        NINA_HOVER_TURN_STEP_MIN_SEC="0.02",
        NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC="30.0",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="1",  # capture just the first step
    )
    halt = threading.Event()
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 30.0)
        t0 = time.monotonic()
        drv._perform_pivot_correction(30.0, halt)
        elapsed = time.monotonic() - t0
    # Outer settle = 0, step_settle = 0 (both pinned by _fast_correction_env).
    # One step of 0.18s for the cap + a tiny brake/settle should land at
    # ~0.18-0.20s total. Definitely > 0.10s and < 0.30s.
    assert 0.13 < elapsed < 0.30, (
        f"large drift must use the FULL PIVOT_MAX_SEC cap; elapsed={elapsed:.3f}s "
        f"(expected ~0.18s for one step at 0.18s cap)"
    )


def test_proportional_step_shrinks_for_small_drift() -> None:
    """A small initial drift should produce a SHORT first-step pulse,
    not the full ``PIVOT_MAX_SEC`` cap. Compares wall-clock of one step
    at small drift vs one step at large drift to prove the scaling.
    """
    drv_small = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv_small.initialize()
    drv_large = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv_large.initialize()

    # rate 30 dps. small=2° → 2/30 = 0.067s. large=30° → 0.18s (cap).
    base_env = {
        "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "0.5",
        "NINA_HOVER_TURN_STEP_DUR_CAP_SEC": "0.18",
        "NINA_HOVER_TURN_STEP_SETTLE_SEC": "0.0",
        "NINA_HOVER_TURN_STEP_MIN_SEC": "0.02",
        "NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC": "30.0",
        "NINA_HOVER_IMU_CORR_MAX_STEPS": "1",
        "NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS": "0",
        "NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG": "100.0",  # disable bail
        "NINA_HOVER_IMU_CORR_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_POLL_HZ": "60",
        "NINA_HOVER_TURN_PRE_SETTLE_SEC": "0.0",
        "NINA_HOVER_TURN_POST_SETTLE_SEC": "0.0",
    }
    with patch.dict(os.environ, base_env, clear=False):
        drv_small.set_imu_hooks(yaw_drift_fn=lambda: 2.0)
        drv_large.set_imu_hooks(yaw_drift_fn=lambda: 30.0)
        t0 = time.monotonic()
        drv_small._perform_pivot_correction(2.0, threading.Event())
        small_elapsed = time.monotonic() - t0
        t0 = time.monotonic()
        drv_large._perform_pivot_correction(30.0, threading.Event())
        large_elapsed = time.monotonic() - t0

    # Small drift step should be markedly shorter than large drift step.
    # large should be near the cap (~0.18s), small should be near 2/30 = 0.067s.
    assert large_elapsed > small_elapsed + 0.05, (
        f"small drift must produce a shorter step than large drift; "
        f"small={small_elapsed:.3f}s vs large={large_elapsed:.3f}s"
    )
    # And the small-drift step should be SHORTER than the cap.
    assert small_elapsed < 0.15, (
        f"small drift step should be well under PIVOT_MAX_SEC=0.18s; "
        f"actual={small_elapsed:.3f}s"
    )


def test_no_progress_exit_when_drift_held_at_start_value() -> None:
    """If the realign isn't reducing drift after ``PROGRESS_CHECK_STEPS``,
    it must exit cleanly with "no progress" rather than burn ``MAX_STEPS``.

    Mirrors the field-observed event where 15 ineffective micro-steps ran
    in ~3.6 s with drift held essentially constant — the cooldown + next
    event should get the fresh chance instead of us wasting that time.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="20",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="100.0",  # disable wrong-direction bail
        NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS="3",
        NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG="2.0",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)  # stuck at start value
    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(4.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    # Must exit at the progress-check step, not run all 20 max_steps.
    assert len(pivot_writes) <= 5, (
        f"no-progress safeguard must short-circuit the realign well "
        f"before MAX_STEPS=20; saw {len(pivot_writes)} pivot writes"
    )


def test_no_progress_check_disabled_by_setting_check_steps_zero() -> None:
    """Setting ``PROGRESS_CHECK_STEPS=0`` must disable the safeguard so the
    legacy "run until MAX_STEPS" behaviour is recoverable.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.01",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="6",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="100.0",
        NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS="0",  # disabled
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)  # stuck
    pre = len(dxl.goal_writes)
    drv._perform_pivot_correction(4.0, threading.Event())
    after = dxl.goal_writes[pre:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    pivot_writes = [w for w in after if w == counter]
    assert len(pivot_writes) == 6, (
        f"with progress-check disabled, stuck drift must run all "
        f"MAX_STEPS=6 micro-steps; saw {len(pivot_writes)}"
    )


def test_proportional_step_honours_min_sec_floor() -> None:
    """A tiny drift just above deadband must still produce a step of at
    least ``STEP_MIN_SEC`` (MX-28 slew floor), not a degenerate 0s pulse.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC="0.18",
        # rate so high that |drift|/rate would be <<min if not clamped.
        NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC="10000.0",
        NINA_HOVER_IMU_CORR_STEP_MIN_SEC="0.08",
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="0.5",
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="100.0",  # disable bail
        NINA_HOVER_IMU_CORR_MAX_STEPS="1",
    )
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 1.0)
    t0 = time.monotonic()
    drv._perform_pivot_correction(1.0, threading.Event())
    elapsed = time.monotonic() - t0
    # MIN_SEC=0.08 must be honoured even though 1/10000 ≈ 0s.
    assert elapsed > 0.06, (
        f"step duration must be clamped to STEP_MIN_SEC; elapsed={elapsed:.3f}s"
    )


# ----------------------------------------------------------------------
# Direction tagging: confirm forward vs backward shows up in logs.
# Operator was unable to tell from a grep whether IMU corrections were
# firing for the backward pulse loop at all; the (forward)/(backward)
# tag now makes the call site visible in every IMU log line emitted by
# the realign routine.
# ----------------------------------------------------------------------

@pytest.fixture
def _imu_corr_log_records(
    caplog: pytest.LogCaptureFixture,
) -> pytest.LogCaptureFixture:
    """Capture nina.hoverboard_axis INFO/WARNING during a single test."""
    caplog.set_level(logging.INFO, logger="nina.hoverboard_axis")
    return caplog


def test_pivot_correction_tags_forward_in_log(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """``is_forward=True`` (or default) emits ``hover IMU correction (forward):``."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    drv._perform_pivot_correction(5.0, threading.Event(), is_forward=True)
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (forward):" in log_text, (
        f"realign log lines must be tagged (forward); saw:\n{log_text}"
    )
    assert "hover IMU correction (backward):" not in log_text, (
        f"forward realign must NOT emit (backward) tag; saw:\n{log_text}"
    )


def test_pivot_correction_tags_backward_in_log(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """``is_forward=False`` emits ``hover IMU correction (backward):`` so the
    operator can confirm via ``grep`` that backward corrections actually
    fire (the original complaint was "changes don't apply for backward").
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    drv._perform_pivot_correction(5.0, threading.Event(), is_forward=False)
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (backward):" in log_text, (
        f"realign log lines must be tagged (backward); saw:\n{log_text}"
    )
    assert "hover IMU correction (forward):" not in log_text, (
        f"backward realign must NOT emit (forward) tag; saw:\n{log_text}"
    )


def test_corrective_hold_tags_cooldown_with_direction(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """The cooldown log line must also carry the direction tag so consecutive
    backward holds are distinguishable from forward holds in a tail -f.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(NINA_HOVER_IMU_CORR_COOLDOWN_SEC="0.05"),
        clear=False,
    ):
        # Drift 7.0 exceeds the new backward threshold default (6.0).
        drv.set_imu_hooks(yaw_drift_fn=lambda: 7.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    drv._imu_corrective_hold(base, 0.30, halt, is_forward=False)
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (backward): cooldown" in log_text, (
        f"cooldown log line must include the (backward) tag; saw:\n{log_text}"
    )


# ----------------------------------------------------------------------
# Backward-direction overrides
#
# On a typical hoverboard chassis the natural yaw rate while reversing is
# 1-2 orders of magnitude higher than while going forward, so the forward
# cooldown / threshold / step-rate calibration produces a visible "snake"
# pattern on the backward leg. The NINA_HOVER_IMU_CORR_BACK_* env vars
# let backward be tuned more aggressively without disturbing forward.
#
# THRESHOLD and STEP_RATE ship with their own backward-optimised defaults
# (6.0 deg and 60.0 dps respectively, baked in after field testing on the
# hoverboard chassis — see "Option B" in the module docstring). COOLDOWN
# still inherits from forward because the forward 1.0 s default works for
# both directions in field testing.
# ----------------------------------------------------------------------

def test_back_overrides_use_backward_optimised_defaults_when_unset() -> None:
    """Unset BACK_* env vars must populate backward attrs with their
    backward-optimised defaults (THRESHOLD=6.0, STEP_RATE=60.0) rather
    than blindly inheriting from forward. COOLDOWN still inherits from
    forward because the forward 1.0 s also works for backward.
    """
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "4.0",
            "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "1.0",
            "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC": "30.0",
            # BACK_* deliberately unset.
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    # Forward unchanged
    assert drv._imu_corr_threshold_deg == 4.0
    assert drv._imu_corr_cooldown_sec == 1.0
    assert drv._imu_corr_step_rate_dps == 30.0
    # Backward defaults: THRESHOLD and STEP_RATE diverge from forward;
    # COOLDOWN still inherits.
    assert drv._imu_corr_back_threshold_deg == 6.0, (
        "BACK_THRESHOLD default must be 6.0 (backward-optimised), not the "
        "forward 4.0 — the forward value fires spuriously on the chassis "
        "natural ~+8 deg backward rest pose"
    )
    assert drv._imu_corr_back_cooldown_sec == drv._imu_corr_cooldown_sec == 1.0
    assert drv._imu_corr_back_step_rate_dps == 60.0, (
        "BACK_STEP_RATE default must be 60.0 (backward-optimised), not the "
        "forward 30.0 — backward pivots on this chassis are physically "
        "more authoritative and the forward calibration over-shoots"
    )


def test_back_overrides_load_explicit_env_values() -> None:
    """When set, BACK_* env vars must populate the dedicated backward attrs
    while leaving the forward attrs untouched.
    """
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "4.0",
            "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "1.0",
            "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC": "30.0",
            "NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG": "3.0",
            "NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC": "0.3",
            "NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC": "60.0",
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    # Forward attrs unchanged
    assert drv._imu_corr_threshold_deg == 4.0
    assert drv._imu_corr_cooldown_sec == 1.0
    assert drv._imu_corr_step_rate_dps == 30.0
    # Backward attrs from explicit overrides
    assert drv._imu_corr_back_threshold_deg == 3.0
    assert drv._imu_corr_back_cooldown_sec == 0.3
    assert drv._imu_corr_back_step_rate_dps == 60.0


def test_back_overrides_invalid_env_falls_back_to_helper_default() -> None:
    """Garbage BACK_* values must NOT crash the controller — they fall back
    to the value the call site passed to each helper.

    For THRESHOLD and STEP_RATE the call site passes the backward-optimised
    constants (6.0 and 60.0); for COOLDOWN it still passes the forward
    cooldown value. The fallback for "" (empty) and unparseable strings
    is the same as for "unset".
    """
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "4.0",
            "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "1.0",
            "NINA_HOVER_IMU_CORR_STEP_RATE_DEG_PER_SEC": "30.0",
            "NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG": "not-a-number",
            "NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC": "",
            "NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC": "garbage",
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    # THRESHOLD and STEP_RATE fall back to backward-optimised defaults.
    assert drv._imu_corr_back_threshold_deg == 6.0
    assert drv._imu_corr_back_step_rate_dps == 60.0
    # COOLDOWN still inherits from forward.
    assert drv._imu_corr_back_cooldown_sec == 1.0


def test_corrective_hold_uses_back_threshold_for_backward() -> None:
    """A drift level that's *below* the forward threshold but *above* the
    backward threshold must fire only on backward, not on forward.
    """
    dxl_fwd = FakeDxl()
    drv_fwd = HoverboardAxisDrive(dxl_fwd, threading.RLock(), _axis(), _cfg())
    drv_fwd.initialize()
    dxl_back = FakeDxl()
    drv_back = HoverboardAxisDrive(dxl_back, threading.RLock(), _axis(), _cfg())
    drv_back.initialize()
    env = _fast_correction_env(
        # Forward threshold high, backward threshold low.
        NINA_HOVER_IMU_CORR_THRESHOLD_DEG="10.0",
        NINA_HOVER_IMU_CORR_BACK_THRESHOLD_DEG="2.0",
        # Cooldown 0 + quick correction so the test doesn't drag.
        NINA_HOVER_IMU_CORR_COOLDOWN_SEC="0.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="1",
        # Sampler reports a drift right between the two thresholds.
        # (set later via set_imu_hooks per-drive)
    )
    drift = 5.0  # > 2.0 back, < 10.0 fwd
    with patch.dict(os.environ, env, clear=False):
        drv_fwd.set_imu_hooks(yaw_drift_fn=lambda: drift)
        drv_back.set_imu_hooks(yaw_drift_fn=lambda: drift)
    base = {12: 2114, 13: 2114}
    halt = threading.Event()

    pre_fwd = len(dxl_fwd.goal_writes)
    drv_fwd._imu_corrective_hold(base, 0.10, halt, is_forward=True)
    fwd_writes = dxl_fwd.goal_writes[pre_fwd:]
    counter = _counter_drift_goals_at_held_blend(drift_deg=5.0)
    fwd_pivot_writes = [w for w in fwd_writes if w == counter]
    assert fwd_pivot_writes == [], (
        f"drift {drift} < forward threshold 10.0 must NOT pivot; saw "
        f"{len(fwd_pivot_writes)} pivot writes"
    )

    pre_back = len(dxl_back.goal_writes)
    drv_back._imu_corrective_hold(base, 0.10, halt, is_forward=False)
    back_writes = dxl_back.goal_writes[pre_back:]
    back_pivot_writes = [w for w in back_writes if w == counter]
    assert len(back_pivot_writes) >= 1, (
        f"drift {drift} > backward threshold 2.0 must fire pivot; saw "
        f"{len(back_pivot_writes)} pivot writes"
    )


def test_corrective_hold_applies_back_cooldown_separately(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """The cooldown log line must report the backward cooldown when the
    hold ran in backward mode (and the forward cooldown when forward).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_IMU_CORR_COOLDOWN_SEC="1.50",
            NINA_HOVER_IMU_CORR_BACK_COOLDOWN_SEC="0.25",
        ),
        clear=False,
    ):
        # Drift 7.0 exceeds both the forward (3.0) and the new backward
        # (6.0) default thresholds so the realign actually fires.
        drv.set_imu_hooks(yaw_drift_fn=lambda: 7.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    drv._imu_corrective_hold(base, 0.10, halt, is_forward=False)
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (backward): cooldown 0.25s" in log_text, (
        f"backward hold must log backward cooldown (0.25s); saw:\n{log_text}"
    )
    assert "cooldown 1.50s" not in log_text, (
        f"backward hold must NOT log the forward cooldown; saw:\n{log_text}"
    )


def _realign_banner_messages(
    caplog: pytest.LogCaptureFixture,
) -> List[str]:
    """Return only the per-correction realign-banner messages from caplog.

    The ``hoverboard IMU hooks: ...`` startup banner also references
    ``back_step_rate`` when the override is set, which would otherwise
    contaminate "step_rate=X not in log_text" assertions. Filter to the
    realign-banner lines (the ones that include ``drift=`` and
    ``iterative realign``) so the assertions are scoped to the actual
    correction event.
    """
    return [
        r.getMessage()
        for r in caplog.records
        if "hover IMU correction (" in r.getMessage()
        and "iterative realign" in r.getMessage()
    ]


def test_perform_pivot_correction_uses_turn_step_rate_for_backward(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """In-motion backward yaw realign uses the same turn step rate as D-pad."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC="33.0",
            NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC="90.0",
            NINA_HOVER_IMU_CORR_MAX_STEPS="1",
        ),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
        drv._perform_pivot_correction(5.0, threading.Event(), is_forward=False)
    banners = _realign_banner_messages(_imu_corr_log_records)
    assert banners, "expected at least one realign banner line"
    joined = "\n".join(banners)
    assert "step_rate=33.0dps" in joined, (
        f"backward realign must log turn step_rate (33.0); saw:\n{joined}"
    )
    assert "step_rate=90.0dps" not in joined, (
        f"legacy BACK_STEP_RATE must not apply to in-motion realign; saw:\n{joined}"
    )


def test_perform_pivot_correction_uses_turn_step_rate_for_forward(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """In-motion forward yaw realign uses turn step rate (not BACK_STEP_RATE)."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _fast_correction_env(
            NINA_HOVER_TURN_STEP_RATE_DEG_PER_SEC="33.0",
            NINA_HOVER_IMU_CORR_BACK_STEP_RATE_DEG_PER_SEC="90.0",
            NINA_HOVER_IMU_CORR_MAX_STEPS="1",
        ),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
        drv._perform_pivot_correction(5.0, threading.Event(), is_forward=True)
    banners = _realign_banner_messages(_imu_corr_log_records)
    assert banners, "expected at least one realign banner line"
    joined = "\n".join(banners)
    assert "step_rate=33.0dps" in joined, (
        f"forward realign must log turn step_rate (33.0); saw:\n{joined}"
    )
    assert "step_rate=90.0dps" not in joined, (
        f"legacy BACK_STEP_RATE must not apply to in-motion realign; saw:\n{joined}"
    )


# ----------------------------------------------------------------------
# Stuck-detector tests
#
# When the pivot lean can't rotate the chassis (typically because the
# bot is stationary and friction is holding the wheels), the realign
# exits as "no progress" and the regular 0.3-1.0 s cooldown immediately
# fires another, equally-doomed realign. The detector counts consecutive
# no-progress exits and swaps in a much longer cooldown so the pulse
# loop gets an uninterrupted window to drive the wheels and break the
# chassis free. Any non-no-progress exit resets the streak.
# ----------------------------------------------------------------------

def _stuck_env(
    streak: str = "2",
    stuck_cooldown: str = "0.40",
    regular_cooldown: str = "0.05",
    **extras: str,
) -> dict[str, str]:
    """Test-friendly env: low streak threshold + short cooldowns + fast
    realign settings so the detector can be exercised in milliseconds.
    """
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_COOLDOWN_SEC=regular_cooldown,
        NINA_HOVER_IMU_CORR_STUCK_STREAK=streak,
        NINA_HOVER_IMU_CORR_STUCK_COOLDOWN_SEC=stuck_cooldown,
        NINA_HOVER_IMU_CORR_DEADBAND_DEG="1.0",
        NINA_HOVER_IMU_CORR_MAX_STEPS="6",
        # progress check fires fast so "no progress" exit is reachable
        NINA_HOVER_IMU_CORR_PROGRESS_CHECK_STEPS="2",
        NINA_HOVER_IMU_CORR_PROGRESS_MIN_DEG="100.0",
        # Disable wrong-direction bail so a stuck-drift sampler exits
        # via "no progress" rather than the bail.
        NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG="100.0",
    )
    env.update(extras)
    return env


def test_stuck_streak_increments_on_no_progress_exit() -> None:
    """A realign that exits as ``no progress`` must increment the streak."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _stuck_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)  # stuck drift
    assert drv._imu_corr_no_progress_streak == 0
    drv._perform_pivot_correction(4.0, threading.Event())
    assert drv._imu_corr_no_progress_streak == 1, (
        "no-progress exit must bump the streak by 1"
    )
    drv._perform_pivot_correction(4.0, threading.Event())
    assert drv._imu_corr_no_progress_streak == 2, (
        "second consecutive no-progress must bump the streak again"
    )


def test_stuck_streak_resets_on_deadband_exit() -> None:
    """Any non-no-progress exit (deadband / overshoot / bail / max-cap)
    must reset the streak to 0 so a single successful realign clears
    a prior stuck state.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _stuck_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)
    # Manually inflate the streak.
    drv._imu_corr_no_progress_streak = 5
    # Switch sampler to one that returns 0.5 — well below deadband, so the
    # very first sample triggers a "deadband reached" exit.
    drv._imu_yaw_drift_fn = lambda: 0.5
    drv._perform_pivot_correction(4.0, threading.Event())
    assert drv._imu_corr_no_progress_streak == 0, (
        "deadband exit must reset the streak to 0"
    )


def test_stuck_streak_resets_on_overshoot_exit() -> None:
    """An overshoot-past-zero exit must reset the streak (same reason as
    deadband: the pivots ARE producing rotation, just slightly too much).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _stuck_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: -4.0)  # opposite sign of initial
    drv._imu_corr_no_progress_streak = 5
    # Initial drift positive; sampler returns negative past deadband
    # → over-shot zero exit.
    drv._perform_pivot_correction(4.0, threading.Event())
    assert drv._imu_corr_no_progress_streak == 0


def test_imu_begin_straight_resets_stuck_streak() -> None:
    """A fresh straight leg must clear any stuck state from the previous leg."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _stuck_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    drv._imu_corr_no_progress_streak = 5
    drv._imu_begin_straight()
    assert drv._imu_corr_no_progress_streak == 0


def test_stuck_cooldown_kicks_in_after_streak_threshold(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """After ``stuck_streak`` consecutive no-progress events the next
    cooldown log must report the long stuck cooldown, not the regular one.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _stuck_env(streak="2", stuck_cooldown="0.40", regular_cooldown="0.05"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)
    # Inflate streak past the threshold; the next realign + cooldown should
    # report the stuck cooldown.
    drv._imu_corr_no_progress_streak = 2
    base = {12: 2114, 13: 2114}
    halt = threading.Event()
    # Hold just long enough for ONE realign + ONE cooldown log to fire.
    drv._imu_corrective_hold(base, 0.10, halt, is_forward=True)
    cooldown_lines = [
        r.getMessage()
        for r in _imu_corr_log_records.records
        if "cooldown" in r.getMessage()
        and "primed-only motion" in r.getMessage()
        or "stuck detected" in r.getMessage()
    ]
    joined = "\n".join(cooldown_lines)
    assert "stuck detected" in joined, (
        f"streak >= threshold must log stuck-detected warning; saw:\n{joined}"
    )
    assert "0.40s" in joined, (
        f"stuck cooldown line must report the 0.40s value; saw:\n{joined}"
    )
    assert "0.05s" not in joined, (
        f"stuck cooldown must override the regular 0.05s; saw:\n{joined}"
    )


def test_stuck_cooldown_not_applied_below_streak_threshold(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """If the streak is below the threshold the regular (direction-aware)
    cooldown must still apply — single transient no-progress events
    should NOT trigger the long stuck cooldown.

    Sets STUCK_STREAK very high so multiple realigns may fit in the
    test's hold window without ever crossing the stuck threshold (the
    inner ``_imu_corrective_hold`` loop is otherwise free to fire 2-3
    realigns in 100 ms once cooldown drops below ~30 ms).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _stuck_env(streak="50", stuck_cooldown="5.00", regular_cooldown="0.07"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)
    # Streak well below threshold (50): must use regular cooldown even
    # if the hold runs through several no-progress events.
    drv._imu_corr_no_progress_streak = 1
    base = {12: 2114, 13: 2114}
    drv._imu_corrective_hold(base, 0.10, threading.Event(), is_forward=True)
    log_text = _imu_corr_log_records.text
    assert "cooldown 0.07s" in log_text, (
        f"streak below threshold must use regular cooldown 0.07s; "
        f"saw:\n{log_text}"
    )
    assert "stuck detected" not in log_text, (
        f"streak below threshold must NOT log stuck-detected warning; "
        f"saw:\n{log_text}"
    )


def test_stuck_streak_zero_disables_detector(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """Setting STUCK_STREAK=0 must completely disable the detector — even
    an infinite no-progress streak must use the regular cooldown.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _stuck_env(streak="0", stuck_cooldown="5.00", regular_cooldown="0.04"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)
    drv._imu_corr_no_progress_streak = 999
    base = {12: 2114, 13: 2114}
    drv._imu_corrective_hold(base, 0.08, threading.Event(), is_forward=True)
    log_text = _imu_corr_log_records.text
    assert "stuck detected" not in log_text, (
        f"STREAK=0 must disable detector entirely; saw:\n{log_text}"
    )
    assert "cooldown 0.04s" in log_text, (
        f"STREAK=0 must keep using regular cooldown; saw:\n{log_text}"
    )


def test_stuck_cooldown_clears_after_successful_realign(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: a stuck streak must be broken by the first successful
    realign so the regular cooldown returns immediately. This is what
    lets the bot recover automatically once it's moving again.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        _stuck_env(streak="2", stuck_cooldown="5.00", regular_cooldown="0.06"),
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 4.0)
    # Manually mark the bot as currently stuck.
    drv._imu_corr_no_progress_streak = 5
    # Now switch the sampler to one that returns inside the deadband,
    # forcing the very next realign to exit cleanly via "deadband reached".
    drv._imu_yaw_drift_fn = lambda: 0.4
    drv._perform_pivot_correction(4.0, threading.Event())
    assert drv._imu_corr_no_progress_streak == 0, (
        "successful realign must clear the streak so subsequent corrections "
        "go back to the regular cooldown without operator intervention"
    )


# ----------------------------------------------------------------------
# Drift-abort tests
#
# Above ``NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG`` (default 30.0) the
# iterative realign cannot recover within one cycle; ``_imu_corrective_hold``
# must brake, set the pulse halt event, and queue a single TTS
# announcement so the operator knows the leg ended. Fires on both the
# forward and backward pulse loops. ``ABORT_DRIFT_DEG=0`` disables the
# safety stop entirely (legacy "keep retrying" behaviour).
# ----------------------------------------------------------------------


def _abort_env(
    abort_deg: str = "30.0",
    **extras: str,
) -> dict[str, str]:
    """Test env: high MAX_STEPS / disabled stuck detector so the abort path
    isn't masked by other safeguards.
    """
    env = _fast_correction_env(
        NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG=abort_deg,
        # Disable the stuck detector so cooldown logs don't mix in.
        NINA_HOVER_IMU_CORR_STUCK_STREAK="0",
        # Make sure the abort fires on the FIRST sample, before any
        # correction logic decides to bail out for other reasons.
        NINA_HOVER_IMU_CORR_POLL_HZ="100",
    )
    env.update(extras)
    return env


def test_imu_corr_abort_drift_deg_default_is_30() -> None:
    """A fresh deploy with no env override must use the 30 deg abort default."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    env_without_abort = {
        k: v
        for k, v in _fast_correction_env().items()
        if k != "NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG"
    }
    with patch.dict(os.environ, env_without_abort, clear=False):
        os.environ.pop("NINA_HOVER_IMU_CORR_ABORT_DRIFT_DEG", None)
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    assert drv._imu_corr_abort_drift_deg == pytest.approx(30.0), (
        "fresh deploy must default the drift-abort threshold to 30.0 deg"
    )


def test_drift_abort_fires_for_forward_and_invokes_tts(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """|drift| >= ABORT_DRIFT_DEG must halt the forward pulse leg and
    queue exactly one TTS announcement with the canonical phrase.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _abort_env(abort_deg="10.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 25.0)  # well past 10 deg abort
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    with patch(
        "nina.controllers.hoverboard_axis_drive.maybe_speak_cant_move_alert"
    ) as speak:
        ret = drv._imu_corrective_hold(base, 0.20, halt, is_forward=True)
    assert ret is True, "abort must return True so the pulse loop bails"
    assert halt.is_set(), "abort must set the pulse halt event"
    assert speak.call_count == 1, (
        f"abort must speak exactly once, saw {speak.call_count} calls"
    )
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (forward):" in log_text
    assert "ABORT threshold" in log_text, (
        f"abort must emit a WARNING log line; saw:\n{log_text}"
    )
    # Brake goals (2048, 2048) must appear in the writes so the bot stops
    # cleanly before the pulse loop bails.
    brake = {12: 2048, 13: 2048}
    assert any(w == brake for w in dxl.goal_writes), (
        f"abort must brake before halting; saw writes={dxl.goal_writes}"
    )


def test_drift_abort_fires_for_backward(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """Backward pulse leg must abort the same way (drift is in world frame)."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _abort_env(abort_deg="15.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: -40.0)  # opposite sign, big magnitude
    halt = threading.Event()
    base = {12: 1986, 13: 1986}  # primed backward
    with patch(
        "nina.controllers.hoverboard_axis_drive.maybe_speak_cant_move_alert"
    ) as speak:
        ret = drv._imu_corrective_hold(base, 0.20, halt, is_forward=False)
    assert ret is True
    assert halt.is_set()
    assert speak.call_count == 1
    log_text = _imu_corr_log_records.text
    assert "hover IMU correction (backward):" in log_text
    assert "ABORT threshold" in log_text


def test_drift_abort_does_not_fire_below_threshold(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """Drift smaller than the abort threshold must let the normal
    correction path run (no halt, no TTS, no ABORT log).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # Drift is +5° (above normal 3° correction threshold from _fast_correction_env
    # but well below the 20° abort threshold).
    with patch.dict(os.environ, _abort_env(abort_deg="20.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    with patch(
        "nina.controllers.hoverboard_axis_drive.maybe_speak_cant_move_alert"
    ) as speak:
        drv._imu_corrective_hold(base, 0.20, halt, is_forward=True)
    assert speak.call_count == 0, (
        "drift below abort threshold must not invoke TTS"
    )
    assert not halt.is_set(), (
        "drift below abort threshold must not set the pulse halt event"
    )
    log_text = _imu_corr_log_records.text
    assert "ABORT threshold" not in log_text, (
        f"sub-threshold drift must not log the abort line; saw:\n{log_text}"
    )


def test_drift_abort_disabled_when_zero(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """``ABORT_DRIFT_DEG=0`` must disable the safety stop entirely so even
    catastrophic drift falls through to the legacy realign behaviour
    (existing operators relying on never-aborting workflows aren't
    affected by the new default).
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _abort_env(abort_deg="0.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 250.0)
    halt = threading.Event()
    base = {12: 2114, 13: 2114}
    with patch(
        "nina.controllers.hoverboard_axis_drive.maybe_speak_cant_move_alert"
    ) as speak:
        # Bounded duration so the test ends even if abort is disabled and
        # the legacy realign keeps running.
        drv._imu_corrective_hold(base, 0.06, halt, is_forward=True)
    assert speak.call_count == 0, (
        "ABORT_DRIFT_DEG=0 must disable TTS announcements entirely"
    )
    assert not halt.is_set(), (
        "ABORT_DRIFT_DEG=0 must not set the pulse halt event"
    )
    log_text = _imu_corr_log_records.text
    assert "ABORT threshold" not in log_text


def test_drift_abort_banner_tag_present_when_enabled(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """The IMU-hooks startup banner must include ``abort_drift=`` when the
    safety stop is configured so operators can spot the active threshold
    in a single grep without diving into env vars.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _abort_env(abort_deg="25.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    banner_lines = [
        r.getMessage()
        for r in _imu_corr_log_records.records
        if r.getMessage().startswith("hoverboard IMU hooks:")
    ]
    assert banner_lines, "expected IMU hooks startup banner"
    joined = "\n".join(banner_lines)
    assert "abort_drift=25.00 deg" in joined, (
        f"banner must surface the configured abort threshold; saw:\n{joined}"
    )


def test_drift_abort_banner_tag_absent_when_disabled(
    _imu_corr_log_records: pytest.LogCaptureFixture,
) -> None:
    """With the safety stop disabled (``ABORT_DRIFT_DEG=0``) the startup
    banner must NOT include the ``abort_drift=`` tag so the log line
    stays compact on bots that opted out.
    """
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(dxl, threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(os.environ, _abort_env(abort_deg="0.0"), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    banner_lines = [
        r.getMessage()
        for r in _imu_corr_log_records.records
        if r.getMessage().startswith("hoverboard IMU hooks:")
    ]
    assert banner_lines
    joined = "\n".join(banner_lines)
    assert "abort_drift=" not in joined, (
        f"disabled abort must omit the tag entirely; saw:\n{joined}"
    )
