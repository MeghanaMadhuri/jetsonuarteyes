"""Saved movement sequences: 180° U-turn, gentle turns, correction caps."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from nina.controllers.hoverboard_axis_drive import (
    _sequence_turn_max_steps_for_deg,
    _straight_corr_max_drift_correct_deg,
)
from nina.movements.executor import execute_saved_movement
from nina.movements.model import STEP_FORWARD, STEP_UTURN, MovementStep, SavedMovement


def test_sequence_turn_max_steps_scales_with_corr_step_deg() -> None:
    # Default corr step 5° → 180/5 + 2 = 38
    assert _sequence_turn_max_steps_for_deg(180.0) == 38
    assert _sequence_turn_max_steps_for_deg(90.0) == 20


def test_straight_corr_max_drift_default() -> None:
    assert _straight_corr_max_drift_correct_deg() == 45.0


def test_executor_uturn_requests_180_degrees() -> None:
    calls: list[tuple[str, float]] = []

    def pulse_turn(direction: str, degrees: float) -> bool:
        calls.append((direction, degrees))
        return True

    nav = SimpleNamespace(
        _is_initialized=True,
        release_brake=lambda: None,
        begin_saved_sequence=lambda: None,
        end_saved_sequence=lambda: None,
        post_turn_settle_for_sequence=lambda: None,
        pulse_turn_degrees=pulse_turn,
        start_pulse_straight_forward=MagicMock(),
        start_pulse_straight_backward=MagicMock(),
        is_forward_pulse_enabled=lambda: True,
        is_forward_pulse_active=lambda: False,
        _pulse_halt=None,
        stop=lambda: None,
    )

    mv = SavedMovement(
        name="out and back",
        steps=[
            MovementStep(kind=STEP_FORWARD, seconds=1.0),
            MovementStep(kind=STEP_UTURN, uturn_direction="left"),
            MovementStep(kind=STEP_FORWARD, seconds=1.0),
        ],
    )
    err = execute_saved_movement(nav, mv)
    assert err is None
    assert calls == [("left", 180.0)]


def test_executor_wraps_sequence_begin_end() -> None:
    flags: list[str] = []

    nav = SimpleNamespace(
        _is_initialized=True,
        release_brake=lambda: None,
        begin_saved_sequence=lambda: flags.append("begin"),
        end_saved_sequence=lambda: flags.append("end"),
        pulse_turn_degrees=lambda *_a, **_k: True,
        post_turn_settle_for_sequence=lambda: None,
        start_pulse_straight_forward=MagicMock(),
        start_pulse_straight_backward=MagicMock(),
        is_forward_pulse_enabled=lambda: True,
        is_forward_pulse_active=lambda: False,
        _pulse_halt=None,
        stop=lambda: None,
    )
    mv = SavedMovement(
        name="one leg",
        steps=[MovementStep(kind=STEP_FORWARD, seconds=0.1)],
    )
    assert execute_saved_movement(nav, mv) is None
    assert flags == ["begin", "end"]
