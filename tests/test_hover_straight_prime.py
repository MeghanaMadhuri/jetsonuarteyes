"""Straight-line lean prime: servos visit 2048 before symmetric FWD/BACK."""

from __future__ import annotations

from types import SimpleNamespace

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import HoverboardAxisDrive


class FakeDxl:
    def __init__(self) -> None:
        self.goal_writes: list[dict[int, int]] = []
        self._present: dict[int, int] = {12: 1900, 13: 2100}

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
        forward_pos_left=2200,
        forward_pos_right=2200,
        backward_pos_left=1900,
        backward_pos_right=1900,
        swap_turn_lr=True,
        turn_push_ticks=0,
        tilt_deg=5.0,
        moving_speed=0,
        sign_left=1,
        sign_right=1,
        pulse_forward_enabled=False,
        pulse_forward_on_sec=0.0,
        pulse_forward_brake_sec=0.0,
        pulse_forward_return_ramp_sec=0.0,
        pulse_forward_coast_blend=0.0,
        pulse_forward_sync_present=False,
        pulse_forward_present_tol_ticks=4,
        pulse_forward_present_step_timeout_sec=0.25,
        pulse_ramp_profile="smoothstep",
        pulse_ramp_trap_edge=0.18,
        pulse_ramp_moving_speed=None,
        pulse_waveform="cosine",
        pulse_series_max=5,
        pulse_series_fwd_sec=1.3,
        pulse_series_coast_initial_sec=0.30,
        pulse_series_coast_increment_sec=0.20,
        pulse_series_min_transition_sec=0.18,
    )


def test_first_straight_forward_primes_2048_then_forward() -> None:
    dxl = FakeDxl()
    axis = _axis()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.0,
        turn_duration_sec=2.3,
    )
    lock = __import__("threading").RLock()
    drv = HoverboardAxisDrive(dxl, lock, axis, cfg)
    drv.initialize()
    n0 = len(dxl.goal_writes)
    drv.forward(10)
    chunk = dxl.goal_writes[n0:]
    assert {12: 2048, 13: 2048} in chunk
    last = chunk[-1]
    assert last[12] != 2048 or last[13] != 2048


def test_second_forward_hold_skips_extra_prime() -> None:
    dxl = FakeDxl()
    axis = _axis()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.0,
        turn_duration_sec=2.3,
    )
    lock = __import__("threading").RLock()
    drv = HoverboardAxisDrive(dxl, lock, axis, cfg)
    drv.initialize()
    drv.forward(10)
    n1 = len(dxl.goal_writes)
    drv.forward(10)
    n2 = len(dxl.goal_writes)
    assert n2 == n1 + 1


def test_switch_forward_to_backward_primes_again() -> None:
    dxl = FakeDxl()
    axis = _axis()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.0,
        turn_duration_sec=2.3,
    )
    lock = __import__("threading").RLock()
    drv = HoverboardAxisDrive(dxl, lock, axis, cfg)
    drv.initialize()
    drv.forward(10)
    drv.backward(10)
    goals_flat = [g for g in dxl.goal_writes]
    assert {12: 2048, 13: 2048} in goals_flat
    assert goals_flat.count({12: 2048, 13: 2048}) >= 2
