"""RealSense D435 colorized stream (singleton; may conflict if autonomy holds the device)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from nina.sensors.realsense_d435 import normalize_depth_error

log = logging.getLogger("sirena_ui.android_gateway.depth_stream")

_lock = threading.RLock()
_refcount = 0
_depth: Any = None
_status: Dict[str, Any] = {
    "ok": True,
    "open": False,
    "message": "idle",
    "code": "idle",
}
_last_min_mm: Dict[str, Optional[int]] = {
    "forward_min_mm": None,
    "left_min_mm": None,
    "right_min_mm": None,
}


def _depth_status_code(message: str, is_open: bool) -> str:
    if is_open:
        return "ready"
    low = message.lower()
    if "dependency missing" in low:
        return "dependency_missing"
    if "permission denied" in low:
        return "permission_denied"
    if "device busy" in low:
        return "device_busy"
    if "camera unavailable" in low:
        return "camera_unavailable"
    if "disabled via nina_depth_disable" in low:
        return "disabled"
    if "closed" in low:
        return "closed"
    if "idle" in low:
        return "idle"
    return "error"


def _camera():
    global _depth
    if _depth is None:
        from nina.sensors.realsense_d435 import RealSenseD435

        _depth = RealSenseD435()
    return _depth


def acquire(reason: str = "consumer") -> Tuple[bool, str]:
    global _refcount
    with _lock:
        _refcount += 1
        if _refcount > 1:
            ok = bool(_status.get("open"))
            return ok, str(_status.get("message", ""))
        cam = _camera()
        try:
            cam.open()
            _status["open"] = True
            _status["message"] = f"open ({reason})"
            _status["code"] = "ready"
            log.info("depth stream opened (%s)", reason)
            return True, "depth ready"
        except Exception as exc:
            _refcount = 0
            _status["open"] = False
            msg = normalize_depth_error(exc)
            _status["message"] = msg
            _status["code"] = _depth_status_code(msg, is_open=False)
            log.warning("depth open failed: %s", exc)
            return False, msg


def release(reason: str = "consumer") -> None:
    global _refcount
    with _lock:
        if _refcount <= 0:
            return
        _refcount -= 1
        if _refcount > 0:
            return
        cam = _depth
        if cam is None:
            return
        try:
            cam.set_color_publish(False)
        except Exception:
            pass
        try:
            cam.close()
        except Exception as exc:
            log.warning("depth close: %s", exc)
        _status["open"] = False
        _status["message"] = "closed"
        _status["code"] = "closed"
        log.info("depth stream closed (%s)", reason)


def status_payload() -> Dict[str, Any]:
    with _lock:
        return {
            "ok": True,
            "camera_open": bool(_status.get("open")),
            "message": str(_status.get("message", "")),
            "code": str(
                _status.get(
                    "code",
                    _depth_status_code(
                        str(_status.get("message", "")),
                        bool(_status.get("open")),
                    ),
                )
            ),
            "refcount": _refcount,
            "forward_min_mm": _last_min_mm.get("forward_min_mm"),
            "left_min_mm": _last_min_mm.get("left_min_mm"),
            "right_min_mm": _last_min_mm.get("right_min_mm"),
        }


def iter_depth_mjpeg(
    should_stop: Callable[[], bool],
    fps_cap: float = 12.0,
) -> Iterator[bytes]:
    import cv2

    ok, _msg = acquire("mjpeg_stream")
    if not ok:
        return

    cam = _camera()
    try:
        cam.set_color_publish(True)
    except Exception as exc:
        log.warning("set_color_publish: %s", exc)

    min_interval = 1.0 / max(1.0, min(30.0, fps_cap))
    while not should_stop():
        t0 = time.monotonic()
        try:
            depth_frame = cam.read()
            if depth_frame is not None:
                with _lock:
                    _last_min_mm["forward_min_mm"] = getattr(
                        depth_frame, "forward_min_mm", None
                    )
                    _last_min_mm["left_min_mm"] = getattr(depth_frame, "left_min_mm", None)
                    _last_min_mm["right_min_mm"] = getattr(
                        depth_frame, "right_min_mm", None
                    )
            tup = cam.latest_color_image()
            if tup is None:
                time.sleep(0.03)
                continue
            w, h, buf = tup
            import numpy as np

            arr = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 3))
            enc_ok, jpeg = cv2.imencode(
                ".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 78]
            )
            if enc_ok:
                yield jpeg.tobytes()
        except Exception:
            log.exception("depth MJPEG frame")
            time.sleep(0.05)
        elapsed = time.monotonic() - t0
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    try:
        cam.set_color_publish(False)
    except Exception:
        pass
    release("mjpeg_stream")
