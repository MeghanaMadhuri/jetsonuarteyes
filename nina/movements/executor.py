"""Run a :class:`SavedMovement` on a hoverboard drive backend."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from nina.movements.model import (
    STEP_BACKWARD,
    STEP_BRAKE,
    STEP_FORWARD,
    STEP_TURN_LEFT,
    STEP_TURN_RIGHT,
    STEP_UTURN,
    SavedMovement,
)

log = logging.getLogger("nina.movements.executor")

_DEFAULT_STRAIGHT_SPEED_PCT = 20


def execute_saved_movement(nav: Any, movement: SavedMovement) -> Optional[str]:
    """Run ``movement`` on ``nav`` (``HoverboardAxisDrive``). Returns error text or None."""
    if nav is None:
        return "Drive hardware is not ready"
    if not getattr(nav, "_is_initialized", False):
        return "Drive hardware is not initialized"

    steps = movement.steps
    if not steps:
        return "Sequence has no steps"

    release = getattr(nav, "release_brake", None)
    if callable(release):
        try:
            release()
        except Exception as exc:
            log.warning("release_brake before sequence: %s", exc)

    begin_seq = getattr(nav, "begin_saved_sequence", None)
    end_seq = getattr(nav, "end_saved_sequence", None)
    if callable(begin_seq):
        begin_seq()
    try:
        for index, step in enumerate(steps, start=1):
            log.info(
                "saved movement %r step %d/%d: %s",
                movement.name,
                index,
                len(steps),
                step.summary(),
            )
            try:
                err = _run_step(nav, step)
            except Exception as exc:
                log.exception("saved movement step failed: %s", step.summary())
                return f"Step {index} failed: {exc}"
            if err:
                return f"Step {index}: {err}"
        return None
    finally:
        if callable(end_seq):
            end_seq()
        try:
            nav.stop()
        except Exception:
            pass


def _release_brake_for_motion(nav: Any) -> None:
    release = getattr(nav, "release_brake", None)
    if callable(release):
        try:
            release()
        except Exception as exc:
            log.warning("release_brake before motion step: %s", exc)


def _run_step(nav: Any, step: Any) -> Optional[str]:
    kind = step.kind
    if kind == STEP_BRAKE:
        return _run_brake(nav)
    if kind == STEP_FORWARD:
        _release_brake_for_motion(nav)
        return _run_straight(nav, forward=True, seconds=float(step.seconds))
    if kind == STEP_BACKWARD:
        _release_brake_for_motion(nav)
        return _run_straight(nav, forward=False, seconds=float(step.seconds))
    if kind == STEP_TURN_LEFT:
        _release_brake_for_motion(nav)
        return _run_turn(nav, "left", float(step.degrees))
    if kind == STEP_TURN_RIGHT:
        _release_brake_for_motion(nav)
        return _run_turn(nav, "right", float(step.degrees))
    if kind == STEP_UTURN:
        _release_brake_for_motion(nav)
        direction = "left" if step.uturn_direction == "left" else "right"
        # True U-turn = 180° heading reversal (not a full 360° spin).
        return _run_turn(nav, direction, 180.0)
    return f"Unknown step kind {kind!r}"


def _run_brake(nav: Any) -> Optional[str]:
    engage = getattr(nav, "engage_brake", None)
    if not callable(engage):
        stop = getattr(nav, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception as exc:
                return f"Brake stop failed: {exc}"
            return None
        return "Brake is not available on this drive backend"
    try:
        engage()
    except Exception as exc:
        log.exception("engage_brake in saved sequence")
        return f"Brake failed: {exc}"
    return None


def _run_straight(nav: Any, *, forward: bool, seconds: float) -> Optional[str]:
    sec = float(seconds)
    if sec <= 0.0:
        return "Forward/back duration must be positive"
    start_fwd = getattr(nav, "start_pulse_straight_forward", None)
    start_back = getattr(nav, "start_pulse_straight_backward", None)
    if not callable(start_fwd) or not callable(start_back):
        return "Straight pulse drive is not available on this robot"
    if not getattr(nav, "is_forward_pulse_enabled", lambda: False)():
        return "Enable NINA_HOVER_PULSE_FORWARD for saved straight segments"

    if forward:
        start_fwd(_DEFAULT_STRAIGHT_SPEED_PCT)
    else:
        start_back(_DEFAULT_STRAIGHT_SPEED_PCT)

    halt = getattr(nav, "_pulse_halt", None)
    deadline = time.monotonic() + sec
    while time.monotonic() < deadline:
        if halt is not None and halt.is_set():
            break
        if not getattr(nav, "is_forward_pulse_active", lambda: False)():
            break
        time.sleep(0.05)
    nav.stop()
    return None


def _run_turn(nav: Any, direction: str, degrees: float) -> Optional[str]:
    deg = float(degrees)
    if deg <= 0.0:
        return "Turn angle must be positive"
    turn_fn = getattr(nav, "pulse_turn_degrees", None)
    if not callable(turn_fn):
        turn_fn = getattr(nav, "pulse_turn_90", None)
        if not callable(turn_fn):
            return "IMU turn API is not available"
        ok = bool(turn_fn(direction))
    else:
        ok = bool(turn_fn(direction, deg))
    if not ok:
        return f"Turn {direction} {deg:.0f}° did not finish within IMU deadband"
    settle = getattr(nav, "post_turn_settle_for_sequence", None)
    if callable(settle):
        settle()
    return None
