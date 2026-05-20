"""Tablet HTTP helpers for hoverboard bench, motion calibration, and rich drive status."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Dict, Optional

from nina.config.settings import HoverboardAxisSettings
from nina.config.hover_calibration import read_hover_calibration_ints
from nina.controllers.hoverboard_axis_drive import (
    estimate_backward_pulse_series_duration_sec,
    estimate_forward_pulse_series_duration_sec,
    hover_computed_turn_pivot_goals,
)
from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.straight_bench_speed import straight_bench_speed_pct

log = logging.getLogger("sirena_ui.android_gateway.tablet_drive_extras")


def _autonomy_blocks(service: NinaService) -> bool:
    try:
        if service._autonomy is not None:  # noqa: SLF001
            return bool(service.autonomy.is_enabled())
    except Exception:
        log.exception("autonomy check")
    return False


def drive_status_payload(service: NinaService) -> Dict[str, Any]:
    """Extended drive snapshot for tablet HUD (matches kiosk ``drive_screen``)."""
    from sirena_ui.android_gateway.drive_http import navigation_hw_status, peek_last_drive_error

    body = navigation_hw_status(service)
    dc = service.drive
    try:
        st = dc.state()
        body["speed_pct"] = int(st.get("speed_pct", 0))
        body["heading_deg"] = float(st.get("heading_deg", 0.0))
        body["distance_m"] = float(st.get("distance_m", 0.0))
        body["direction"] = str(st.get("direction", "idle"))
        body["reverse"] = bool(st.get("reverse", False))
    except Exception as exc:
        log.debug("drive state merge: %s", exc)

    drift_side = "n/a"
    drift_deg: Optional[float] = None
    try:
        mon = service.imu_monitor
        if mon is None:
            drift_side = "off"
        else:
            s = mon.snapshot()
            drift_deg = float(s.yaw_drift_deg)
            drift_side = str(s.drift_side)
    except Exception:
        pass
    body["imu_drift_deg"] = drift_deg
    body["imu_drift_side"] = drift_side

    straight_active = False
    try:
        nav = dc._nav  # noqa: SLF001
        if nav is not None and hasattr(nav, "is_straight_pulse_series_active"):
            straight_active = bool(nav.is_straight_pulse_series_active())
    except Exception:
        pass
    body["straight_pulse_active"] = straight_active

    err = peek_last_drive_error()
    if err:
        body["last_drive_error"] = err
    return body


def _straight_fwd_duration_ms(
    axis: HoverboardAxisSettings, *, use_forward_pulse_bench: bool
) -> int:
    """Mirror ``DriveScreen._straight_sequence_spec`` (single segment)."""
    raw = (os.environ.get("NINA_STRAIGHT_TEST_MS") or "").strip()
    if not raw:
        raw = (os.environ.get("NINA_STRAIGHT_SEQ_FWD1_MS") or "").strip()
    if raw:
        try:
            ms = int(raw)
        except ValueError:
            ms = 40_000
    elif use_forward_pulse_bench:
        est_sec = estimate_forward_pulse_series_duration_sec(axis)
        ms = min(120_000, max(100, int(math.ceil(est_sec * 1000.0)) + 200))
    else:
        ms = 40_000
    return max(100, min(120_000, ms))


def _straight_back_duration_ms(
    axis: HoverboardAxisSettings, *, use_backward_pulse_bench: bool
) -> int:
    """Mirror ``DriveScreen._straight_back_sequence_spec``."""
    raw = (os.environ.get("NINA_STRAIGHT_BACK_TEST_MS") or "").strip()
    if raw:
        try:
            ms = int(raw)
        except ValueError:
            ms = 40_000
    elif use_backward_pulse_bench:
        est_sec = estimate_backward_pulse_series_duration_sec(axis)
        ms = min(120_000, max(100, int(math.ceil(est_sec * 1000.0)) + 200))
    else:
        ms = 40_000
    return max(100, min(120_000, ms))


def _schedule_straight_fallback_stop(
    service: NinaService,
    dc: DriveController,
    *,
    duration_ms: int,
) -> None:
    """Non-pulse straight segment: timed ``drive_wheels`` then stop (kiosk fallback)."""

    def _run() -> None:
        time.sleep(max(0.1, duration_ms / 1000.0))
        try:
            dc.stop(drain=True)
        except Exception:
            try:
                dc.stop()
            except Exception:
                pass
        try:
            service.imu_straight_end()
        except Exception:
            log.exception("imu_straight_end after fallback straight")

    threading.Thread(
        target=_run,
        daemon=True,
        name="tablet-straight-fallback",
    ).start()


def hover_straight_start(service: NinaService, *, backward: bool) -> Dict[str, Any]:
    """Match kiosk ``DriveScreen._begin_straight_bench_run`` / ``_apply_straight_sequence_segment``."""
    if _autonomy_blocks(service):
        return {"ok": False, "error": "autonomy active — disable autonomy first"}
    from sirena_ui.android_gateway.drive_http import (
        _drive_not_ready_response,
        _prime_drive_hardware,
    )

    prime = _prime_drive_hardware(service, wait_timeout_sec=10.0)
    blocked = _drive_not_ready_response(prime)
    if blocked is not None:
        return blocked
    dc = service.drive
    st = dc.state()
    if not bool(st.get("connected")):
        msg = str(st.get("driver_message", "")).strip()
        detail = msg or "hoverboard driver not connected"
        return {
            "ok": False,
            "error": (
                f"{detail}. Wait for drive status green, brake off, then try again."
            ),
        }
    with dc._lock:  # noqa: SLF001
        if dc._state.get("brake"):  # noqa: SLF001
            return {"ok": False, "error": "Release brake before running a straight test."}

    service.imu_straight_begin()
    sp = straight_bench_speed_pct()
    axis = service.settings.hoverboard_axis
    pulse_supported = dc.supports_forward_pulse()

    if backward:
        seq_dir = "back"
        duration_ms = _straight_back_duration_ms(
            axis, use_backward_pulse_bench=pulse_supported
        )
    else:
        seq_dir = "back" if bool(st.get("reverse")) else "forward"
        if seq_dir == "forward":
            duration_ms = _straight_fwd_duration_ms(
                axis, use_forward_pulse_bench=pulse_supported
            )
        else:
            duration_ms = _straight_back_duration_ms(
                axis, use_backward_pulse_bench=pulse_supported
            )

    used_pulse_bench = False
    if seq_dir == "forward" and pulse_supported:
        dc.start_forward_pulse_bench(sp)
        used_pulse_bench = True
    elif seq_dir == "back" and pulse_supported:
        dc.start_backward_pulse_bench(sp)
        used_pulse_bench = True
    else:
        dc.drive_wheels(seq_dir, sp, seq_dir, sp)
        _schedule_straight_fallback_stop(service, dc, duration_ms=duration_ms)

    return {
        "ok": True,
        "backward": backward,
        "seq_direction": seq_dir,
        "speed_percent": sp,
        "duration_ms": duration_ms,
        "pulse_bench": used_pulse_bench,
    }


def hover_straight_stop(service: NinaService) -> Dict[str, Any]:
    """Match kiosk ``DriveScreen._restore_after_straight_test`` / ``_finish_straight_test``."""
    dc = service.drive
    try:
        dc.stop(drain=True)
    except Exception:
        try:
            dc.stop()
        except Exception:
            log.exception("straight stop")
    try:
        service.imu_straight_end()
    except Exception:
        log.exception("imu_straight_end")
    return {"ok": True}


def hover_calibration_snapshot(service: NinaService) -> Dict[str, Any]:
    ax = service.settings.hoverboard_axis
    nav = service.settings.navigation
    tl0, tl1 = hover_computed_turn_pivot_goals(ax, turn_left=True)
    tr0, tr1 = hover_computed_turn_pivot_goals(ax, turn_left=False)
    file_ints = read_hover_calibration_ints()
    return {
        "ok": True,
        "id_left": int(ax.id_left),
        "id_right": int(ax.id_right),
        "brake_pos_left": int(ax.brake_pos_left),
        "brake_pos_right": int(ax.brake_pos_right),
        "forward_pos_left": int(ax.forward_pos_left),
        "forward_pos_right": int(ax.forward_pos_right),
        "backward_pos_left": int(ax.backward_pos_left),
        "backward_pos_right": int(ax.backward_pos_right),
        "turn_left_pos_left": int(
            ax.turn_left_pos_left if ax.turn_left_pos_left is not None else tl0
        ),
        "turn_left_pos_right": int(
            ax.turn_left_pos_right if ax.turn_left_pos_right is not None else tl1
        ),
        "turn_right_pos_left": int(
            ax.turn_right_pos_left if ax.turn_right_pos_left is not None else tr0
        ),
        "turn_right_pos_right": int(
            ax.turn_right_pos_right if ax.turn_right_pos_right is not None else tr1
        ),
        "turn_duration_sec": float(nav.turn_duration_sec),
        "file_overrides": file_ints,
    }


def hover_calibration_preview(
    service: NinaService, *, left: int, right: int
) -> Dict[str, Any]:
    try:
        service.preview_hover_lean_positions(int(left), int(right))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def hover_calibration_neutral(service: NinaService) -> Dict[str, Any]:
    try:
        service.park_hoverboard_brake()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def hover_calibration_persist(
    service: NinaService,
    *,
    forward_pos_left: Optional[int] = None,
    forward_pos_right: Optional[int] = None,
    backward_pos_left: Optional[int] = None,
    backward_pos_right: Optional[int] = None,
    turn_left_pos_left: Optional[int] = None,
    turn_left_pos_right: Optional[int] = None,
    turn_right_pos_left: Optional[int] = None,
    turn_right_pos_right: Optional[int] = None,
    turn_duration_sec: Optional[float] = None,
) -> Dict[str, Any]:
    try:
        service.persist_hover_lean_calibration(
            forward_pos_left=forward_pos_left,
            forward_pos_right=forward_pos_right,
            backward_pos_left=backward_pos_left,
            backward_pos_right=backward_pos_right,
            turn_left_pos_left=turn_left_pos_left,
            turn_left_pos_right=turn_left_pos_right,
            turn_right_pos_left=turn_right_pos_left,
            turn_right_pos_right=turn_right_pos_right,
            turn_duration_sec=turn_duration_sec,
        )
        service.park_hoverboard_brake()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
