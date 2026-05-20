"""Momentary BLDC moves over HTTP (same nav primitives as legacy robot_bridge)."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Dict, Optional

from sirena_ui.workers.drive_controller import DriveController
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.drive_http")

_valid = frozenset({"forward", "back", "left", "right", "stop"})
_hold_dirs = frozenset({"forward", "back", "left", "right"})
_turn_dirs = frozenset({"left", "right"})

_last_drive_error_lock = threading.Lock()
_last_drive_error: Optional[str] = None


def _set_last_drive_error(msg: Optional[str]) -> None:
    global _last_drive_error
    with _last_drive_error_lock:
        _last_drive_error = (msg or "").strip() or None


def peek_last_drive_error() -> Optional[str]:
    with _last_drive_error_lock:
        return _last_drive_error


def _json_safe_float(value: Any) -> Optional[float]:
    """Drop NaN/Inf so FastAPI JSON encoding cannot 500 the tablet poll."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _sanitize_drive_status_body(body: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("heading_deg", "distance_m", "imu_drift_deg"):
        if key in body:
            body[key] = _json_safe_float(body.get(key))
    return body


def _autonomy_blocks(service: NinaService) -> bool:
    try:
        if service._autonomy is not None:  # noqa: SLF001
            return bool(service.autonomy.is_enabled())
    except Exception:
        log.exception("autonomy check")
    return False


def _prime_drive_hardware(
    service: NinaService,
    *,
    wait_timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """Bring up Dynamixel bus + BLDC nav for tablet HTTP (no kiosk Drive screen).

    Kiosk ``DriveScreen.on_enter`` only called ``ensure_hardware()``; the bus is
    normally started from ``MainWindow``, but HTTP must not depend on that screen.
    """
    try:
        service.ensure_bus()
    except Exception as exc:
        log.exception("ensure_bus before tablet drive")
        return {"ok": False, "ready": False, "error": f"Dynamixel bus: {exc}"}
    try:
        service.start_mpu9250_imu_monitor()
    except Exception:
        log.debug("IMU monitor start skipped", exc_info=True)
    dc = service.drive
    dc.ensure_hardware()
    if dc._nav is not None:  # noqa: SLF001
        return {"ok": True, "ready": True}
    deadline = time.monotonic() + max(0.0, float(wait_timeout_sec))
    while time.monotonic() < deadline:
        if dc._nav is not None:  # noqa: SLF001
            return {"ok": True, "ready": True}
        st = dc.state()
        msg = str(st.get("driver_message", "") or "").strip()
        if "init failed" in msg.lower():
            return {"ok": False, "ready": False, "error": msg or "BLDC init failed"}
        time.sleep(0.05)
    msg = str(dc.state().get("driver_message", "") or "").strip()
    return {
        "ok": True,
        "ready": False,
        "hardware_initializing": True,
        "error": msg or "BLDC still initializing — retry in a moment",
    }


def _drive_not_ready_response(prime: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if prime.get("ready"):
        return None
    out: Dict[str, Any] = {
        "ok": False,
        "error": str(prime.get("error") or "BLDC not ready"),
    }
    if prime.get("hardware_initializing"):
        out["hardware_initializing"] = True
    return out


def bootstrap_tablet_drive(service: NinaService) -> None:
    """Best-effort BLDC bring-up when the robot bridge starts (background)."""
    _prime_drive_hardware(service, wait_timeout_sec=15.0)


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

    prime = _prime_drive_hardware(service, wait_timeout_sec=10.0)
    blocked = _drive_not_ready_response(prime)
    if blocked is not None:
        return blocked

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
                dc._do_stop()  # noqa: SLF001
                _set_last_drive_error(None)
                return
            # On the drive worker already: call wheel ops directly (do not
            # re-enqueue via ``drive_wheels`` — would deadlock the worker).
            if direction == "forward":
                dc._do_drive_wheels(  # noqa: SLF001
                    "forward",
                    speed_percent,
                    "forward",
                    speed_percent,
                )
                time.sleep(d_sec)
                dc._do_stop()  # noqa: SLF001
            elif direction == "back":
                dc._do_drive_wheels(  # noqa: SLF001
                    "back",
                    speed_percent,
                    "back",
                    speed_percent,
                )
                time.sleep(d_sec)
                dc._do_stop()  # noqa: SLF001
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


def drive_hold_start(service: NinaService, *, direction: str) -> Dict[str, Any]:
    """Match kiosk D-pad press: ``DriveController.drive`` until release."""
    direction = direction.strip().lower()
    if direction not in _hold_dirs:
        return {"ok": False, "error": f"invalid direction {direction!r}"}
    if _autonomy_blocks(service):
        return {
            "ok": False,
            "error": "autonomy active — disable autonomy before manual drive",
        }
    prime = _prime_drive_hardware(service, wait_timeout_sec=10.0)
    blocked = _drive_not_ready_response(prime)
    if blocked is not None:
        return blocked
    dc = service.drive
    with dc._lock:  # noqa: SLF001
        if dc._state.get("brake"):  # noqa: SLF001
            return {"ok": False, "error": "Release brake to drive."}
    dc.drive(direction)
    _set_last_drive_error(None)
    return {"ok": True, "direction": direction, "mode": "hold"}


def drive_hold_stop(service: NinaService) -> Dict[str, Any]:
    """Match kiosk D-pad release: ``DriveController.stop``."""
    service.drive.stop()
    _set_last_drive_error(None)
    return {"ok": True, "mode": "hold_stop"}


def drive_turn(service: NinaService, *, which: str) -> Dict[str, Any]:
    """Match kiosk **Turn left/right** (``DriveController.turn_90``)."""
    which = which.strip().lower()
    if which not in _turn_dirs:
        return {"ok": False, "error": f"which must be 'left' or 'right', got {which!r}"}
    if _autonomy_blocks(service):
        return {
            "ok": False,
            "error": "autonomy active — disable autonomy before manual turns",
        }
    prime = _prime_drive_hardware(service, wait_timeout_sec=10.0)
    blocked = _drive_not_ready_response(prime)
    if blocked is not None:
        return blocked
    dc = service.drive
    with dc._lock:  # noqa: SLF001
        if dc._state.get("brake"):  # noqa: SLF001
            return {"ok": False, "error": "Release brake before running a turn."}
    dc.turn_90(which)
    _set_last_drive_error(None)
    return {"ok": True, "which": which, "queued": True}


def set_drive_reverse(service: NinaService, *, on: bool) -> Dict[str, Any]:
    """Match kiosk reverse pill (``DriveController.set_reverse``)."""
    dc = service.drive
    dc.set_reverse(bool(on))
    st = dc.state()
    return {"ok": True, "reverse": bool(st.get("reverse", on))}


def _kick_drive_for_status_poll(service: NinaService) -> None:
    """Non-blocking bring-up for ``GET /v1/robot/drive/status`` (kiosk + tablet).

    Unlike ``_prime_drive_hardware``, this must not ``time.sleep`` on the Qt GUI
    thread — Android polls every ~2.5 s and the command plane only drains a few
    jobs per tick. A multi-second wait there starves the event loop and surfaces
    as HTTP 500 / "drive status unreachable" while ``/health`` still works.
    """
    if not service.bus_ready:
        try:
            service.ensure_bus()
        except Exception as exc:
            log.warning("ensure_bus during drive status poll: %s", exc)
    try:
        service.start_mpu9250_imu_monitor()
    except Exception:
        log.debug("IMU monitor start skipped", exc_info=True)
    service.drive.ensure_hardware()


def navigation_hw_status(service: NinaService) -> Dict[str, Any]:
    """Read-only drive snapshot for HTTP polling (runs on the Qt GUI thread)."""
    err = peek_last_drive_error()
    _kick_drive_for_status_poll(service)
    dc = service.drive
    try:
        nav = dc._nav  # noqa: SLF001
        st = dc.state()
        brake = bool(st.get("brake", True))
        if nav is None:
            msg = str(st.get("driver_message", "") or "").strip()
            connected = bool(st.get("connected"))
            failed = "init failed" in msg.lower() or "failed" in msg.lower()
            if not msg:
                msg = "BLDC initializing…" if not failed else msg
            body: Dict[str, Any] = {
                "ok": True,
                "connected": connected,
                "hardware_initializing": not connected and not failed,
                "message": msg,
                "invert_left": bool(st.get("invert_left", False)),
                "invert_right": bool(st.get("invert_right", False)),
                "brake": brake,
            }
            if err:
                body["last_drive_error"] = err
            return _sanitize_drive_status_body(body)
        invert_left = False
        invert_right = False
        if hasattr(nav, "get_invert_left"):
            invert_left = bool(nav.get_invert_left())
        if hasattr(nav, "get_invert_right"):
            invert_right = bool(nav.get_invert_right())
        body = {
            "ok": True,
            "connected": True,
            "message": "BLDC L+R connected",
            "invert_left": invert_left,
            "invert_right": invert_right,
            "brake": brake,
        }
        if err:
            body["last_drive_error"] = err
        return _sanitize_drive_status_body(body)
    except Exception as exc:
        log.exception("navigation_hw_status")
        msg = f"{type(exc).__name__}: {exc}"
        out: Dict[str, Any] = {
            "ok": True,
            "connected": False,
            "message": msg,
            "invert_left": False,
            "invert_right": False,
            "brake": bool(dc.state().get("brake", True)),
        }
        if err:
            out["last_drive_error"] = err
        return _sanitize_drive_status_body(out)


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


def robot_set_brake(service: NinaService, *, on: bool) -> Dict[str, Any]:
    """Match kiosk ``DriveController.set_brake`` (servo brake pose)."""
    if _autonomy_blocks(service) and not on:
        return {
            "ok": False,
            "error": "autonomy active — disable autonomy before releasing brake",
        }
    prime = _prime_drive_hardware(service, wait_timeout_sec=10.0)
    if not on:
        blocked = _drive_not_ready_response(prime)
        if blocked is not None:
            return blocked
    dc = service.drive
    dc.set_brake(bool(on))
    st = dc.state()
    return {"ok": True, "brake": bool(st.get("brake", on))}


def emergency_stop(service: NinaService) -> Dict[str, Any]:
    _prime_drive_hardware(service, wait_timeout_sec=10.0)
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
