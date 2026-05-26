"""On-device voice loop: mic → ASR → LLM → TTS → Nina audio player."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from nina.services.audio_player import AudioPlayer
from nina.voice.capture import MicCapture
from nina.voice.clients import (
    LlmClient,
    TtsClient,
    VoiceServiceError,
    asr_websocket_url,
    wait_for_services,
)
from nina.voice.settings import VoiceEdgeSettings

log = logging.getLogger("nina.voice.orchestrator")

_GOODBYE_RE = re.compile(
    r"\b(good\s*bye|goodbye|bye|see you|see ya|stop listening)\b",
    re.I,
)


class VoiceAssistant:
    """Runs the conversational loop in a daemon thread with its own asyncio loop."""

    def __init__(
        self,
        settings: VoiceEdgeSettings,
        *,
        audio_player: Optional[AudioPlayer] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_response: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._settings = settings
        self._player = audio_player or AudioPlayer()
        self._on_status = on_status
        self._on_transcript = on_transcript
        self._on_response = on_response
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mic: Optional[MicCapture] = None
        self._llm = LlmClient(settings.llm_base_url, device_id=settings.device_id)
        self._tts = TtsClient(settings.tts_base_url, device_id=settings.device_id)
        self._session_active = False
        self._speaking = False
        self._ws_queue: Optional[asyncio.Queue] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _status(self, msg: str) -> None:
        log.info("%s", msg)
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main, name="VoiceAssistant", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._mic is not None:
            self._mic.stop()
        t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        self._thread = None
        self._session_active = False

    def _thread_main(self) -> None:
        try:
            wait_for_services(
                llm=self._llm,
                tts=self._tts,
                asr_health_url=f"{self._settings.asr_base_url}/health",
                timeout_sec=90.0,
            )
        except VoiceServiceError as exc:
            self._status(f"voice services unavailable: {exc}")
            return
        self._status("voice assistant ready")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_forever())
        finally:
            loop.close()

    async def _run_forever(self) -> None:
        asr_url = asr_websocket_url(self._settings.asr_base_url, self._settings.device_id)
        import websockets  # lazy: optional dep

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    asr_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                ) as ws:
                    self._session_active = True
                    self._status("listening")
                    recv_task = asyncio.create_task(self._recv_loop(ws))
                    send_task = asyncio.create_task(self._send_loop(ws))
                    done, pending = await asyncio.wait(
                        [recv_task, send_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    for t in done:
                        try:
                            await t
                        except Exception:
                            log.debug("voice task ended", exc_info=True)
            except Exception as exc:
                if not self._stop.is_set():
                    self._status(f"ASR reconnecting: {exc}")
                    await asyncio.sleep(2.0)
            finally:
                self._session_active = False
                if self._mic is not None:
                    self._mic.stop()
                    self._mic = None

    async def _send_loop(self, ws) -> None:
        queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()

        def on_chunk(data: bytes) -> None:
            if self._speaking:
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, data)
            except Exception:
                pass

        self._mic = MicCapture(
            device=self._settings.mic_device,
            sample_rate=self._settings.mic_rate_hz,
            on_chunk=on_chunk,
        )
        self._mic.start()
        try:
            while not self._stop.is_set():
                chunk = await queue.get()
                if chunk is None:
                    break
                await ws.send(chunk)
        finally:
            if self._mic is not None:
                self._mic.stop()

    async def _recv_loop(self, ws) -> None:
        while not self._stop.is_set():
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue
            text = str(raw).strip()
            if not text:
                continue
            await self._handle_transcript(text)

    async def _handle_transcript(self, text: str) -> None:
        self._status(f"heard: {text[:80]}")
        if self._on_transcript:
            try:
                self._on_transcript(text)
            except Exception:
                pass
        lower = text.lower()
        if any(p in lower for p in self._settings.goodbye_phrases) or _GOODBYE_RE.search(
            text
        ):
            self._status("goodbye")
            self._stop.set()
            return
        self._speaking = True
        try:
            answer = await asyncio.to_thread(self._llm.ask, text)
        except Exception as exc:
            log.warning("LLM ask failed: %s", exc)
            self._status("LLM error")
            return
        finally:
            pass
        self._status(f"reply: {answer[:80]}")
        if self._on_response:
            try:
                self._on_response(answer)
            except Exception:
                pass
        try:
            mp3 = await asyncio.to_thread(self._tts.generate, answer)
            await asyncio.to_thread(self._play_and_wait, mp3)
        except Exception as exc:
            log.warning("TTS/play failed: %s", exc)
            self._status("TTS error")
        finally:
            self._speaking = False

    def _play_and_wait(self, path: Path) -> None:
        proc = self._player.play(path)
        if proc is None:
            return
        deadline = time.monotonic() + 120.0
        while proc.poll() is None and time.monotonic() < deadline:
            if self._stop.is_set():
                self._player.stop_all()
                break
            time.sleep(0.05)

    def status_dict(self) -> dict:
        return {
            "running": self.running,
            "session_active": self._session_active,
            "speaking": self._speaking,
            "device_id": self._settings.device_id,
            "asr_url": self._settings.asr_base_url,
            "llm_url": self._settings.llm_base_url,
            "tts_url": self._settings.tts_base_url,
            "llm_ok": self._llm.health(),
            "tts_ok": self._tts.health(),
        }
