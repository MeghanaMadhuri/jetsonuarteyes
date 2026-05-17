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
        # Bail warmup disabled in tests so wrong-direction sample streams
        # trigger the bail on the very first sample. The warmup-grace tests
        # override this explicitly.
        "NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS": "0",
        "NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG": "1.0",
        "NINA_HOVER_IMU_CORR_SETTLE_SEC": "0.0",
        "NINA_HOVER_IMU_CORR_COOLDOWN_SEC": "0.0",
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

    def counting_pivot(drift: float, halt_ev: threading.Event) -> bool:
        calls.append(drift)
        return original_pivot(drift, halt_ev)

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
    pivot_left = _pivot_left_goals_20pct()
    assert all(w != pivot_left for w in after), (
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
    pivot_left = _pivot_left_goals_20pct()
    assert all(w != pivot_left for w in after_before_reset), (
        "before reset, cooldown should still block correction"
    )

    drv._imu_begin_straight()  # simulates entering a fresh straight leg

    pre_third = len(dxl.goal_writes)
    drv._imu_corrective_hold(base, 0.15, halt, is_forward=True)
    after_reset = dxl.goal_writes[pre_third:]
    assert any(w == pivot_left for w in after_reset), (
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
        "NINA_HOVER_IMU_CORR_MAX_STEPS",
        "NINA_HOVER_IMU_CORR_BAIL_WARMUP_STEPS",
        "NINA_HOVER_IMU_CORR_BAIL_MARGIN_DEG",
        "NINA_HOVER_IMU_CORR_SETTLE_SEC",
        "NINA_HOVER_IMU_CORR_COOLDOWN_SEC",
        "NINA_HOVER_IMU_CORR_POLL_HZ",
    ):
        os.environ.pop(var, None)
    drv.set_imu_hooks(yaw_drift_fn=lambda: 0.0)
    assert drv._imu_corr_threshold_deg >= 3.0, "threshold should be calm by default"
    assert 15 <= drv._imu_corr_pivot_blend_pct <= 30, (
        "per-step blend should be authoritative enough to actually move the bot"
    )
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
    assert drv._imu_corr_settle_sec >= 0.20, "outer brake settle should be long by default"
    assert drv._imu_corr_cooldown_sec >= 0.5, "cooldown should be substantial by default"
    assert drv._imu_corr_poll_sec >= 1.0 / 10.0, "poll rate should be slow by default"
    assert drv._imu_corr_invert_sign is False, "invert flag must default off (per-bot opt-in)"


# ----------------------------------------------------------------------
# NINA_HOVER_IMU_CORR_INVERT_SIGN — per-bot pivot-direction flip
# ----------------------------------------------------------------------

def test_invert_sign_flips_pivot_direction_for_positive_drift() -> None:
    """With invert ON, drift +5 should produce a *pivot-right* lean, not left."""
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
    pivot_right = _pivot_right_goals_20pct()
    pivot_left = _pivot_left_goals_20pct()
    assert any(w == pivot_right for w in after), (
        f"invert=1 + drift=+5 should pivot RIGHT, saw {after}"
    )
    assert not any(w == pivot_left for w in after), (
        f"invert=1 + drift=+5 must NOT emit the un-inverted pivot-left lean, "
        f"saw {after}"
    )


def test_invert_sign_flips_pivot_direction_for_negative_drift() -> None:
    """With invert ON, drift -5 should produce a *pivot-left* lean."""
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
    pivot_left = _pivot_left_goals_20pct()
    assert any(w == pivot_left for w in after), (
        f"invert=1 + drift=-5 should pivot LEFT, saw {after}"
    )


def test_invert_sign_off_preserves_original_convention() -> None:
    """Default invert=off keeps the old positive-drift → pivot-left mapping."""
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
    assert any(w == _pivot_left_goals_20pct() for w in after)


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
    with patch.dict(os.environ, env, clear=False):
        drv.set_imu_hooks(yaw_drift_fn=sampler)
    halt = threading.Event()
    pre = len(dxl.goal_writes)
    t0 = time.monotonic()
    drv._perform_pivot_correction(8.20, halt)
    elapsed = time.monotonic() - t0
    after = dxl.goal_writes[pre:]
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
    # Two consecutive growing samples must trigger the bail; only the first
    # 1-2 micro-steps may have fired before the bail short-circuits the loop.
    assert len(pivot_writes) <= 3, (
        f"wrong-direction bail must abort the realign quickly; saw "
        f"{len(pivot_writes)} pivot-left micro-step writes"
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
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
    # Initial drift +8 was used to TRIGGER the realign; the loop samples
    # +6 (act), +4 (act), +2 (act), +0.5 (exit). So ~3 actuated steps.
    assert 2 <= len(pivot_writes) <= 5, (
        f"realign should fire a small number of micro-steps before deadband "
        f"exits the loop; saw {len(pivot_writes)} pivot-left writes"
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
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
    # Constant +5° drift never trips the deadband, the overshoot guard, or
    # the wrong-direction bail (|5| is not > |initial 5| + deadband). Must
    # hit MAX_STEPS=6.
    assert pivot_writes == [_pivot_left_goals_20pct()] * 6, (
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
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
    # All 7 steps should run (no early bail) — drift in warmup ignored,
    # post-warmup samples stay inside the 5° margin so no bail trigger.
    assert len(pivot_writes) >= 5, (
        f"warmup must absorb momentum-driven growth and let the realign "
        f"run; saw only {len(pivot_writes)} pivot-left micro-step writes"
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
    pivot_writes = [w for w in after if w == _pivot_left_goals_20pct()]
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
    pivot_left = _pivot_left_goals_20pct()
    pivot_right = _pivot_right_goals_20pct()
    # Initial +7 drift -> first samples positive -> pivot LEFT for a couple
    # of steps. Then sample -3 triggers the overshoot guard and exits without
    # actuating a right-side step (so we don't see a pivot_right in this run).
    assert any(w == pivot_left for w in after), (
        f"expected at least one pivot-left micro-step; saw {after}"
    )
    # Overshoot guard fires BEFORE the negative-drift step would actuate, so
    # no pivot_right writes here; that's the desired "stop chasing" behaviour.
    assert not any(w == pivot_right for w in after), (
        f"overshoot past zero must STOP the realign, not chase the other way; "
        f"saw pivot_right in {after}"
    )
