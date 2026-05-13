"""Enrollment / announce helpers for HTTP (state mirrors legacy vision_http)."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from nina.jetson_net.announcement_sentence import build_sentence
from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer
from sirena_ui.workers.nina_service import NinaService
from sirena_ui.workers.vision_types import KIND_OBJECT, Detection

log = logging.getLogger("sirena_ui.android_gateway.vision_tablet")

_enroll_lock = threading.Lock()
_enroll_status: Dict[str, Any] = {
    "in_progress": False,
    "samples": 0,
    "target": 8,
    "last": None,
}

_announce_lock = threading.Lock()
_announce_last_at = 0.0
_announce_last_sentence: Optional[str] = None
_announce_last_error: Optional[str] = None
_ANNOUNCE_COOLDOWN_SEC = 1.5
_ANNOUNCE_CACHE = Path(__file__).resolve().parents[2] / "nina" / "data" / "announcements"

_hooks_installed = False


def detections_to_json(dets: List[Detection]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in dets:
        out.append(
            {
                "kind": d.kind,
                "label": d.label,
                "confidence": float(d.confidence),
                "bbox": [int(d.bbox[0]), int(d.bbox[1]), int(d.bbox[2]), int(d.bbox[3])],
                "identity": d.identity,
                "identity_score": d.identity_score,
            }
        )
    return out


def install_vision_hooks(service: NinaService) -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    v = service.vision

    def _on_prog(samples: int, target: int) -> None:
        with _enroll_lock:
            _enroll_status["samples"] = int(samples)
            _enroll_status["target"] = int(target)

    def _on_fin(payload: dict) -> None:
        with _enroll_lock:
            _enroll_status["last"] = payload
            _enroll_status["in_progress"] = False
            _enroll_status["samples"] = 0
        try:
            if isinstance(payload, dict) and payload.get("ok"):
                raw_name = payload.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    from nina.jetson_net.face_greeting_tts import queue_hello_greeting

                    queue_hello_greeting(raw_name.strip())
        except Exception:
            log.exception("post-enroll greeting")

    v.enrollment_progress.connect(_on_prog)
    v.enrollment_finished.connect(_on_fin)
    _hooks_installed = True


def enroll_status_snapshot() -> Dict[str, Any]:
    with _enroll_lock:
        return {
            "in_progress": bool(_enroll_status.get("in_progress")),
            "samples": int(_enroll_status.get("samples", 0)),
            "target": int(_enroll_status.get("target", 8)),
            "last": _enroll_status.get("last"),
        }


def start_enroll_face(service: NinaService, name: str, target_samples: int) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "Name cannot be empty."}
    ts = max(1, min(32, int(target_samples)))
    with _enroll_lock:
        if _enroll_status.get("in_progress"):
            return {"ok": False, "error": "Enrollment already in progress."}
        _enroll_status["in_progress"] = True
        _enroll_status["samples"] = 0
        _enroll_status["target"] = ts
        _enroll_status["last"] = None
    install_vision_hooks(service)
    service.vision.acquire()
    st = service.vision.status()
    if not st.camera_open:
        with _enroll_lock:
            _enroll_status["in_progress"] = False
        return {"ok": False, "error": st.message or "Camera is not open."}
    service.vision.set_face_enabled(True)
    service.vision.enroll_face(name, target_samples=ts)
    return {"ok": True, "queued": True, "target_samples": ts}


def start_announce_objects(service: NinaService) -> Dict[str, Any]:
    global _announce_last_at, _announce_last_sentence, _announce_last_error
    try:
        dets = list(service.vision._stale_preview_detections)  # noqa: SLF001
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    labels = [str(d.label) for d in dets if d.kind == KIND_OBJECT]
    sentence = build_sentence(labels)

    now = time.time()
    with _announce_lock:
        if (
            _announce_last_sentence == sentence
            and now - _announce_last_at < _ANNOUNCE_COOLDOWN_SEC
        ):
            return {
                "ok": True,
                "queued": False,
                "skipped": True,
                "sentence": sentence,
            }
        _announce_last_sentence = sentence
        _announce_last_at = now
        _announce_last_error = None

    _ANNOUNCE_CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(sentence.encode("utf-8")).hexdigest()[:16]
    out_path = _ANNOUNCE_CACHE / f"ann_{key}.mp3"

    def run() -> None:
        global _announce_last_error
        try:
            if not out_path.exists():
                AudioGenerator.generate(sentence, out_path)
            player = AudioPlayer()
            if not player.is_supported:
                msg = "No audio player on robot (e.g. install mpg123)."
                _announce_last_error = msg
                log.warning(msg)
                return
            player.play(out_path)
        except AudioGeneratorError as exc:
            _announce_last_error = str(exc)
            log.warning("vision announce TTS: %s", exc)
        except Exception as exc:
            _announce_last_error = str(exc)
            log.exception("vision announce")

    threading.Thread(target=run, daemon=True, name="vision-announce").start()
    return {"ok": True, "queued": True, "sentence": sentence}


def announce_error_snapshot() -> Dict[str, Any]:
    with _announce_lock:
        err = _announce_last_error
    return {"error": err}


# ---- Person follow (FaceFollowController) — same control loop as PyQt Vision screen ----

_follow_hooks_installed = False
_follow_status_lock = threading.Lock()
_follow_status_message: str = "Follow: off"


def follow_status_message_snapshot() -> str:
    with _follow_status_lock:
        return str(_follow_status_message)


def install_follow_hooks(service: NinaService) -> None:
    """One-time: wire vision detections + status line into ``service.face_follow``."""
    global _follow_hooks_installed
    if _follow_hooks_installed:
        return

    ff = service.face_follow
    v = service.vision

    def _on_status(msg: object) -> None:
        global _follow_status_message
        with _follow_status_lock:
            _follow_status_message = str(msg)

    ff.status_message.connect(_on_status)

    def _on_latched(name: str) -> None:
        try:
            from nina.jetson_net.face_greeting_tts import queue_hello_greeting

            if name.strip():
                queue_hello_greeting(name.strip())
        except Exception:
            log.exception("follow face_latched greeting")

    ff.face_latched.connect(_on_latched)

    def _on_detections(dets: Any) -> None:
        if ff.is_active():
            ff.ingest_detections(dets)

    v.detections_changed.connect(_on_detections)
    _follow_hooks_installed = True
