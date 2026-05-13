"""Momentary BLDC moves over HTTP (same nav primitives as legacy robot_bridge)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.drive_http")

_valid = frozenset({"forward", "back", "left", "right", "stop"})

_last_drive_error_lock = threading.Lock()
_last_drive_error: Optional[str] = None


def _set_last_drive_error(msg: Optional[str]) -> None:
    global _last_drive_error
    with _last_drive_error_lock:
        _last_drive_error = (msg or "").strip() or None


def peek_last_drive_error() -> Optional[str]:
    with _last_drive_error_lock:
        return _last_drive_error


def _autonomy_blocks(service: NinaService) -> bool:
    try:
        if service._autonomy is not None:  # noqa: SLF001
            return bool(service.autonomy.is_enabled())
    except Exception:
        log.exception("autonomy check")
    return False


def momentary_drive(
    service: NinaService,
    *,
    direction: str,
    duration_ms: int,
    speed_percent: int,
) -> Dict[str, Any]:
    direction = direction.strip().lower()
    if direction not in _valid:
        return {"ok": False, "error": f"invalid direction {direction!r}"}
    duration_ms = max(50, min(5000, int(duration_ms)))
    speed_percent = max(5, min(100, int(speed_percent)))
    d_sec = duration_ms / 1000.0

    if _autonomy_blocks(service):
        return {
            "ok": False,
            "error": "autonomy active — disable autonomy before HTTP drive",
        }

    dc = service.drive

    def run() -> None:
        try:
            if dc._nav is None:  # noqa: SLF001
                dc._do_init()  # noqa: SLF001
            nav = dc._nav  # noqa: SLF001
            if nav is None:
                _set_last_drive_error("navigation not initialized")
                return
            if direction == "stop":
                nav.stop()
                _set_last_drive_error(None)
                return
            if direction == "forward":
                nav.forward(speed_percent=speed_percent)
                time.sleep(d_sec)
                nav.stop()
            elif direction == "back":
                nav.backward(speed_percent=speed_percent)
                time.sleep(d_sec)
                nav.stop()
            elif direction == "left":
                nav.turn_left(speed_percent=speed_percent, duration=d_sec)
            elif direction == "right":
                nav.turn_right(speed_percent=speed_percent, duration=d_sec)
            _set_last_drive_error(None)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.exception("momentary_drive %s", direction)
            _set_last_drive_error(msg)

    dc._enqueue(run)  # type: ignore[attr-defined]
    return {"ok": True, "queued": True, "direction": direction, "duration_ms": duration_ms}


def navigation_hw_status(service: NinaService) -> Dict[str, Any]:
    err = peek_last_drive_error()
    dc = service.drive
    try:
        dc.ensure_hardware()
        nav = dc._nav  # noqa: SLF001
        if nav is None:
            # init still in flight
            st = dc.state()
            body: Dict[str, Any] = {
                "ok": True,
                "connected": bool(st.get("connected")),
                "message": str(st.get("driver_message", "")),
                "invert_left": bool(st.get("invert_left", False)),
                "invert_right": bool(st.get("invert_right", False)),
            }
            if err:
                body["last_drive_error"] = err
            return body
        body = {
            "ok": True,
            "connected": True,
            "message": "BLDC L+R connected",
            "invert_left": bool(nav.get_invert_left()),
            "invert_right": bool(nav.get_invert_right()),
        }
        if err:
            body["last_drive_error"] = err
        return body
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        out: Dict[str, Any] = {
            "ok": True,
            "connected": False,
            "message": msg,
            "invert_left": False,
            "invert_right": False,
        }
        if err:
            out["last_drive_error"] = err
        return out


def set_wheel_invert(
    service: NinaService,
    *,
    left: Optional[bool] = None,
    right: Optional[bool] = None,
) -> Dict[str, Any]:
    if left is None and right is None:
        return {"ok": False, "error": "no fields: set left and/or right"}
    dc = service.drive
    if left is not None:
        dc.set_invert_left(bool(left))
    if right is not None:
        dc.set_invert_right(bool(right))
    nav = dc._nav  # noqa: SLF001
    if nav is not None:
        return {
            "ok": True,
            "invert_left": bool(nav.get_invert_left()),
            "invert_right": bool(nav.get_invert_right()),
        }
    st = dc.state()
    return {
        "ok": True,
        "invert_left": bool(st.get("invert_left", False)),
        "invert_right": bool(st.get("invert_right", False)),
    }


def emergency_stop(service: NinaService) -> Dict[str, Any]:
    dc = service.drive

    def run() -> None:
        try:
            if dc._nav is None:  # noqa: SLF001
                dc._do_init()  # noqa: SLF001
            nav = dc._nav  # noqa: SLF001
            if nav is None:
                return
            nav.emergency_stop()
            _set_last_drive_error(None)
        except Exception:
            log.exception("emergency_stop")

    dc._enqueue(run)  # type: ignore[attr-defined]
    return {"ok": True, "queued": True}
