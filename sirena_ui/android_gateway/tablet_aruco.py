"""Singleton ArUco follow controller for tablet HTTP (same as Vision screen)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from sirena_ui.workers.aruco_follow_controller import ArucoFollowController
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.tablet_aruco")

_lock = threading.Lock()
_controller: Optional[ArucoFollowController] = None
_last_status: str = "ArUco: off"


def _controller_for(service: NinaService) -> ArucoFollowController:
    global _controller
    with _lock:
        if _controller is None:
            _controller = ArucoFollowController(service.drive, parent=None)
            _controller.status_message.connect(_on_status)
        return _controller


def _on_status(msg: str) -> None:
    global _last_status
    _last_status = (msg or "").strip() or "ArUco: off"


def aruco_status() -> Dict[str, Any]:
    with _lock:
        active = bool(_controller is not None and _controller.is_active())
        return {"ok": True, "active": active, "message": _last_status}


def aruco_start(service: NinaService, marker_id: int) -> Dict[str, Any]:
    ctrl = _controller_for(service)
    if not ctrl.start(int(marker_id)):
        return {"ok": False, "error": _last_status or "could not start ArUco approach"}
    return {"ok": True, "active": True, "marker_id": int(marker_id)}


def aruco_stop(service: NinaService) -> Dict[str, Any]:
    with _lock:
        if _controller is not None:
            _controller.stop()
    service.drive.stop()
    return {"ok": True, "active": False}


def ingest_frame_if_active(qimg: object) -> None:
    """Called from vision MJPEG hub when ArUco approach is active."""
    with _lock:
        ctrl = _controller
    if ctrl is None or not ctrl.is_active():
        return
    try:
        ctrl.ingest_frame(qimg)  # type: ignore[arg-type]
    except Exception:
        log.debug("aruco ingest_frame failed", exc_info=True)
