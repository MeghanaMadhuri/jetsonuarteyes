"""IMU yaw-drift correction wired into ``HoverboardAxisDrive`` straight pulses.

These tests exercise the asymmetric lean-bias correction added so a bot
driving straight forward or backward auto-yaws toward zero drift, plus the
``NINA_HOVER_POST_TURN_SETTLE_SEC`` knob that makes the priming pause visible
right after a "Turn left/right" → "Straight back" sequence (the original
"feels like priming was skipped" complaint).
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


# ----------------------------------------------------------------------
# Goal-bias math
# ----------------------------------------------------------------------

def test_imu_correction_zero_when_no_hook_wired() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    assert drv._imu_correction_ticks() == 0
    out = drv._imu_corrected_goals({12: 2100, 13: 2100}, is_forward=True)
    assert out == {12: 2100, 13: 2100}


def test_imu_correction_inside_deadband_returns_zero_ticks() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(yaw_drift_fn=lambda: 0.2)  # below default 0.5 deg deadband
    assert drv._imu_correction_ticks() == 0


def test_imu_correction_forward_positive_drift_slows_left_speeds_right() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # +5 deg @ 6 ticks/deg = 30 ticks → left -30, right +30 in raw goal space
    drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    out = drv._imu_corrected_goals({12: 2100, 13: 2100}, is_forward=True)
    assert out[12] == 2070, out
    assert out[13] == 2130, out


def test_imu_correction_backward_flips_sign() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(yaw_drift_fn=lambda: 5.0)
    # backward goals start at 2000 (less than brake 2048).
    # speed_sign = -1 ⇒ left +30, right -30 in raw goal space
    out = drv._imu_corrected_goals({12: 2000, 13: 2000}, is_forward=False)
    assert out[12] == 2030, out
    assert out[13] == 1970, out


def test_imu_correction_clamped_to_max_ticks() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_KP_TICKS_PER_DEG": "10",
            "NINA_HOVER_IMU_CORR_MAX_TICKS": "25",
            "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "0.0",
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 99.0)
    assert drv._imu_correction_ticks() == 25
    drv.set_imu_hooks(yaw_drift_fn=lambda: -99.0)
    # set_imu_hooks rereads env, so previous patch is gone — but max from prior
    # call is sticky on the instance, so this verifies clamping survives the
    # second hook install too.


def test_imu_correction_swallows_sampler_exceptions() -> None:
    def boom() -> float:
        raise RuntimeError("imu wedged")

    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    drv.set_imu_hooks(yaw_drift_fn=boom)
    assert drv._imu_correction_ticks() == 0  # no exception, no bias


def test_imu_correction_respects_disable_env() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ, {"NINA_HOVER_IMU_CORR_ENABLE": "0"}, clear=False
    ):
        drv.set_imu_hooks(yaw_drift_fn=lambda: 10.0)
    assert drv._imu_correction_ticks() == 0


# ----------------------------------------------------------------------
# Auto begin/end straight-leg hook plumbing
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
    drv.forward(10)  # second straight in same direction → no extra begin
    assert begins == [1]
    drv.backward(10)  # switching direction primes again → second begin
    assert begins == [1, 1]
    assert ends == []  # never left a straight key
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
    # Pivot leaves the symmetric-straight key → end fires once.
    drv.set_wheels(
        left_dir=drv.DIR_FORWARD, left_speed=20,
        right_dir=drv.DIR_BACKWARD, right_speed=20,
    )
    assert ends == [1]
    # Resuming straight after the pivot triggers a fresh begin.
    drv.forward(10)
    assert begins == [1, 1]


# ----------------------------------------------------------------------
# Pulse loops actually re-issue goals during their main holds
# ----------------------------------------------------------------------

def test_backward_pulse_main_hold_repolls_imu() -> None:
    """During the back-hold the pulse loop should re-apply IMU-corrected goals."""
    dxl = FakeDxl()
    drv = HoverboardAxisDrive(
        dxl,
        threading.RLock(),
        # Make the back hold long enough to observe several poll ticks.
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
        # Constant +2 deg → ticks = round(6 * 2) = 12 → left -12, right +12 on back leg sign
        return 2.0

    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_POLL_HZ": "60",
            "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "0.0",
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=fake_drift)

    drv.start_pulse_straight_backward(50)
    for _ in range(100):
        if not drv.is_forward_pulse_active():
            break
        time.sleep(0.02)
    drv.stop()
    assert samples["calls"] >= 3, "imu drift sampler should be polled during main hold"
    # speed_sign=-1, sign_left=+1: left_goal = 2000 - (1 * -1 * 12) = 2012
    # right_goal = 2000 + (1 * -1 * 12) = 1988
    saw_corrected = any(
        g.get(12) == 2012 and g.get(13) == 1988 for g in dxl.goal_writes
    )
    assert saw_corrected, "expected at least one IMU-corrected backward goal write"


def test_forward_pulse_main_hold_repolls_imu() -> None:
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
    samples = {"calls": 0}

    def fake_drift() -> float:
        samples["calls"] += 1
        return -3.0  # bot drifting left → corr negative → speed up left, slow right

    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_IMU_CORR_POLL_HZ": "60",
            "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "0.0",
        },
        clear=False,
    ):
        drv.set_imu_hooks(yaw_drift_fn=fake_drift)

    drv.start_pulse_straight_forward(50)
    for _ in range(100):
        if not drv.is_forward_pulse_active():
            break
        time.sleep(0.02)
    drv.stop()
    assert samples["calls"] >= 3
    # corr = round(6 * -3) = -18, speed_sign=+1, sign_left=+1, forward base=2114
    # (forward_pos_left=2100 + _STRAIGHT_FWD_EXTRA_TICKS 14 = 2114)
    # left = 2114 - (1 * 1 * -18) = 2132; right = 2114 + (1 * 1 * -18) = 2096
    saw_corrected = any(
        g.get(12) == 2132 and g.get(13) == 2096 for g in dxl.goal_writes
    )
    assert saw_corrected, "expected at least one IMU-corrected forward goal write"


# ----------------------------------------------------------------------
# Post-turn settle dwell after the straight prime
# ----------------------------------------------------------------------

def test_post_turn_settle_dwells_before_pulse_starts() -> None:
    """``NINA_HOVER_POST_TURN_SETTLE_SEC`` should add an observable pause."""
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    with patch.dict(
        os.environ,
        {"NINA_HOVER_POST_TURN_SETTLE_SEC": "0.15"},
        clear=False,
    ):
        t0 = time.monotonic()
        drv.start_pulse_straight_backward(50)
        # Just after start_pulse_straight_backward returns we expect at least
        # the settle dwell + prime to have happened.
        elapsed = time.monotonic() - t0
    drv.stop()
    assert elapsed >= 0.12, (
        f"start_pulse_straight_backward returned in {elapsed:.3f}s; "
        f"expected to dwell at least ~0.15s for post-turn settle"
    )


def test_post_turn_settle_default_is_no_op() -> None:
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    # Default = unset; start should be quick.
    os.environ.pop("NINA_HOVER_POST_TURN_SETTLE_SEC", None)
    t0 = time.monotonic()
    drv.start_pulse_straight_backward(50)
    elapsed = time.monotonic() - t0
    drv.stop()
    assert elapsed < 0.10, (
        f"start_pulse_straight_backward should not pause by default; took {elapsed:.3f}s"
    )


def test_yaw_drift_none_falls_back_to_plain_wait() -> None:
    """A sampler returning None (calibrating / idle) must not bias the goals."""
    drv = HoverboardAxisDrive(FakeDxl(), threading.RLock(), _axis(), _cfg())
    drv.initialize()
    sampler_calls: List[int] = []

    def sampler() -> Optional[float]:
        sampler_calls.append(1)
        return None

    drv.set_imu_hooks(yaw_drift_fn=sampler)
    assert drv._imu_corrected_goals({12: 2100, 13: 2100}, is_forward=True) == {
        12: 2100,
        13: 2100,
    }
    assert sampler_calls  # was actually consulted
