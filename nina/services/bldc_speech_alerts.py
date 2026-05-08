"""Spoken alerts for BLDC / drive issues (plays on the Jetson speaker).

Uses ``espeak-ng`` or ``espeak`` — same family as face greetings, **no gTTS /
network**. Intended when the operator cannot watch the console.

Environment:

* ``NINA_BLDC_ALERT_SPEECH`` — ``0`` / ``false`` / ``off`` disables all alerts.
  Default: **enabled**.
* ``NINA_BLDC_ALERT_COOLDOWN_SEC`` — minimum seconds between *identical* spoken
  messages (default **12**). Different messages are not blocked by each other.

Runs TTS in a **daemon thread** so GPIO / drive workers are never blocked.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Optional

from nina.services.audio_player import play_silence_preroll_blocking


log = logging.getLogger("nina.bldc_speech")

_alert_lock = threading.Lock()
_last_key: Optional[str] = None
_last_at: float = 0.0


def _alerts_enabled() -> bool:
    raw = (os.environ.get("NINA_BLDC_ALERT_SPEECH") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "n")


def _cooldown_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("NINA_BLDC_ALERT_COOLDOWN_SEC", "12")))
    except ValueError:
        return 12.0


def _normalize_message(msg: str) -> str:
    t = (msg or "").strip()
    t = re.sub(r"[^\w\s.,;:!?'\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 220:
        t = t[:217] + "..."
    return t


def _espeak_path() -> Optional[str]:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def maybe_speak_bldc_alert(message: str) -> None:
    """If alerts are enabled and cooldown allows, speak ``message`` once.

    Safe from any thread. Swallows all errors (logging only).
    """
    if not _alerts_enabled():
        return
    text = _normalize_message(message)
    if not text:
        return
    cooldown = _cooldown_sec()
    now = time.time()
    global _last_key, _last_at
    with _alert_lock:
        if (
            cooldown > 0
            and _last_key == text
            and now - _last_at < cooldown
        ):
            return
        _last_key = text
        _last_at = now

    es = _espeak_path()
    if not es:
        log.debug("bldc speech alert skipped: no espeak-ng / espeak on PATH")
        return

    def _run() -> None:
        try:
            play_silence_preroll_blocking()
            r = subprocess.run(
                [es, "-s", "150", text],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=120,
                check=False,
            )
            if r.returncode != 0:
                log.warning(
                    "bldc speech alert: espeak failed rc=%s",
                    r.returncode,
                )
        except subprocess.TimeoutExpired:
            log.warning("bldc speech alert: espeak timed out")
        except OSError as exc:
            log.warning("bldc speech alert: espeak failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="bldc-alert-tts").start()
