"""Vision-guided drive toward a fixed ArUco marker ID (RGB / USB camera preview).

Uses the same forward speed envelope as the Drive \"Straight\" bench
(:func:`straight_bench_speed_pct` → ``NINA_STRAIGHT_TEST_SPEED_PCT``).
Lateral error (marker centre vs image centre) maps to differential forward
or brief in-place pivots so the robot recentres while approaching.

Tuning (optional env):
    NINA_ARUCO_FOLLOW_TICK_MS   Control period (default 50).
    NINA_ARUCO_DICT             OpenCV name, default DICT_4X4_50.
    NINA_ARUCO_STOP_AREA_FRAC   Stop when marker contour area / image area
                                reaches this (default 0.10).
    NINA_ARUCO_YAW_GAIN, NINA_ARUCO_YAW_ERR_BOOST,
    NINA_ARUCO_ERR_FWD_SCALE_MIN, NINA_ARUCO_ERR_FWD_SCALE_POWER
                                Same roles as face-follow steering.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QImage

from sirena_ui.workers.drive_controller import RIGHT_WHEEL_EXTRA_RUN_PP, DriveController
from sirena_ui.workers.straight_bench_speed import straight_bench_speed_pct

log = logging.getLogger("sirena_ui.aruco_follow")

_TICK_MS = max(16, int(os.environ.get("NINA_ARUCO_FOLLOW_TICK_MS", "50")))

try:
    _YAW_GAIN = float(os.environ.get("NINA_ARUCO_YAW_GAIN", "5.0"))
except ValueError:
    _YAW_GAIN = 5.0
_YAW_GAIN = max(0.5, min(20.0, _YAW_GAIN))

try:
    _YAW_ERR_BOOST = float(os.environ.get("NINA_ARUCO_YAW_ERR_BOOST", "0.75"))
except ValueError:
    _YAW_ERR_BOOST = 0.75
_YAW_ERR_BOOST = max(0.0, min(3.0, _YAW_ERR_BOOST))

try:
    _ERR_FWD_SCALE_MIN = float(os.environ.get("NINA_ARUCO_ERR_FWD_SCALE_MIN", "0.28"))
except ValueError:
    _ERR_FWD_SCALE_MIN = 0.28
_ERR_FWD_SCALE_MIN = max(0.08, min(0.95, _ERR_FWD_SCALE_MIN))

try:
    _ERR_FWD_SCALE_POWER = float(
        os.environ.get("NINA_ARUCO_ERR_FWD_SCALE_POWER", "1.0")
    )
except ValueError:
    _ERR_FWD_SCALE_POWER = 1.0
_ERR_FWD_SCALE_POWER = max(0.5, min(3.0, _ERR_FWD_SCALE_POWER))

try:
    _STOP_AREA_FRAC = float(os.environ.get("NINA_ARUCO_STOP_AREA_FRAC", "0.10"))
except ValueError:
    _STOP_AREA_FRAC = 0.10
_STOP_AREA_FRAC = max(0.02, min(0.60, _STOP_AREA_FRAC))

_DICT_NAME = (os.environ.get("NINA_ARUCO_DICT") or "DICT_4X4_50").strip().upper()


def _steering_command(
    base_forward: float,
    err_x: float,
    *,
    yaw_gain: float,
    max_wheel: int,
) -> Tuple[str, int, str, int]:
    max_w = max(1, min(100, int(max_wheel)))
    err_clamped = max(-1.0, min(1.0, err_x))
    err_mag = abs(err_clamped)
    fwd_scale = _ERR_FWD_SCALE_MIN + (1.0 - _ERR_FWD_SCALE_MIN) * (
        (1.0 - err_mag) ** _ERR_FWD_SCALE_POWER
    )
    base = float(base_forward) * fwd_scale
    yaw_mult = 1.0 + _YAW_ERR_BOOST * err_mag
    yaw = float(err_clamped) * float(yaw_gain) * yaw_mult
    ls = base + yaw
    rs = base - yaw

    if ls <= -0.05 or rs <= -0.05:
        sp = int(max(1.0, min(float(max_w), round(max(abs(ls), abs(rs))))))
        if err_clamped > 0:
            return ("forward", sp, "back", sp)
        return ("back", sp, "forward", sp)

    ls_i = max(1, min(max_w, int(round(ls))))
    rs_i = max(1, min(max_w, int(round(rs))))
    if err_mag >= 0.06 and abs(ls_i - rs_i) < 2:
        if err_clamped > 0:
            ls_i = min(max_w, ls_i + 1)
            rs_i = max(1, rs_i - 1)
        elif err_clamped < 0:
            ls_i = max(1, ls_i - 1)
            rs_i = min(max_w, rs_i + 1)

    r_cap = max(1, max_w - RIGHT_WHEEL_EXTRA_RUN_PP)
    rs_cmd = max(1, min(r_cap, rs_i - RIGHT_WHEEL_EXTRA_RUN_PP))
    return ("forward", ls_i, "forward", rs_cmd)


def _qimage_bgr888_to_bgr(qimg: QImage) -> Optional[np.ndarray]:
    if qimg.isNull():
        return None
    if qimg.format() != QImage.Format_BGR888:
        qimg = qimg.convertToFormat(QImage.Format_BGR888)
    w, h = qimg.width(), qimg.height()
    if w <= 0 or h <= 0:
        return None
    bpl = qimg.bytesPerLine()
    try:
        nbytes = int(qimg.sizeInBytes())
    except Exception:
        nbytes = bpl * h
    try:
        bits = qimg.constBits()
    except Exception:
        bits = qimg.bits()
    try:
        raw = bits.asstring(nbytes)
    except Exception:
        try:
            raw = bytes(bits)
        except Exception:
            return None
    arr = np.frombuffer(raw, dtype=np.uint8, count=nbytes)
    try:
        mat = arr.reshape((h, bpl))
    except ValueError:
        return None
    return np.ascontiguousarray(mat[:, : w * 3].reshape((h, w, 3)))


def _get_predefined_dictionary(cv2, name: str):
    n = (name or "DICT_4X4_50").strip().upper()
    if not hasattr(cv2.aruco, n):
        n = "DICT_4X4_50"
    return getattr(cv2.aruco, n)


def _detect_marker_centre_area(
    bgr: np.ndarray, marker_id: int, cv2, dictionary,
) -> Optional[Tuple[float, float, float]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    corners: List[np.ndarray]
    ids: Optional[np.ndarray]
    if hasattr(cv2.aruco, "ArucoDetector"):
        params = cv2.aruco.DetectorParameters()
        det = cv2.aruco.ArucoDetector(dictionary, params)
        corners, ids, _ = det.detectMarkers(gray)
    else:
        corners, ids, _ = cv2.aruco.detectMarkers(gray, dictionary)

    if ids is None or len(ids) == 0 or len(corners) == 0:
        return None
    flat = ids.flatten()
    idxs = [i for i, mid in enumerate(flat) if int(mid) == int(marker_id)]
    if not idxs:
        return None
    i = idxs[0]
    pts = corners[i][0]
    cx = float(np.mean(pts[:, 0]))
    cy = float(np.mean(pts[:, 1]))
    area = float(cv2.contourArea(pts))
    return cx, cy, area


class ArucoFollowController(QObject):
    """Start/stop ArUco approach; consumes preview ``QImage`` frames."""

    status_message = pyqtSignal(str)

    def __init__(self, drive: DriveController, parent=None) -> None:
        super().__init__(parent)
        self._drive = drive
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(_TICK_MS)

        self._lock = threading.Lock()
        self._frame_bgr: Optional[np.ndarray] = None

        self._active = False
        self._target_id: int = 0
        self._frame_wh: Tuple[int, int] = (640, 480)
        self._arrived_reported = False

        self._cv2 = None
        self._dictionary = None

    def set_frame_size(self, w: int, h: int) -> None:
        self._frame_wh = (max(1, int(w)), max(1, int(h)))

    def ingest_frame(self, qimg: QImage) -> None:
        bgr = _qimage_bgr888_to_bgr(qimg)
        if bgr is None:
            return
        with self._lock:
            self._frame_bgr = bgr
            self._frame_wh = (bgr.shape[1], bgr.shape[0])

    def _ensure_cv(self):
        if self._cv2 is not None:
            return True
        try:
            import cv2
        except Exception as exc:
            log.warning("OpenCV unavailable for ArUco: %s", exc)
            return False
        self._cv2 = cv2
        try:
            dic_const = _get_predefined_dictionary(cv2, _DICT_NAME)
            if hasattr(cv2.aruco, "getPredefinedDictionary"):
                self._dictionary = cv2.aruco.getPredefinedDictionary(dic_const)
            else:
                self._dictionary = cv2.aruco.Dictionary_get(dic_const)
        except Exception as exc:
            log.exception("ArUco dictionary init failed: %s", exc)
            self._cv2 = None
            self._dictionary = None
            return False
        return True

    def start(self, marker_id: int) -> bool:
        if not self._ensure_cv():
            self.status_message.emit("ArUco: OpenCV / ArUco unavailable")
            return False
        st = self._drive.state()
        if st.get("brake"):
            self.status_message.emit("ArUco: set Brake OFF on Drive first")
            return False
        mid = int(marker_id)
        if mid < 0:
            mid = 0
        self._target_id = mid
        try:
            self._drive.stop(drain=True)
        except Exception:
            pass
        self._arrived_reported = False
        self._active = True
        with self._lock:
            self._frame_bgr = None
        self._timer.start()
        self.status_message.emit(f"ArUco: seeking id {self._target_id}…")
        try:
            self._drive.ensure_hardware()
        except Exception as exc:
            log.debug("ensure_hardware: %s", exc)
        return True

    def stop(self) -> None:
        self._active = False
        self._timer.stop()
        with self._lock:
            self._frame_bgr = None
        try:
            self._drive.stop(drain=True)
        except Exception as exc:
            log.debug("drive.stop: %s", exc)
        self.status_message.emit("ArUco: off")

    def is_active(self) -> bool:
        return bool(self._active)

    def _drive_stop_safe(self) -> None:
        try:
            self._drive.stop(drain=True)
        except Exception:
            try:
                self._drive.stop()
            except Exception:
                pass

    def _tick(self) -> None:
        if not self._active:
            return
        if self._drive.state().get("brake"):
            self._active = False
            self._timer.stop()
            self._drive_stop_safe()
            self.status_message.emit("ArUco: stopped (brake on)")
            return

        if not self._ensure_cv():
            self._active = False
            self._timer.stop()
            self._drive_stop_safe()
            return

        with self._lock:
            bgr = self._frame_bgr
        if bgr is None:
            self._drive_stop_safe()
            return

        h, w = bgr.shape[:2]
        img_area = float(max(1, w * h))
        try:
            found = _detect_marker_centre_area(
                bgr, self._target_id, self._cv2, self._dictionary
            )
        except Exception as exc:
            log.debug("detectMarkers: %s", exc)
            self._drive_stop_safe()
            return

        if found is None:
            self._drive_stop_safe()
            self.status_message.emit(
                f"ArUco: no marker {self._target_id} in view — hold still"
            )
            return

        cx, cy, m_area = found
        area_frac = m_area / img_area
        if area_frac >= _STOP_AREA_FRAC:
            self._drive_stop_safe()
            if not self._arrived_reported:
                self._arrived_reported = True
                pct = int(round(100.0 * area_frac))
                self.status_message.emit(
                    f"ArUco: arrived (marker ~{pct}% of view; "
                    f"tune NINA_ARUCO_STOP_AREA_FRAC)"
                )
            return

        self._arrived_reported = False
        err_x = (cx - 0.5 * w) / max(w * 0.5, 1.0)
        err_x = max(-1.0, min(1.0, err_x))

        max_sp = straight_bench_speed_pct()
        ld, ls, rd, rs = _steering_command(
            float(max_sp),
            err_x,
            yaw_gain=_YAW_GAIN,
            max_wheel=max_sp,
        )
        try:
            self._drive.drive_wheels(ld, ls, rd, rs)
        except Exception as exc:
            log.debug("drive_wheels: %s", exc)

        ang_txt = (
            f"err={err_x:+.2f}"
            if abs(err_x) >= 0.04
            else "centred"
        )
        self.status_message.emit(
            f"ArUco: id {self._target_id} · area {100.0 * area_frac:.1f}% / "
            f"{100.0 * _STOP_AREA_FRAC:.0f}% stop · {ang_txt}"
        )
