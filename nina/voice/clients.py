"""HTTP/WebSocket clients for local voice microservices."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

log = logging.getLogger("nina.voice.clients")


class VoiceServiceError(RuntimeError):
    pass


class LlmClient:
    def __init__(self, base_url: str, *, device_id: str, timeout_sec: float = 90.0) -> None:
        self._base = base_url.rstrip("/")
        self._device_id = device_id
        self._timeout = timeout_sec
        self._session_uuid = str(uuid.uuid4())

    @property
    def session_uuid(self) -> str:
        return self._session_uuid

    def reset_session(self) -> None:
        self._session_uuid = str(uuid.uuid4())

    def health(self) -> bool:
        try:
            r = requests.get(f"{self._base}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def ask(self, question: str) -> str:
        body = {
            "question": question.strip(),
            "mac_id": self._device_id,
            "session_uuid": self._session_uuid,
        }
        r = requests.post(
            f"{self._base}/ask",
            json=body,
            timeout=self._timeout,
        )
        r.raise_for_status()
        data = r.json()
        text = str(data.get("response") or "").strip()
        if not text:
            raise VoiceServiceError("LLM returned empty response")
        if data.get("session_uuid"):
            self._session_uuid = str(data["session_uuid"])
        return text


class TtsClient:
    def __init__(self, base_url: str, *, device_id: str, timeout_sec: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._device_id = device_id
        self._timeout = timeout_sec

    def health(self) -> bool:
        try:
            r = requests.get(f"{self._base}/health", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, text: str, *, filename: str = "speech.mp3") -> Path:
        payload = {
            "text": text,
            "mac_id": self._device_id,
            "cluster_id": self._device_id,
            "filename": filename,
        }
        r = requests.post(
            f"{self._base}/generate-tts",
            json=payload,
            timeout=self._timeout,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok", True):
            raise VoiceServiceError(str(data.get("error") or "TTS failed"))
        path_raw = data.get("path")
        if not path_raw:
            raise VoiceServiceError("TTS response missing path")
        path = Path(str(path_raw))
        if not path.is_file():
            raise VoiceServiceError(f"TTS file missing: {path}")
        return path


def asr_websocket_url(base_url: str, device_id: str) -> str:
    """Build ``ws://host:port/ws/audio?mac=...`` from an HTTP base URL."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = "ws://" + base
    return f"{ws_base}/ws/audio?mac={quote(device_id, safe='')}"


def wait_for_services(
    *,
    llm: Optional[LlmClient] = None,
    tts: Optional[TtsClient] = None,
    asr_health_url: Optional[str] = None,
    timeout_sec: float = 60.0,
    poll_sec: float = 1.0,
) -> None:
    """Block until voice microservices respond or *timeout_sec* elapses."""
    deadline = time.monotonic() + max(1.0, timeout_sec)
    while time.monotonic() < deadline:
        ok = True
        if llm is not None and not llm.health():
            ok = False
        if tts is not None and not tts.health():
            ok = False
        if asr_health_url:
            try:
                r = requests.get(asr_health_url, timeout=2.0)
                if r.status_code != 200:
                    ok = False
            except Exception:
                ok = False
        if ok:
            return
        time.sleep(poll_sec)
    raise VoiceServiceError("voice microservices not ready within timeout")
