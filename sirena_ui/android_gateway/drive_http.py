"""Momentary BLDC moves over HTTP (same nav primitives as legacy robot_bridge)."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
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


def _drive_hardware_ready(service: NinaService) -> bool:
    """True when hoverboard nav is up and the Drive pill would show connected."""
    dc = getattr(service, "_drive", None)
    if dc is None:
        return False
    try:
        if dc._nav is None:  # noqa: SLF001
            return False
        return bool(dc.state().get("connected"))
    except Exception:
        return False


def _prime_drive_hardware(
    service: NinaService,
    *,
    wait_timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """Kick BLDC nav initialization without blocking the Qt GUI thread.

    The embedded FastAPI gateway runs these helpers through ``QtCommandPlane``.
    That means this function must not call ``ensure_bus()`` or poll with
    ``time.sleep``. MainWindow already brings up the Dynamixel bus on a QThread;
    once that is ready, ``DriveController.ensure_hardware()`` queues the hover
    initialization on the drive worker and returns immediately.
    """
    _ = wait_timeout_sec
    if _drive_hardware_ready(service):
        return {"ok": True, "ready": True}
    if not service.bus_ready:
        return {
            "ok": True,
            "ready": False,
            "hardware_initializing": True,
            "error": "Dynamixel bus still initializing — retry in a moment",
        }
    try:
        service.start_mpu9250_imu_monitor()
    except Exception:
        log.debug("IMU monitor start skipped", exc_info=True)
    dc = service.drive
    dc.ensure_hardware()
    if _drive_hardware_ready(service):
        return {"ok": True, "ready": True}
    msg = str(dc.state().get("driver_message", "") or "").strip()
    if "init failed" in msg.lower():
        return {"ok": False, "ready": False, "error": msg or "BLDC init failed"}
    return {
        "ok": True,
        "ready": False,
        "hardware_initializing": True,
        "error": msg or "BLDC still initializing — retry in a moment",
    }


def _prime_drive_for_manual(
    service: NinaService,
    *,
    wait_timeout_sec: float = 1.2,
) -> Dict[str, Any]:
    """Fast path for D-pad / momentary: never block the Qt event loop."""
    _ = wait_timeout_sec
    if _drive_hardware_ready(service):
        return {"ok": True, "ready": True}
    return _prime_drive_hardware(service, wait_timeout_sec=0.0)


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
    """Best-effort BLDC bring-up when the robot bridge starts."""
    _prime_drive_hardware(service, wait_timeout_sec=0.0)


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

    prime = _prime_drive_for_manual(service, wait_timeout_sec=1.2)
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
    prime = _prime_drive_for_manual(service, wait_timeout_sec=1.2)
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
    service.drive.stop(drain=True)
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
    prime = _prime_drive_for_manual(service, wait_timeout_sec=1.2)
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


def navigation_hw_status(service: NinaService) -> Dict[str, Any]:
    """Read-only drive snapshot for HTTP polling (runs on the Qt GUI thread)."""
    err = peek_last_drive_error()
    dc = getattr(service, "_drive", None)
    if dc is None:
        body: Dict[str, Any] = {
            "ok": True,
            "connected": False,
            "hardware_initializing": bool(getattr(service, "bus_ready", False)),
            "message": "Drive controller not started",
            "invert_left": False,
            "invert_right": False,
            "brake": True,
        }
        if err:
            body["last_drive_error"] = err
        return _sanitize_drive_status_body(body)
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


def drive_status_payload(service: NinaService) -> Dict[str, Any]:
    """Extended drive snapshot for tablet HUD (matches kiosk ``drive_screen``)."""
    body = navigation_hw_status(service)
    dc = getattr(service, "_drive", None)
    if dc is None:
        body["speed_pct"] = 0
        body["heading_deg"] = None
        body["distance_m"] = None
        body["direction"] = "idle"
        body["reverse"] = False
        body["imu_drift_deg"] = None
        body["imu_drift_side"] = "off"
        body["straight_pulse_active"] = False
        return _sanitize_drive_status_body(body)
    try:
        st = dc.state()
        body["speed_pct"] = int(st.get("speed_pct", 0))
        body["heading_deg"] = _json_safe_float(st.get("heading_deg", 0.0))
        body["distance_m"] = _json_safe_float(st.get("distance_m", 0.0))
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
            drift_deg = _json_safe_float(s.yaw_drift_deg)
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
    return _sanitize_drive_status_body(body)


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
    if not on:
        prime = _prime_drive_for_manual(service, wait_timeout_sec=1.2)
        blocked = _drive_not_ready_response(prime)
        if blocked is not None:
            return blocked
    dc = service.drive
    dc.set_brake(bool(on))
    st = dc.state()
    return {"ok": True, "brake": bool(st.get("brake", on))}


def emergency_stop(service: NinaService) -> Dict[str, Any]:
    """Hard stop immediately — same as kiosk Esc (no BLDC prime wait)."""
    dc = service.drive
    dc.emergency_stop()
    _set_last_drive_error(None)
    return {"ok": True, "queued": True}
