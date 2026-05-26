"""Microphone capture as 16 kHz mono PCM for the ASR WebSocket."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger("nina.voice.capture")

FRAME_BYTES = 960  # 30 ms @ 16 kHz s16le mono


class MicCapture:
    """Stream raw PCM from ``arecord`` (ALSA) in a background thread."""

    def __init__(
        self,
        *,
        device: str = "default",
        sample_rate: int = 16000,
        on_chunk: Callable[[bytes], None],
        frame_bytes: int = FRAME_BYTES,
    ) -> None:
        self._device = device
        self._rate = sample_rate
        self._on_chunk = on_chunk
        self._frame_bytes = frame_bytes
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        if shutil.which("arecord") is None:
            raise RuntimeError("arecord not found; install alsa-utils")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MicCapture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._thread = None
        self._proc = None

    def _run(self) -> None:
        cmd = [
            "arecord",
            "-q",
            "-D",
            self._device,
            "-f",
            "S16_LE",
            "-r",
            str(self._rate),
            "-c",
            "1",
            "-t",
            "raw",
        ]
        env = os.environ.copy()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except Exception as exc:
            log.error("arecord failed to start: %s", exc)
            return
        assert self._proc.stdout is not None
        while not self._stop.is_set():
            chunk = self._proc.stdout.read(self._frame_bytes)
            if not chunk:
                break
            try:
                self._on_chunk(chunk)
            except Exception:
                log.debug("mic chunk handler failed", exc_info=True)
        if self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
