"""Hoverboard forward pulse: alternates FWD lean goals with brake; cancels on stop."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from types import SimpleNamespace

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive


class FakeDxl:
    def __init__(self) -> None:
        self.goal_writes: list[dict[int, int]] = []

    def _require_initialized(self) -> None:
        return None

    def _clamp_pos(self, value: int) -> int:
        return max(0, min(4095, int(value)))

    def sync_write_moving_speed_subset(self, *_a, **_k) -> None:
        return None

    def sync_write_goal_position(self, positions: dict[int, int]) -> None:
        self.goal_writes.append(dict(positions))


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
    )


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
    fwd = {12: 2100, 13: 2100}
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
    assert n_after <= n_before + 25


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
    assert dxl.goal_writes == [{12: 2100, 13: 2100}]
