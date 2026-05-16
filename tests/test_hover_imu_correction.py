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

import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import patch

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive


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
    """Tunables that make the discrete pivot fast and easy to observe in tests."""
    env = {
        "NINA_HOVER_IMU_CORR_THRESHOLD_DEG": "3.0",
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT": "20",
        "NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC": "0.08",
        "NINA_HOVER_IMU_CORR_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_POLL_HZ": "60",
    }
    env.update(extras)
    return env


def _pivot_left_goals_20pct() -> dict[int, int]:
    """Expected lean goals for a 20% blend ``_goals_for_wheels(F, B)`` pivot.

    With the test axis (brake=2048, fwd=2100, bwd=2000, swap_turn_lr=True,
    push=0, extra=100): ``hover_computed_turn_pivot_goals(turn_left=True)``
    returns (1900, 2200). At u=0.2: left = 2048 + (1900-2048)*0.2 = 2018,
    right = 2048 + (2200-2048)*0.2 = 2078.
    """
    return {12: 2018, 13: 2078}


def _pivot_right_goals_20pct() -> dict[int, int]:
    """Mirror of :func:`_pivot_left_goals_20pct` for ``_goals_for_wheels(B, F)``."""
    return {12: 2078, 13: 2018}


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

def test_corrective_hold_pivots_left_for_positive_drift() -> None:
    """Drift +5 → pause, pivot left (F,B geometry), brake, re-prime, resume base."""
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
    pivot_left = _pivot_left_goals_20pct()
    assert any(w == pivot_left for w in after), (
        f"expected pivot-left lean {pivot_left} in writes, saw {after}"
    )
    # Brake goals (2048, 2048) must appear between base and pivot.
    brake = {12: 2048, 13: 2048}
    assert any(w == brake for w in after)
    # Once the pivot completes the loop should resume the primed base lean.
    assert after[-1] == base, f"hold did not resume primed base lean; tail={after[-3:]}"


def test_corrective_hold_pivots_right_for_negative_drift() -> None:
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
    pivot_right = _pivot_right_goals_20pct()
    assert any(w == pivot_right for w in after), (
        f"expected pivot-right lean {pivot_right} in writes, saw {after}"
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
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
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
    # Symmetric forward at speed 10 yields equal left/right goals — they must
    # remain symmetric (no asymmetric bias).
    last = after[-1]
    assert last[12] == last[13], (
        f"set_wheels symmetric straight should not bias L/R; got {last}"
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
# Pulse loops actually trigger pause-pivot-resume during their main holds
# ----------------------------------------------------------------------

def test_backward_pulse_main_hold_pivots_when_drift_exceeds_threshold() -> None:
    """Above-threshold drift inside the back-hold must surface a pivot lean write."""
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
    samples = {"calls": 0}

    def fake_drift() -> float:
        samples["calls"] += 1
        return 5.0  # constant above-threshold drift

    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=fake_drift)

    drv.start_pulse_straight_backward(50)
    for _ in range(200):
        if not drv.is_forward_pulse_active():
            break
        time.sleep(0.02)
    drv.stop()
    assert samples["calls"] >= 2, "drift sampler should be polled in the main hold"
    pivot_left = _pivot_left_goals_20pct()
    assert any(g == pivot_left for g in dxl.goal_writes), (
        f"expected pivot-left lean {pivot_left} during backward pulse, "
        f"saw writes={dxl.goal_writes!r}"
    )


def test_forward_pulse_main_hold_pivots_when_drift_exceeds_threshold() -> None:
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(
        dxl,
        threading.RLock(),
        replace(
            _axis(),
            pulse_series_max=1,
            pulse_series_fwd_sec=0.20,
            pulse_series_coast_initial_sec=0.0,
        ),
        _cfg(),
    )
    drv.initialize()

    def fake_drift() -> float:
        return -5.0  # bot drifted left → pivot right

    with patch.dict(os.environ, _fast_correction_env(), clear=False):
        drv.set_imu_hooks(yaw_drift_fn=fake_drift)

    drv.start_pulse_straight_forward(50)
    for _ in range(200):
        if not drv.is_forward_pulse_active():
            break
        time.sleep(0.02)
    drv.stop()
    pivot_right = _pivot_right_goals_20pct()
    assert any(g == pivot_right for g in dxl.goal_writes), (
        f"expected pivot-right lean {pivot_right} during forward pulse, "
        f"saw writes={dxl.goal_writes!r}"
    )


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
    pivot_left = _pivot_left_goals_20pct()
    pivot_right = _pivot_right_goals_20pct()
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
    pivot_left = _pivot_left_goals_20pct()
    pivot_right = _pivot_right_goals_20pct()
    assert all(w != pivot_left and w != pivot_right for w in after), (
        f"None-drift must not trigger any pivot lean; saw {after}"
    )
    assert sampler_calls, "sampler should still be consulted"
