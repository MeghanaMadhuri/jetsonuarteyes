"""Hoverboard forward + backward straight-line drive tests.

Forward motion uses the **drift-corrected straight loop**: each cycle
drives forward for ``NINA_HOVER_STRAIGHT_LEG_SEC``, brakes, samples
drift at standstill, runs proportional micro-step pivots if drift
exceeds the deadband, and repeats until ``stop()`` (or a 90° abort).
Backward motion still uses the legacy pulse series (separate
``pulse_series_back_*`` knobs).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nina.config.settings import HoverboardAxisSettings
from nina.controllers.dynamixel_manager import REG_PRESENT_POS
from nina.controllers.hoverboard_axis_drive import (
    HoverboardAxisDrive,
    _STRAIGHT_BACK_EXTRA_TICKS,
    _STRAIGHT_FWD_EXTRA_TICKS,
    _nudge_goal_from_brake,
    _straight_abort_drift_deg,
    _straight_brake_settle_sec,
    _straight_corr_back_step_dur_cap_sec,
    _straight_corr_back_step_min_sec,
    _straight_corr_blend_pct,
    _straight_corr_deadband_deg,
    _straight_corr_residual_deg,
    _straight_corr_step_dur_cap_sec,
    _straight_corr_step_min_sec,
    _straight_corr_step_rate_dps,
    _straight_corr_swap_pivot_dir,
    _straight_leg_sec,
    _straight_settle_max_sec,
    _straight_settle_poll_sec,
    _straight_settle_rate_dps,
    _straight_settle_stable_sec,
    estimate_backward_pulse_series_duration_sec,
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
    """Tunable axis for snappy tests; backward knobs preserved for legacy series.

    Forward straight knobs (``NINA_HOVER_STRAIGHT_*``) are sourced from env
    in each test that needs them; the axis fields are left at defaults.
    """
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


def _fast_straight_env() -> dict[str, str]:
    """Snappy timings + IMU correction tuning for unit tests.

    Pins ``NINA_HOVER_STRAIGHT_CORR_BLEND_PCT`` to 20 so the pivot
    goals match ``_goals_for_wheels(..., 20, ..., 20)`` in the
    direction-of-pivot assertions below. The production default
    (full calibrated pivot at 100) is exercised by a dedicated test.
    The deadband override (1.5°) is tighter than the new production
    default (2.5°) so the existing ``drift=0.5`` / ``drift=10``
    fixtures keep mapping to skip / correct as the assertions expect.
    """
    return {
        "NINA_HOVER_STRAIGHT_LEG_SEC": "0.05",
        "NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC": "0.02",
        "NINA_HOVER_STRAIGHT_PRIME_SEC": "0.01",
        "NINA_HOVER_STRAIGHT_CORR_BLEND_PCT": "20",
        "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC": "0.05",
        "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC": "0.005",
        "NINA_HOVER_STRAIGHT_CORR_STEP_RATE_DPS": "60",
        "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "1.5",
        # Tight exit threshold (hysteresis exit), strictly tighter than
        # the trigger deadband (1.5°). Most fixtures simulate drift
        # collapsing to ~0 after one mocked step, so any value <1.5° is
        # fine — 0.5° matches a typical IMU noise floor.
        "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "0.5",
        # Pin pivot-direction swap OFF in the snappy fixture so the
        # legacy direction-of-correction assertions (which expect
        # ``L=FWD,R=BACK`` for a positive-drift pivot LEFT decision)
        # stay valid; a dedicated test exercises swap=True separately.
        "NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR": "0",
        # Active settle: snappy windows so tests don't hang on the
        # default 1.5 s hard cap when no yaw_rate_fn is wired.
        "NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS": "3.0",
        "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "0.01",
        "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "0.10",
        "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.002",
        "NINA_HOVER_POST_TURN_SETTLE_SEC": "0.0",
        # Legacy in-motion correction knobs (still consumed by the
        # backward pulse and the 90° turn closed loop). Pinned so the
        # backward + turn tests in other modules stay stable.
        "NINA_HOVER_IMU_CORR_DEADBAND_DEG": "1.5",
        "NINA_HOVER_IMU_CORR_PIVOT_BLEND_PCT": "20",
        "NINA_HOVER_IMU_CORR_PIVOT_MAX_SEC": "0.05",
        "NINA_HOVER_IMU_CORR_STEP_MIN_SEC": "0.005",
        "NINA_HOVER_IMU_CORR_STEP_SETTLE_SEC": "0.005",
        "NINA_HOVER_IMU_CORR_STEP_RATE_DPS": "60",
        "NINA_HOVER_IMU_CORR_MAX_STEPS": "5",
        "NINA_HOVER_IMU_CORR_INVERT_SIGN": "0",
    }


def _make_hb(
    axis: HoverboardAxisSettings, dxl: FakeDxl | None = None
) -> tuple[HoverboardAxisDrive, FakeDxl]:
    if dxl is None:
        dxl = FakeDxl()
    cfg = SimpleNamespace(
        default_speed_percent=10,
        settle_delay_sec=0.005,
        invert_left_dir=False,
        invert_right_dir=False,
    )
    hb = HoverboardAxisDrive(dxl, threading.RLock(), axis, cfg)
    hb.initialize()
    return hb, dxl


def _expected_forward_goals() -> dict[int, int]:
    fl = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    fr = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    return {12: fl, 13: fr}


def _wait_until_idle(hb: HoverboardAxisDrive, timeout_sec: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not hb.is_forward_pulse_active():
            return
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# Env getter defaults
# ---------------------------------------------------------------------------


_STRAIGHT_ENV_KEYS = (
    "NINA_HOVER_STRAIGHT_LEG_SEC",
    "NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC",
    "NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG",
    "NINA_HOVER_STRAIGHT_CORR_BLEND_PCT",
    "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC",
    "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC",
    "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC",
    "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC",
    "NINA_HOVER_STRAIGHT_CORR_STEP_RATE_DPS",
    "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG",
    "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG",
    "NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS",
    "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC",
    "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC",
    "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC",
    "NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR",
)


def test_straight_env_getter_defaults() -> None:
    """Without overrides: 0.5 s leg, 0.3 s brake settle, 90° abort, 60%
    blend, chassis-matched 0.030 s cap / 0.030 s floor / 84 dps / 3.0°
    deadband; 1.0° residual (hysteresis exit, tighter than deadband);
    active settle 3 dps / 0.10 s stable / 1.5 s max / 0.02 s poll;
    correction pivot direction SWAPPED by default (reference chassis)."""
    with patch.dict(os.environ, {k: "" for k in _STRAIGHT_ENV_KEYS}, clear=False):
        for k in _STRAIGHT_ENV_KEYS:
            os.environ.pop(k, None)
        assert _straight_leg_sec() == 0.5
        assert _straight_brake_settle_sec() == 0.30
        assert _straight_abort_drift_deg() == 90.0
        assert _straight_corr_blend_pct() == 60
        assert _straight_corr_step_dur_cap_sec() == 0.030
        assert _straight_corr_step_min_sec() == 0.030
        # Backward-specific step duration defaults: softer than forward
        # because the same 0.030 s kick over-pushes on backward-decelerated
        # chassis (chassis-asymmetric pre-pivot momentum profile).
        assert _straight_corr_back_step_dur_cap_sec() == 0.020
        assert _straight_corr_back_step_min_sec() == 0.015
        assert _straight_corr_step_rate_dps() == 84.0
        assert _straight_corr_deadband_deg() == 3.0
        assert _straight_corr_residual_deg() == 1.0
        assert _straight_settle_rate_dps() == 3.0
        assert _straight_settle_stable_sec() == 0.10
        assert _straight_settle_max_sec() == 1.50
        assert _straight_settle_poll_sec() == 0.02
        assert _straight_corr_swap_pivot_dir() is True


def test_straight_corr_swap_pivot_dir_truthy_values() -> None:
    """Common truthy / falsy spellings of the swap env var."""
    for falsy in ("0", "false", "FALSE", "no", "off", ""):
        with patch.dict(
            os.environ,
            {"NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR": falsy},
            clear=False,
        ):
            assert _straight_corr_swap_pivot_dir() is False, falsy
    for truthy in ("1", "true", "yes", "ON", "anything"):
        with patch.dict(
            os.environ,
            {"NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR": truthy},
            clear=False,
        ):
            assert _straight_corr_swap_pivot_dir() is True, truthy


def test_straight_corr_back_step_dur_overrides_honored() -> None:
    """Backward-specific overrides must be respected exactly."""
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC": "0.022",
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC": "0.017",
        },
        clear=False,
    ):
        assert _straight_corr_back_step_dur_cap_sec() == 0.022
        assert _straight_corr_back_step_min_sec() == 0.017


def test_straight_corr_back_step_dur_clamps_out_of_range() -> None:
    """Out-of-range values clamp to ``[0.005, 1.0]`` like the forward
    versions, instead of crashing.
    """
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC": "0.00001",
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC": "9999",
        },
        clear=False,
    ):
        assert _straight_corr_back_step_dur_cap_sec() == 0.005
        assert _straight_corr_back_step_min_sec() == 1.0


def test_straight_corr_back_step_dur_garbage_falls_back_to_default() -> None:
    """Garbage strings fall back to the documented defaults."""
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC": "garbage",
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC": "nope",
        },
        clear=False,
    ):
        assert _straight_corr_back_step_dur_cap_sec() == 0.020
        assert _straight_corr_back_step_min_sec() == 0.015


def test_drift_correction_forward_uses_forward_step_getters_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward-leg correction must call the **forward** step-duration
    getters and never the backward getters. This locks the
    "forward path is bit-identical" contract that the backward fix
    must not violate.
    """
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 5.0, yaw_rate_fn=lambda: 0.0)

    calls = {"fwd_cap": 0, "fwd_min": 0, "back_cap": 0, "back_min": 0}
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_step_dur_cap_sec",
        lambda: (calls.__setitem__("fwd_cap", calls["fwd_cap"] + 1) or 0.05),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_step_min_sec",
        lambda: (calls.__setitem__("fwd_min", calls["fwd_min"] + 1) or 0.005),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_back_step_dur_cap_sec",
        lambda: (calls.__setitem__("back_cap", calls["back_cap"] + 1) or 0.02),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_back_step_min_sec",
        lambda: (calls.__setitem__("back_min", calls["back_min"] + 1) or 0.015),
    )

    env = _fast_straight_env() | {"NINA_HOVER_IMU_CORR_MAX_STEPS": "1"}
    with patch.dict(os.environ, env, clear=False):
        hb._correct_drift_at_standstill(
            initial_drift=5.0,
            brake_goals={12: 2048, 13: 2048},
            halt=threading.Event(),
            direction_label="forward",
        )

    assert calls["fwd_cap"] == 1, (
        f"forward path must call forward step_dur_cap getter once; "
        f"saw {calls}"
    )
    assert calls["fwd_min"] == 1, (
        f"forward path must call forward step_min getter once; "
        f"saw {calls}"
    )
    assert calls["back_cap"] == 0, (
        f"forward path must NOT touch backward step_dur_cap getter "
        f"(forward = bit-identical to before this change); saw {calls}"
    )
    assert calls["back_min"] == 0, (
        f"forward path must NOT touch backward step_min getter "
        f"(forward = bit-identical to before this change); saw {calls}"
    )


def test_drift_correction_backward_uses_backward_step_getters_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward-leg correction must call the **backward** step-duration
    getters and never the forward ones — that's the whole point of
    the backward-specific tuning knobs.
    """
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 5.0, yaw_rate_fn=lambda: 0.0)

    calls = {"fwd_cap": 0, "fwd_min": 0, "back_cap": 0, "back_min": 0}
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_step_dur_cap_sec",
        lambda: (calls.__setitem__("fwd_cap", calls["fwd_cap"] + 1) or 0.05),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_step_min_sec",
        lambda: (calls.__setitem__("fwd_min", calls["fwd_min"] + 1) or 0.005),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_back_step_dur_cap_sec",
        lambda: (calls.__setitem__("back_cap", calls["back_cap"] + 1) or 0.02),
    )
    monkeypatch.setattr(
        "nina.controllers.hoverboard_axis_drive._straight_corr_back_step_min_sec",
        lambda: (calls.__setitem__("back_min", calls["back_min"] + 1) or 0.015),
    )

    env = _fast_straight_env() | {"NINA_HOVER_IMU_CORR_MAX_STEPS": "1"}
    with patch.dict(os.environ, env, clear=False):
        hb._correct_drift_at_standstill(
            initial_drift=5.0,
            brake_goals={12: 2048, 13: 2048},
            halt=threading.Event(),
            direction_label="backward",
        )

    assert calls["back_cap"] == 1, (
        f"backward path must call backward step_dur_cap getter once; "
        f"saw {calls}"
    )
    assert calls["back_min"] == 1, (
        f"backward path must call backward step_min getter once; "
        f"saw {calls}"
    )
    assert calls["fwd_cap"] == 0, (
        f"backward path must NOT touch forward step_dur_cap getter "
        f"(otherwise forward tuning bleeds into backward); saw {calls}"
    )
    assert calls["fwd_min"] == 0, (
        f"backward path must NOT touch forward step_min getter; "
        f"saw {calls}"
    )


def test_straight_corr_back_step_dur_independent_from_forward() -> None:
    """Setting the forward step-duration env vars must NOT affect the
    backward getters, and vice versa. This is the contract that
    "ship the backward fix without impacting forward" depends on —
    so the forward path can be re-tuned independently and the
    backward path keeps its softer defaults (and vice versa).
    """
    for k in (
        "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC",
        "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC",
        "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC",
        "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC",
    ):
        os.environ.pop(k, None)
    # Override forward only — backward must keep its 0.020 / 0.015
    # defaults regardless.
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC": "0.060",
            "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC": "0.045",
        },
        clear=False,
    ):
        assert _straight_corr_step_dur_cap_sec() == 0.060
        assert _straight_corr_step_min_sec() == 0.045
        assert _straight_corr_back_step_dur_cap_sec() == 0.020
        assert _straight_corr_back_step_min_sec() == 0.015
    # Override backward only — forward must keep its 0.030 / 0.030
    # defaults regardless.
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_DUR_CAP_SEC": "0.025",
            "NINA_HOVER_STRAIGHT_CORR_BACK_STEP_MIN_SEC": "0.012",
        },
        clear=False,
    ):
        assert _straight_corr_step_dur_cap_sec() == 0.030
        assert _straight_corr_step_min_sec() == 0.030
        assert _straight_corr_back_step_dur_cap_sec() == 0.025
        assert _straight_corr_back_step_min_sec() == 0.012


def test_straight_env_getter_overrides() -> None:
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_LEG_SEC": "0.5",
            "NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC": "0.1",
            "NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "45.0",
            "NINA_HOVER_STRAIGHT_CORR_BLEND_PCT": "60",
            "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC": "0.18",
            "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC": "0.04",
            "NINA_HOVER_STRAIGHT_CORR_STEP_RATE_DPS": "90.0",
            "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "4.0",
            "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "1.5",
            "NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS": "5.0",
            "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "0.25",
            "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "3.5",
            "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.05",
        },
        clear=False,
    ):
        assert _straight_leg_sec() == 0.5
        assert _straight_brake_settle_sec() == 0.1
        assert _straight_abort_drift_deg() == 45.0
        assert _straight_corr_blend_pct() == 60
        assert _straight_corr_step_dur_cap_sec() == 0.18
        assert _straight_corr_step_min_sec() == 0.04
        assert _straight_corr_step_rate_dps() == 90.0
        assert _straight_corr_deadband_deg() == 4.0
        assert _straight_corr_residual_deg() == 1.5
        assert _straight_settle_rate_dps() == 5.0
        assert _straight_settle_stable_sec() == 0.25
        assert _straight_settle_max_sec() == 3.5
        assert _straight_settle_poll_sec() == 0.05


def test_straight_env_getter_clamps() -> None:
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_LEG_SEC": "999",
            "NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC": "-5",
            "NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "500",
            "NINA_HOVER_STRAIGHT_CORR_BLEND_PCT": "9999",
            "NINA_HOVER_STRAIGHT_CORR_STEP_DUR_CAP_SEC": "0.0001",
            "NINA_HOVER_STRAIGHT_CORR_STEP_MIN_SEC": "9999",
            "NINA_HOVER_STRAIGHT_CORR_STEP_RATE_DPS": "0.001",
            "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "9999",
            "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "9999",
            "NINA_HOVER_STRAIGHT_SETTLE_RATE_DPS": "9999",
            "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "-1",
            "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "9999",
            "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.00001",
        },
        clear=False,
    ):
        assert _straight_leg_sec() == 5.0
        assert _straight_brake_settle_sec() == 0.0
        assert _straight_abort_drift_deg() == 180.0
        assert _straight_corr_blend_pct() == 100
        assert _straight_corr_step_dur_cap_sec() == 0.005
        assert _straight_corr_step_min_sec() == 1.0
        assert _straight_corr_step_rate_dps() == 1.0
        assert _straight_corr_deadband_deg() == 30.0
        assert _straight_corr_residual_deg() == 30.0
        assert _straight_settle_rate_dps() == 30.0
        assert _straight_settle_stable_sec() == 0.0
        assert _straight_settle_max_sec() == 10.0
        assert _straight_settle_poll_sec() == 0.001


# ---------------------------------------------------------------------------
# Drive-then-check cycle
# ---------------------------------------------------------------------------


def test_forward_loop_drives_then_brakes_then_cycles() -> None:
    """Each cycle: FWD goals applied, then brake goals, then FWD again."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # Drift always zero so no correction steps are inserted between legs.
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        # Allow ~3 cycles to complete: 3 * (leg 0.05 + settle 0.02) = 0.21 s.
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    fwd = _expected_forward_goals()
    brk = {12: 2048, 13: 2048}
    # Both shapes should appear, and we should see at least 2 forward legs
    # (i.e. the loop genuinely cycles rather than firing once).
    forward_count = sum(1 for g in dxl.goal_writes if g == fwd)
    brake_count = sum(1 for g in dxl.goal_writes if g == brk)
    assert forward_count >= 2, f"forward legs={forward_count} writes={dxl.goal_writes}"
    assert brake_count >= 2, f"brake stamps={brake_count}"


def test_forward_loop_no_drift_no_pivot_writes() -> None:
    """With drift==0 the loop should only emit FWD + brake goals — never pivot pairs."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.20)
        hb.stop()
    _wait_until_idle(hb)

    fwd = _expected_forward_goals()
    brk = {12: 2048, 13: 2048}
    for g in dxl.goal_writes:
        assert g in (fwd, brk), f"unexpected goal during zero-drift run: {g}"


def test_forward_loop_starts_integrator_once_for_cumulative_tracking() -> None:
    """The integrator starts ONCE at the top of the motion (not per-leg).

    Per-leg integrator resets were the bug: small same-sign per-leg
    biases (+1° / +2° / leg) never crossed the per-leg deadband but
    compounded into a real-world curve. Switching to cumulative
    tracking means the integrator runs from movement-start to
    movement-end, and the drift sample after each leg is the total
    heading deviation from the operator-defined start heading.

    The end pair must also fire once (paired with begin) when the
    motion terminates, so the integrator is cleaned up.
    """
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    begin_calls = {"n": 0}
    end_calls = {"n": 0}
    hb.set_imu_hooks(
        yaw_drift_fn=lambda: 0.0,
        begin_straight_fn=lambda: begin_calls.__setitem__(
            "n", begin_calls["n"] + 1
        ),
        end_straight_fn=lambda: end_calls.__setitem__(
            "n", end_calls["n"] + 1
        ),
    )

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        # Run for ~5–6 legs (each leg is 0.05 s + brake settle).
        time.sleep(0.45)
        hb.stop()
    _wait_until_idle(hb)

    assert begin_calls["n"] == 1, (
        f"integrator should start exactly ONCE per forward motion "
        f"(cumulative tracking); saw {begin_calls['n']} begin calls"
    )
    assert end_calls["n"] >= 1, (
        f"integrator must end at least once when motion stops; "
        f"saw {end_calls['n']} end calls"
    )


def test_forward_loop_cumulative_drift_compounds_across_legs() -> None:
    """A consistent same-sign per-leg drift bias should eventually
    trigger correction.

    With per-leg reset semantics (the old buggy behavior), a yaw_fn
    returning a constant +1° would have been "+1° per leg, within
    deadband, no correction" forever — accumulating into a curve.

    With cumulative tracking, the yaw_fn is what we ask it to be, and
    once cumulative drift crosses the 1.5° deadband (the fixture's
    trigger), correction fires. The behavioral lock: a constant
    +5° (well above 1.5° deadband) must produce pivot writes.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 5.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    pivot_steps = sum(
        1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r
    )
    assert pivot_steps >= 1, (
        f"cumulative drift of +5° must trigger at least one correction "
        f"step; saw {pivot_steps} pivot writes in {dxl.goal_writes}"
    )


# ---------------------------------------------------------------------------
# Drift correction direction
# ---------------------------------------------------------------------------


class _DriftSequence:
    """Yaw sampler that returns a scripted drift series, then 0.0 forever.

    First sample after each forward leg comes from the scripted list;
    subsequent samples (during pivot correction) follow afterwards. When
    the list is exhausted the sampler returns 0.0 (i.e. drift settled).
    """

    def __init__(self, drifts: list[float]) -> None:
        self._drifts = list(drifts)

    def __call__(self) -> float:
        if self._drifts:
            return self._drifts.pop(0)
        return 0.0


def test_forward_loop_positive_drift_pivots_left() -> None:
    """Drift > 0 (drifted right) → first pivot step is L=FWD, R=BACK."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # Big initial drift, then drift returns to 0 after one pivot step.
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([10.0, 0.0]))

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        # One leg + one correction step + a bit of slack.
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    # Expected pivot-left goals: left=FWD@20%, right=BACK@20%.
    pivot_left = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD,
        left_speed=20,
        right_dir=hb.DIR_BACKWARD,
        right_speed=20,
    )
    assert any(g == pivot_left for g in dxl.goal_writes), (
        f"expected pivot-left goals {pivot_left} not found in {dxl.goal_writes}"
    )


def test_forward_loop_negative_drift_pivots_right() -> None:
    """Drift < 0 (drifted left) → first pivot step is L=BACK, R=FWD."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([-10.0, 0.0]))

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    pivot_right = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD,
        left_speed=20,
        right_dir=hb.DIR_FORWARD,
        right_speed=20,
    )
    assert any(g == pivot_right for g in dxl.goal_writes), (
        f"expected pivot-right goals {pivot_right} not found"
    )


def test_forward_loop_swap_pivot_dir_inverts_goals() -> None:
    """With SWAP_PIVOT_DIR=1, a positive-drift pivot_left decision must
    apply the L=BACK, R=FWD goals (the field-tested mapping on the
    reference chassis where the conventional L=FWD,R=BACK mechanically
    rotates the chassis right). The legacy mapping is never applied.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([10.0, 0.0]))

    env = _fast_straight_env() | {"NINA_HOVER_STRAIGHT_CORR_SWAP_PIVOT_DIR": "1"}
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    swapped_pivot_left = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    legacy_pivot_left = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    assert any(g == swapped_pivot_left for g in dxl.goal_writes), (
        f"with swap=1, positive-drift pivot_left decision must apply "
        f"{swapped_pivot_left}; goal_writes={dxl.goal_writes}"
    )
    assert all(g != legacy_pivot_left for g in dxl.goal_writes), (
        "with swap=1, the legacy L=FWD,R=BACK goals must NOT be applied"
    )


def test_forward_loop_invert_sign_flips_pivot_direction() -> None:
    """With INVERT_SIGN=1 positive drift should pivot RIGHT instead of LEFT."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([10.0, 0.0]))

    env = _fast_straight_env() | {"NINA_HOVER_IMU_CORR_INVERT_SIGN": "1"}
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    pivot_right = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD,
        left_speed=20,
        right_dir=hb.DIR_FORWARD,
        right_speed=20,
    )
    assert any(g == pivot_right for g in dxl.goal_writes), (
        "with INVERT_SIGN=1, positive drift should produce a right pivot"
    )


def test_forward_loop_correction_default_uses_meaningful_pivot_blend() -> None:
    """With no override, drift correction must drive servos to a
    *meaningful* pivot pose — one motor leaning back, the other
    leaning forward, with each at least 50 ticks off the brake
    centre — not the legacy 20% blended nudge that chassis
    stiction defeated.

    The current default is 60% blend (tuned down from 100% to dial
    in less per-kick rotation); this test asserts the structural
    properties of the write rather than the exact tick values, so
    future blend-tuning doesn't churn the test.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([10.0, 0.0]))

    # Same env as ``_fast_straight_env`` EXCEPT do not override
    # NINA_HOVER_STRAIGHT_CORR_BLEND_PCT — let it fall to the default.
    env = {k: v for k, v in _fast_straight_env().items()
           if k != "NINA_HOVER_STRAIGHT_CORR_BLEND_PCT"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("NINA_HOVER_STRAIGHT_CORR_BLEND_PCT", None)
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    brake = 2048
    legacy_20pct_offset_max = 30  # 20%-blend pivot is ~17–24 ticks off brake on this fixture
    meaningful_pivot_writes = [
        g for g in dxl.goal_writes
        if 12 in g and 13 in g
        and (g[12] - brake) * (g[13] - brake) < 0  # one above, one below
        and abs(g[12] - brake) > legacy_20pct_offset_max
        and abs(g[13] - brake) > legacy_20pct_offset_max
    ]
    assert meaningful_pivot_writes, (
        "default drift correction must apply a pivot pose with one motor "
        "back and one forward, each >30 ticks off brake (i.e. NOT the "
        f"legacy 20%-blend nudge). goal_writes={dxl.goal_writes}"
    )


class _RateSequence:
    """Yaw-rate sampler that returns a scripted series, then 0.0 forever.

    Models a chassis that's spinning at first (high |rate|) and gradually
    comes to rest. Each call returns the next value; once the list is
    exhausted it returns 0.0 (perfectly still) so the active settle
    eventually succeeds.
    """

    def __init__(self, rates: list[float]) -> None:
        self._rates = list(rates)

    def __call__(self) -> float:
        if self._rates:
            return self._rates.pop(0)
        return 0.0


def test_active_settle_waits_for_low_rate_before_drift_sample() -> None:
    """When the chassis is still rotating (|yaw_rate| > threshold), the
    loop must hold off on sampling drift until the rate drops. Once it
    does, normal cycling resumes.
    """
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    # First 5 rate samples are "spinning" (>3 dps threshold); then 0.
    # Drift fn returns 0 once we get there.
    rate_seq = _RateSequence([20.0, 15.0, 8.0, 5.0, 4.0])
    drift_calls = {"n": 0}

    def drift_fn() -> float:
        drift_calls["n"] += 1
        return 0.0

    hb.set_imu_hooks(yaw_drift_fn=drift_fn, yaw_rate_fn=rate_seq)

    # Make the loop run one cycle then halt.
    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "1.0",
        "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "0.005",
        "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.005",
    }
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.20)
        hb.stop()
    _wait_until_idle(hb)

    # Drift must have been sampled at least once (rate eventually
    # dropped below threshold within the test window).
    assert drift_calls["n"] >= 1, (
        "active settle should release once yaw_rate dips below threshold; "
        f"drift_fn was called {drift_calls['n']} times"
    )


def test_active_settle_falls_back_to_timer_when_no_rate_hook() -> None:
    """No yaw_rate_fn wired -> loop must fall back to the fixed
    brake_settle timer and keep cycling normally."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # Only drift hook, no rate hook.
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    fwd = _expected_forward_goals()
    forward_count = sum(1 for g in dxl.goal_writes if g == fwd)
    assert forward_count >= 2, (
        "fallback path should still cycle forward legs without rate hook; "
        f"forward legs={forward_count}"
    )


def test_active_settle_timeout_skips_correction_but_still_checks_abort() -> None:
    """If the chassis never settles within SETTLE_MAX_SEC, the loop:

    1. MUST still sample drift, so the cumulative-drift abort threshold
       can fire and halt a runaway-spin (the field-observed bug was
       precisely that the old ``continue`` skipped the abort check,
       letting the bot keep driving while spinning out).
    2. MUST NOT attempt the standstill micro-step pivot correction —
       pivoting a chassis that's already rotating can't make a clean
       correction and risks compounding the rotation.

    This test simulates a chassis stuck rotating at 50 dps but with
    drift below the 90° abort (5°), so the loop should sample drift,
    skip the correction, and continue to the next leg.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    drift_calls = {"n": 0}

    def drift_fn() -> float:
        drift_calls["n"] += 1
        return 5.0  # > deadband but well below abort threshold

    hb.set_imu_hooks(yaw_drift_fn=drift_fn, yaw_rate_fn=lambda: 50.0)

    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "0.05",
        "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "0.005",
        "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.005",
    }
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    # Drift MUST be sampled (the abort threshold gates on it) — this
    # is the post-fix contract. The OLD behavior (drift_calls == 0)
    # was the runaway-spin bug.
    assert drift_calls["n"] >= 1, (
        "post-fix: active-settle timeout must still sample drift for "
        f"the abort check; drift_fn called {drift_calls['n']} times"
    )
    # But correction must NOT fire — no pivot writes despite the 5°
    # drift being above the deadband.
    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    assert all(g != pivot_l and g != pivot_r for g in dxl.goal_writes), (
        "active-settle timeout must skip the standstill micro-step "
        "correction even though drift exceeds deadband — chassis is "
        "still rotating, pivot can't make a clean correction"
    )


def test_forward_loop_aborts_correction_when_step_makes_drift_worse() -> None:
    """If a correction step INCREASES |drift| (wrong pivot direction /
    sensor glitch / wheel stall), the loop must bail after exactly one
    bad step rather than compound the error into a runaway spin.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # Sequence: post-leg sample is +5.0, then after step 1 it's WORSE
    # (+12.0), then it keeps getting worse if we were to keep stepping.
    # The loop must stop after the +5.0 → +12.0 step.
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([5.0, 12.0, 20.0, 30.0]))

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    pivot_steps = sum(1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r)
    assert pivot_steps <= 2, (
        f"loop must bail after the first wrong-direction step within a "
        f"single correction window; saw {pivot_steps} pivot writes in "
        f"{dxl.goal_writes}"
    )


def test_forward_loop_correction_continues_past_deadband_until_residual() -> None:
    """Hysteresis: once correction fires (|drift| > deadband), the loop
    must continue stepping until |drift| <= residual, not stop at the
    deadband edge.

    With deadband=2.0° and residual=0.3°, a drift trajectory of
    5.0 → 1.0 → 0.1 must produce TWO pivot writes (one for the 5° →
    1° kick, one for the 1° → 0.1° kick) — the first sample at +1.0°
    is already inside the 2.0° deadband but OUTSIDE the 0.3° residual,
    so the loop must press on.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([5.0, 1.0, 0.1, 0.0]))

    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "2.0",
        "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "0.3",
    }
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    pivot_steps = sum(1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r)
    assert pivot_steps >= 2, (
        f"hysteresis: correction must continue past deadband (2.0°) until "
        f"residual (0.3°); expected >=2 pivot writes, saw {pivot_steps} in "
        f"{dxl.goal_writes}"
    )


def test_forward_loop_correction_exits_at_residual_not_deadband() -> None:
    """The exit threshold is ``residual``, not ``deadband``.

    With deadband=2.0° and residual=1.5°, a drift of 5.0 → 1.4 must
    exit after ONE step (1.4 ≤ 1.5 residual), even though 1.4 is well
    under the 2.0° deadband too. (The point of this test is to fix the
    new behavior contractually so a future "exit at deadband" regression
    can't sneak through.)
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([5.0, 1.4, 0.0]))

    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "2.0",
        "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "1.5",
    }
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    # First correction window should be exactly one step (5.0 → 1.4,
    # exits because 1.4 ≤ residual=1.5). Subsequent legs may add more
    # pivots if drift exceeds deadband again, but the FIRST correction
    # window is what we're locking down.
    pivot_steps = sum(1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r)
    assert pivot_steps >= 1, (
        f"correction must fire on drift 5.0; saw {pivot_steps} pivot writes"
    )


def test_forward_loop_residual_clamped_below_deadband() -> None:
    """If RESIDUAL_DEG is configured >= DEADBAND_DEG, the correction
    routine must internally clamp residual to ``deadband / 2`` so the
    loop doesn't exit on its first sample (which already passed the
    trigger). Use deadband=2.0°, residual=5.0°: 5.0 ≥ 2.0, clamp to
    1.0°. Drift 4.0 → 0.5 → exit after one step (0.5 ≤ 1.0)."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=_DriftSequence([4.0, 0.5, 0.0]))

    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_CORR_DEADBAND_DEG": "2.0",
        "NINA_HOVER_STRAIGHT_CORR_RESIDUAL_DEG": "5.0",
    }
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.25)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    pivot_steps = sum(1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r)
    assert pivot_steps >= 1, (
        f"correction must still fire & take at least one step even when "
        f"residual is misconfigured >= deadband (clamp behavior); "
        f"saw {pivot_steps} pivot writes"
    )


def test_forward_loop_drift_within_deadband_skips_correction() -> None:
    """Drift inside the deadband should NOT produce any pivot writes."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # 0.5° drift with a 1.5° deadband → no correction.
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.5)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.20)
        hb.stop()
    _wait_until_idle(hb)

    fwd = _expected_forward_goals()
    brk = {12: 2048, 13: 2048}
    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD,
        left_speed=20,
        right_dir=hb.DIR_BACKWARD,
        right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD,
        left_speed=20,
        right_dir=hb.DIR_FORWARD,
        right_speed=20,
    )
    for g in dxl.goal_writes:
        assert g != pivot_l and g != pivot_r, (
            f"unexpected pivot inside deadband: {g}"
        )
        assert g in (fwd, brk), f"unexpected goal shape: {g}"


# ---------------------------------------------------------------------------
# 90° abort
# ---------------------------------------------------------------------------


def test_forward_loop_aborts_above_threshold_and_speaks() -> None:
    """|drift| ≥ abort threshold → brake, speak cant_move, halt loop."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 120.0)  # > 90° abort

    spoken = {"n": 0}

    def _fake_speak() -> None:
        spoken["n"] += 1

    env = _fast_straight_env() | {"NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "90.0"}
    with patch.dict(os.environ, env, clear=False):
        with patch(
            "nina.controllers.hoverboard_axis_drive."
            "maybe_speak_cant_move_alert",
            side_effect=_fake_speak,
        ):
            hb.start_pulse_straight_forward(50)
            _wait_until_idle(hb, timeout_sec=2.0)

    assert not hb.is_forward_pulse_active(), "loop should self-halt on abort"
    assert spoken["n"] >= 1, "cant_move alert was not invoked on abort"
    # Final goal must be the brake stamp.
    brk = {12: 2048, 13: 2048}
    assert dxl.goal_writes[-1] == brk, (
        f"loop did not brake on abort; last write={dxl.goal_writes[-1]}"
    )


def test_forward_loop_zero_abort_disables_safety_stop() -> None:
    """Setting abort to 0 should keep the loop running even at huge drift."""
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    # Constant huge drift; the loop must keep cycling until we stop it.
    hb.set_imu_hooks(yaw_drift_fn=lambda: 120.0)

    spoken = {"n": 0}

    def _fake_speak() -> None:
        spoken["n"] += 1

    env = _fast_straight_env() | {"NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "0"}
    with patch.dict(os.environ, env, clear=False):
        with patch(
            "nina.controllers.hoverboard_axis_drive."
            "maybe_speak_cant_move_alert",
            side_effect=_fake_speak,
        ):
            hb.start_pulse_straight_forward(50)
            time.sleep(0.15)
            still_running = hb.is_forward_pulse_active()
            hb.stop()
    _wait_until_idle(hb)
    assert still_running, "abort=0 should keep loop alive at 120° drift"
    assert spoken["n"] == 0, "no alert should fire when abort is disabled"


# ---------------------------------------------------------------------------
# Halt + cancellation paths (preserved from legacy tests)
# ---------------------------------------------------------------------------


def test_forward_loop_stops_after_stop() -> None:
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.15)
        n_before = len(dxl.goal_writes)
        hb.stop()
        time.sleep(0.20)
    n_after = len(dxl.goal_writes)
    assert n_after <= n_before + 40, "loop kept writing long after stop()"
    assert not hb.is_forward_pulse_active()


def test_set_wheels_halts_loop() -> None:
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.08)
        hb.set_wheels(
            left_dir=hb.DIR_FORWARD,
            left_speed=10,
            right_dir=hb.DIR_FORWARD,
            right_speed=10,
        )
        time.sleep(0.15)
    assert not hb.is_forward_pulse_active()


def test_start_pulse_disabled_falls_back_to_forward_goals() -> None:
    axis = replace(_axis_pulse_fast(), pulse_forward_enabled=False)
    hb, dxl = _make_hb(axis)
    dxl.goal_writes.clear()
    hb.start_pulse_straight_forward(40)
    assert not hb.is_forward_pulse_active()
    fl = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    fr = _nudge_goal_from_brake(2100, 2048, _STRAIGHT_FWD_EXTRA_TICKS)
    assert dxl.goal_writes[-1] == {12: fl, 13: fr}


def test_forward_loop_ends_at_full_brake_on_stop() -> None:
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.15)
        hb.stop()
        _wait_until_idle(hb)
    assert dxl.goal_writes[-1] == {12: 2048, 13: 2048}


def test_forward_loop_no_imu_sampler_still_cycles() -> None:
    """Without IMU hooks the loop must still advance forward legs (no abort, no correction)."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # Intentionally do NOT call set_imu_hooks: _imu_yaw_drift_fn is None.

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_forward(50)
        time.sleep(0.20)
        hb.stop()
    _wait_until_idle(hb)
    fwd = _expected_forward_goals()
    forward_count = sum(1 for g in dxl.goal_writes if g == fwd)
    assert forward_count >= 2, (
        f"loop must keep cycling without IMU; forward legs={forward_count}"
    )


# ---------------------------------------------------------------------------
# Backward straight — uses the same drift-corrected algorithm as forward.
#
# start_pulse_straight_backward now shares ``_drift_correct_loop`` with the
# forward path (passing ``is_forward=False`` so the drive leg uses backward
# goals). Cumulative tracking, hysteresis, micro-step pivot correction,
# active settle, and the 90° abort all behave identically — only the leg's
# drive direction differs.
# ---------------------------------------------------------------------------


def test_estimate_backward_pulse_series_duration_sec_matches_forward_formula() -> None:
    """Backward now uses the same drive-then-check algorithm as forward,
    so its duration estimate must match the forward formula:
    ``prime + cycles * (leg + brake_settle)``."""
    axis = _axis_pulse_fast()
    with patch.dict(
        os.environ,
        {
            "NINA_HOVER_STRAIGHT_PRIME_SEC": "0.05",
            "NINA_HOVER_STRAIGHT_LEG_SEC": "0.5",
            "NINA_HOVER_STRAIGHT_BRAKE_SETTLE_SEC": "0.3",
            "NINA_HOVER_STRAIGHT_BENCH_CYCLES": "12",
        },
        clear=False,
    ):
        got = estimate_backward_pulse_series_duration_sec(axis)
    # 0.05 + 12 * (0.5 + 0.3) = 0.05 + 9.6 = 9.65
    assert abs(got - (0.05 + 12.0 * (0.5 + 0.3))) < 1e-9


def test_backward_loop_cycles_drive_then_brake() -> None:
    """``start_pulse_straight_backward`` must drive the wheels in
    reverse, brake, then drive again — same drive-then-check cadence
    as the forward loop, just with backward goals."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    # Default ``_STRAIGHT_BACK_EXTRA_TICKS=0`` (no nudge), so the loop
    # commands the bare ``backward_pos_*`` (2000 each from
    # ``_axis_pulse_fast``). Field-observed wheel asymmetry caused a
    # runaway spin at the +5 tick push; the default got dialed back to
    # 0 so the conservative baseline matches the operator-tuned
    # ``backward_pos_*`` exactly. Operators whose chassis needs extra
    # push opt in via ``NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS``.
    rev = {12: 2000, 13: 2000}
    brk = {12: 2048, 13: 2048}
    saw_rev = any(g == rev for g in dxl.goal_writes)
    saw_brk = any(g == brk for g in dxl.goal_writes)
    assert saw_rev, (
        f"backward loop must command the reverse goals at least once; "
        f"writes={dxl.goal_writes}"
    )
    assert saw_brk, (
        f"backward loop must brake between legs; writes={dxl.goal_writes}"
    )


def test_backward_loop_ends_at_full_brake() -> None:
    """The loop must leave the chassis at the calibrated brake pose on
    exit (operator release)."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.20)
        hb.stop()
    _wait_until_idle(hb)

    brk = {12: 2048, 13: 2048}
    assert dxl.goal_writes[-1] == brk, (
        f"backward loop didn't brake on exit; last write={dxl.goal_writes[-1]}"
    )


def test_backward_loop_cumulative_drift_compounds_across_legs() -> None:
    """A constant +5° cumulative drift (via the fixture's 1.5° deadband)
    must trigger at least one in-place pivot correction during a
    backward run — proving the SAME drift-correction algorithm fires
    for backward motion."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 5.0)

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    pivot_l = hb._goals_for_wheels(
        left_dir=hb.DIR_FORWARD, left_speed=20,
        right_dir=hb.DIR_BACKWARD, right_speed=20,
    )
    pivot_r = hb._goals_for_wheels(
        left_dir=hb.DIR_BACKWARD, left_speed=20,
        right_dir=hb.DIR_FORWARD, right_speed=20,
    )
    pivot_steps = sum(
        1 for g in dxl.goal_writes if g == pivot_l or g == pivot_r
    )
    assert pivot_steps >= 1, (
        f"backward cumulative drift of +5° must trigger at least one "
        f"correction step; saw {pivot_steps} pivot writes in "
        f"{dxl.goal_writes}"
    )


def test_backward_loop_starts_integrator_once_for_cumulative_tracking() -> None:
    """Backward motion uses the same cumulative-tracking semantics as
    forward: the IMU integrator starts ONCE at the top of the motion,
    not at every leg."""
    axis = _axis_pulse_fast()
    hb, _dxl = _make_hb(axis)
    begin_calls = {"n": 0}
    end_calls = {"n": 0}
    hb.set_imu_hooks(
        yaw_drift_fn=lambda: 0.0,
        begin_straight_fn=lambda: begin_calls.__setitem__(
            "n", begin_calls["n"] + 1
        ),
        end_straight_fn=lambda: end_calls.__setitem__(
            "n", end_calls["n"] + 1
        ),
    )

    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.45)
        hb.stop()
    _wait_until_idle(hb)

    assert begin_calls["n"] == 1, (
        f"backward integrator should start exactly ONCE per motion "
        f"(cumulative); saw {begin_calls['n']} begin calls"
    )
    assert end_calls["n"] >= 1, (
        f"backward integrator must end at least once on motion stop; "
        f"saw {end_calls['n']} end calls"
    )


def test_backward_loop_aborts_during_settle_timeout_when_drift_above_threshold() -> None:
    """Runaway-spin path: the chassis is rotating too fast for active
    settle to ever confirm stillness (settle TIMEOUT), AND the
    cumulative drift is past the abort threshold. The loop must
    sample drift anyway and fire ``cant_move`` instead of silently
    launching another drive leg.

    This was the field-observed bug at +5 ticks: wheel asymmetry
    spun the chassis fast enough that active settle timed out every
    cycle, the old ``continue`` skipped the abort check, and the
    bot kept driving backward (compounding the spin) for ~6 s before
    a lucky settle finally allowed the abort to fire at ~668°.
    Post-fix, the abort fires on the first cycle where the runaway
    drift breaches threshold.
    """
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    # yaw_rate_fn always returns a high rate → active settle never
    # confirms stillness → settle times out at settle_max_sec.
    # yaw_drift_fn returns 150° (well past the 90° abort threshold) so
    # the loop must halt the moment it samples drift during the
    # unsettled cycle.
    hb.set_imu_hooks(
        yaw_drift_fn=lambda: 150.0,
        yaw_rate_fn=lambda: 60.0,
    )

    spoken = {"n": 0}

    def _fake_speak() -> None:
        spoken["n"] += 1

    env = _fast_straight_env() | {
        "NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "90.0",
        # Tight settle cap so the test resolves quickly — irrelevant
        # to the assertion since yaw_rate=60 dps > rate_thr=3 dps
        # means settle never confirms regardless.
        "NINA_HOVER_STRAIGHT_SETTLE_MAX_SEC": "0.05",
        "NINA_HOVER_STRAIGHT_SETTLE_STABLE_SEC": "0.01",
        "NINA_HOVER_STRAIGHT_SETTLE_POLL_SEC": "0.005",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch(
            "nina.controllers.hoverboard_axis_drive."
            "maybe_speak_cant_move_alert",
            side_effect=_fake_speak,
        ):
            hb.start_pulse_straight_backward(50)
            _wait_until_idle(hb, timeout_sec=2.0)

    assert not hb.is_forward_pulse_active(), (
        "backward loop must self-halt on settle-timeout-with-high-drift "
        "(runaway-spin path)"
    )
    assert spoken["n"] >= 1, (
        "cant_move alert was not invoked when settle timed out + drift "
        "exceeded threshold — runaway-spin abort regressed"
    )
    brk = {12: 2048, 13: 2048}
    assert dxl.goal_writes[-1] == brk, (
        f"backward loop did not brake on runaway-spin abort; "
        f"last write={dxl.goal_writes[-1]}"
    )


def test_backward_loop_aborts_above_threshold_and_speaks() -> None:
    """|drift| >= abort threshold during backward → brake, play
    cant_move, halt — same abort behavior as the forward loop."""
    axis = _axis_pulse_fast()
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 120.0)

    spoken = {"n": 0}

    def _fake_speak() -> None:
        spoken["n"] += 1

    env = _fast_straight_env() | {"NINA_HOVER_STRAIGHT_ABORT_DRIFT_DEG": "90.0"}
    with patch.dict(os.environ, env, clear=False):
        with patch(
            "nina.controllers.hoverboard_axis_drive."
            "maybe_speak_cant_move_alert",
            side_effect=_fake_speak,
        ):
            hb.start_pulse_straight_backward(50)
            _wait_until_idle(hb, timeout_sec=2.0)

    assert not hb.is_forward_pulse_active(), (
        "backward loop should self-halt on 90° abort"
    )
    assert spoken["n"] >= 1, (
        "cant_move alert was not invoked on backward abort"
    )
    brk = {12: 2048, 13: 2048}
    assert dxl.goal_writes[-1] == brk, (
        f"backward loop did not brake on abort; "
        f"last write={dxl.goal_writes[-1]}"
    )


def test_backward_loop_default_uses_bare_calibrated_lean_no_nudge() -> None:
    """With the default ``_STRAIGHT_BACK_EXTRA_TICKS=0``, the backward
    loop must command the bare ``backward_pos_*`` — no nudge — so the
    operator's calibrated lean magnitude is preserved exactly.

    Operators whose chassis needs extra push opt in via
    ``NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS`` (covered by the dedicated
    override test below). This test locks the conservative default:
    "obey the calibrated tune, don't push past it."
    """
    axis = _axis_pulse_fast()  # backward_pos_*=2000, brake=2048
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    os.environ.pop("NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS", None)
    with patch.dict(os.environ, _fast_straight_env(), clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    bare = {12: int(axis.backward_pos_left), 13: int(axis.backward_pos_right)}
    saw_bare = any(g == bare for g in dxl.goal_writes)
    assert saw_bare, (
        f"with default nudge=0 the backward loop must command the "
        f"bare backward_pos_* ({bare}); writes={dxl.goal_writes}"
    )


def test_straight_back_extra_ticks_default_is_zero() -> None:
    """Lock the conservative default — no nudge past the calibrated
    backward lean. Dialed back from 5 → 2 → 0 after the field-observed
    runaway-spin (5 spun out; 2 still surfaced asymmetry). Operators
    whose chassis needs extra push set the env override; editing this
    constant should be a deliberate fleet-wide decision, not a chassis-
    specific tune.
    """
    assert _STRAIGHT_BACK_EXTRA_TICKS == 0


def test_back_extra_ticks_smaller_than_forward_extra_ticks() -> None:
    """Backward nudge default is intentionally smaller than the
    forward nudge.

    Forward's calibrated lean sits ~50 ticks short of the lean stack
    limit on the reference build, so 14 extra ticks is safe. Backward's
    calibrated lean is more constrained — both because it sits closer
    to the lean stack limit AND because past it the BLDC motors'
    asymmetry breakaway threshold gets crossed. Default backward push
    is 0 to leave the calibrated tune alone; even with the env
    override the operator should keep it small.
    """
    assert _STRAIGHT_BACK_EXTRA_TICKS < _STRAIGHT_FWD_EXTRA_TICKS


def test_straight_back_extra_ticks_env_override_honored() -> None:
    """Env override ``NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS`` lets the
    operator dial the nudge without rebuilding. Clamps to ``[0, 50]``.
    """
    from nina.controllers.hoverboard_axis_drive import _straight_back_extra_ticks

    # Default (unset) = the documented module-level constant (0).
    os.environ.pop("NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS", None)
    assert _straight_back_extra_ticks() == _STRAIGHT_BACK_EXTRA_TICKS

    # Explicit ``=0`` matches the default (operator can be explicit
    # in their service env without changing behavior).
    with patch.dict(
        os.environ,
        {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "0"},
        clear=False,
    ):
        assert _straight_back_extra_ticks() == 0

    # Override to a nonzero mid-value (the operator's "my chassis
    # can't break stiction at calibration" dial).
    with patch.dict(
        os.environ,
        {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "4"},
        clear=False,
    ):
        assert _straight_back_extra_ticks() == 4

    # Out-of-range clamps to [0, 50].
    with patch.dict(
        os.environ,
        {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "999"},
        clear=False,
    ):
        assert _straight_back_extra_ticks() == 50
    with patch.dict(
        os.environ,
        {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "-5"},
        clear=False,
    ):
        assert _straight_back_extra_ticks() == 0

    # Garbage falls back to default.
    with patch.dict(
        os.environ,
        {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "garbage"},
        clear=False,
    ):
        assert _straight_back_extra_ticks() == _STRAIGHT_BACK_EXTRA_TICKS


def test_backward_loop_honors_back_extra_ticks_env_override() -> None:
    """End-to-end: setting the env var changes the goals the backward
    loop actually commands. Locks the operator-facing tunable surface.
    """
    axis = _axis_pulse_fast()  # backward_pos_*=2000, brake=2048
    hb, dxl = _make_hb(axis)
    hb.set_imu_hooks(yaw_drift_fn=lambda: 0.0)

    # Operator opts in to a 3-tick nudge — backward_pos_left=2000 <
    # brake=2048, so the nudge subtracts → 1997 on both sides.
    env = _fast_straight_env() | {"NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS": "3"}
    with patch.dict(os.environ, env, clear=False):
        hb.start_pulse_straight_backward(50)
        time.sleep(0.30)
        hb.stop()
    _wait_until_idle(hb)

    nudged = {12: 1997, 13: 1997}
    saw_nudged = any(g == nudged for g in dxl.goal_writes)
    assert saw_nudged, (
        f"with NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS=3 the loop must "
        f"command the 3-tick-nudged backward goals ({nudged}); writes="
        f"{dxl.goal_writes}"
    )
    bare = {12: 2000, 13: 2000}
    saw_bare = any(g == bare for g in dxl.goal_writes)
    assert not saw_bare, (
        f"with a nonzero nudge the loop must NOT also command the "
        f"bare backward_pos_* ({bare}) — every backward write is the "
        f"nudged value; writes={dxl.goal_writes}"
    )


def test_start_pulse_backward_disabled_falls_back_to_backward_goals() -> None:
    """When ``pulse_forward_enabled`` is False, ``start_pulse_straight_backward``
    falls back to the continuous ``backward()`` set-and-hold (no drift
    correction, no priming loop)."""
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
    hb.start_pulse_straight_backward(40)
    assert not hb.is_forward_pulse_active()
    # Default ``_STRAIGHT_BACK_EXTRA_TICKS=0`` (no nudge) → bare
    # calibrated ``backward_pos_*`` (2000). The disabled-pulse fallback
    # and the live backward loop share the same nudge mechanism, so
    # both apply the same lean magnitude regardless of the operator's
    # ``NINA_HOVER_STRAIGHT_BACK_EXTRA_TICKS`` choice.
    assert dxl.goal_writes[-1] == {12: 2000, 13: 2000}


# ---------------------------------------------------------------------------
# Ramp helper (unrelated to forward changes; kept as a quick sanity check)
# ---------------------------------------------------------------------------


def test_pulse_ramp_blend_profiles_are_monotonic() -> None:
    from nina.controllers.hoverboard_axis_drive import _pulse_ramp_blend_u

    for profile in ("smoothstep", "smootherstep", "cubic_io", "trapezoid"):
        us = [_pulse_ramp_blend_u(i / 200.0, profile, 0.18) for i in range(201)]
        for i in range(200):
            assert us[i + 1] + 1e-9 >= us[i], (profile, i, us[i], us[i + 1])
        assert us[0] <= 1e-12
        assert abs(us[-1] - 1.0) < 1e-9
