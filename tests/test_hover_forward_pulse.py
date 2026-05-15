"""Hoverboard forward pulse: timed series of FWD / near-brake, then full brake."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    _STRAIGHT_FWD_EXTRA_TICKS,
    _nudge_goal_from_brake,
    estimate_forward_pulse_series_duration_sec,
)


class FakeDxl:
    def __init__(self) -> None:
        self.goal_writes: list[dict[int, int]] = []
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


def _axis_pulse_fast() -> HoverboardAxisSettings:
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
    )


def test_estimate_forward_pulse_series_duration_sec_formula() -> None:
    axis = replace(
        _axis_pulse_fast(),
        pulse_series_max=12,
        pulse_forward_return_ramp_sec=0.0,
        pulse_series_min_transition_sec=0.0,
        pulse_series_fwd_sec=0.9,
        pulse_series_coast_initial_sec=0.3,
    )
    with patch.dict(os.environ, {"NINA_HOVER_STRAIGHT_PRIME_SEC": "0.05"}, clear=False):
        got = estimate_forward_pulse_series_duration_sec(axis)
    # prime 0.05 + (1 + 2*12) * eff_ramp(0) + 12 * (0.9 + 0.3) = 0.05 + 14.4
    assert abs(got - 14.45) < 1e-9


def test_forward_pulse_alternates_forward_and_brake() -> None:
    dxl = FakeDxl()
    axis = _axis_pulse_fast()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.01,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    hb.start_pulse_straight_forward(50)
    time.sleep(0.2)
    hb.stop()
    assert len(dxl.goal_writes) >= 4
    # Symmetric straight FWD uses nudged goals past calibrated forward_pos_*.
    fl = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    fr = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    fwd = {12: fl, 13: fr}
    brk = {12: 2048, 13: 2048}
    saw_fwd = any(g == fwd for g in dxl.goal_writes)
    saw_brk = any(g == brk for g in dxl.goal_writes)
    assert saw_fwd and saw_brk


def test_forward_pulse_stops_after_stop() -> None:
    dxl = FakeDxl()
    axis = _axis_pulse_fast()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.01,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    hb.start_pulse_straight_forward(50)
    time.sleep(0.15)
    n_before = len(dxl.goal_writes)
    hb.stop()
    time.sleep(0.15)
    n_after = len(dxl.goal_writes)
    assert n_after <= n_before + 80


def test_set_wheels_halts_pulse() -> None:
    dxl = FakeDxl()
    axis = _axis_pulse_fast()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.01,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    hb.start_pulse_straight_forward(50)
    time.sleep(0.06)
    hb.set_wheels(
        left_dir=hb.DIR_FORWARD,
        left_speed=10,
        right_dir=hb.DIR_FORWARD,
        right_speed=10,
    )
    time.sleep(0.1)
    assert not hb.is_forward_pulse_active()


def test_start_pulse_disabled_falls_back_to_forward_goals() -> None:
    dxl = FakeDxl()
    axis = replace(_axis_pulse_fast(), pulse_forward_enabled=False)
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.01,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    dxl.goal_writes.clear()
    hb.start_pulse_straight_forward(40)
    assert not hb.is_forward_pulse_active()
    fl = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    fr = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    assert dxl.goal_writes[-1] == {12: fl, 13: fr}
    prime = {12: 2048, 13: 2048}
    assert prime in dxl.goal_writes


def test_forward_pulse_series_ends_at_full_brake() -> None:
    dxl = FakeDxl()
    axis = replace(
        _axis_pulse_fast(),
        pulse_series_max=3,
        pulse_series_fwd_sec=0.02,
        pulse_series_coast_initial_sec=0.01,
        pulse_series_min_transition_sec=0.012,
        pulse_forward_return_ramp_sec=0.02,
        pulse_forward_coast_blend=0.2,
    )
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.01,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    hb.start_pulse_straight_forward(50)
    for _ in range(300):
        if not hb.is_forward_pulse_active():
            break
        time.sleep(0.02)
    assert not hb.is_forward_pulse_active()
    assert dxl.goal_writes[-1] == {12: 2048, 13: 2048}


def test_pulse_ramp_blend_profiles_are_monotonic() -> None:
    from nina.controllers.hoverboard_axis_drive import _pulse_ramp_blend_u

    for profile in ("smoothstep", "smootherstep", "cubic_io", "trapezoid"):
        us = [_pulse_ramp_blend_u(i / 200.0, profile, 0.18) for i in range(201)]
        for i in range(200):
            assert us[i + 1] + 1e-9 >= us[i], (profile, i, us[i], us[i + 1])
        assert us[0] <= 1e-12
        assert abs(us[-1] - 1.0) < 1e-9
