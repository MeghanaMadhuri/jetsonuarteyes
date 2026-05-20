"""Bridge ``VisionWorker.frame_ready`` to a latest-JPEG buffer for MJPEG streaming."""

from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, Callable, Optional

from PyQt5.QtCore import QObject, Qt, pyqtSlot
from PyQt5.QtGui import QImage

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.vision_mjpeg")

# Tablet MJPEG: full-res JPEG is often larger *and* slower over Wi‑Fi than a capped
# width at slightly higher quality. Override with env if needed.
def _mjpeg_max_width() -> int:
    raw = os.environ.get("NINA_MJPEG_MAX_WIDTH", "1280").strip()
    try:
        w = int(raw)
    except ValueError:
        return 1280
    return w if w > 0 else 0


def _mjpeg_jpeg_quality() -> int:
    raw = os.environ.get("NINA_MJPEG_JPEG_QUALITY", "78").strip()
    try:
        q = int(raw)
    except ValueError:
        return 78
    return max(40, min(95, q))


class VisionMjpegHub(QObject):
    """Parent on QApplication; slots run on GUI thread (QueuedConnection from worker)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._jpeg: Optional[bytes] = None
        self._attached = False
        self._vision = None

    def attach(self, service: "NinaService") -> None:
        from sirena_ui.workers.nina_service import NinaService  # noqa: F401

        v = service.vision
        if self._attached and self._vision is v:
            return
        self.detach()
        self._vision = v
        v.frame_ready.connect(self._on_frame, Qt.QueuedConnection)
        self._attached = True
        log.info("VisionMjpegHub attached")

    def detach(self) -> None:
        if self._vision is not None:
            try:
                self._vision.frame_ready.disconnect(self._on_frame)
            except TypeError:
                pass
        self._vision = None
        self._attached = False
        with self._lock:
            self._jpeg = None

    _client_lock = threading.Lock()
    _clients = 0

    def client_enter(self, service: "NinaService") -> None:
        with VisionMjpegHub._client_lock:
            VisionMjpegHub._clients += 1
            if VisionMjpegHub._clients == 1:
                self.attach(service)

    def client_leave(self) -> None:
        with VisionMjpegHub._client_lock:
            VisionMjpegHub._clients = max(0, VisionMjpegHub._clients - 1)
            if VisionMjpegHub._clients == 0:
                self.detach()

    @pyqtSlot(QImage)
    def _on_frame(self, img: QImage) -> None:
        if img.isNull():
            return
        try:
            from sirena_ui.android_gateway.tablet_aruco import ingest_frame_if_active

            ingest_frame_if_active(img)
        except Exception:
            pass
        try:
            from PyQt5.QtCore import QBuffer, QByteArray, QIODevice

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            max_w = _mjpeg_max_width()
            to_save = img
            if max_w > 0 and img.width() > max_w:
                # Fast scale: less CPU than SmoothTransformation; fewer pixels → lower latency.
                to_save = img.scaledToWidth(max_w, Qt.FastTransformation)
            q = _mjpeg_jpeg_quality()
            to_save.save(buf, "JPEG", quality=q)
            raw = bytes(ba.data())
            with self._lock:
                self._jpeg = raw
        except Exception:
            log.exception("vision jpeg encode")

    def latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg
