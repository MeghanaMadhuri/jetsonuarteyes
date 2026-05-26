"""Lightweight TTS for Jetson edge — Piper if configured, else espeak-ng.

API-compatible with sirena-repo ``/generate-tts`` so the Nina orchestrator
and ASR TTS-suppression signals keep working without OpenVoice.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Nina Edge TTS")

TTS_SIG_DIR = Path(
    os.environ.get("TTS_SIG_DIR", os.environ.get("NINA_VOICE_TTS_SIG_DIR", "./tmp/tts_signals"))
)
SPEECH_DIR = Path(os.environ.get("NINA_VOICE_SPEECH_DIR", "./speech"))
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")
PIPER_MODEL = os.environ.get("PIPER_MODEL", "")
PIPER_CONFIG = os.environ.get("PIPER_CONFIG", "")
ESPEAK_VOICE = os.environ.get("NINA_VOICE_ESPEAK_VOICE", "en+f3")

TTS_SIG_DIR.mkdir(parents=True, exist_ok=True)
SPEECH_DIR.mkdir(parents=True, exist_ok=True)


class TTSRequest(BaseModel):
    text: str
    mac_id: str = "default"
    cluster_id: Optional[str] = None
    filename: Optional[str] = "speech.mp3"


def _probe_duration_seconds(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return max(0.5, len(path.read_bytes()) / 16000.0)
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return max(0.1, float(out.strip()))
    except Exception:
        return 1.0


def _write_tts_signal(mac_id: str, mp3_path: Path, duration_s: float) -> None:
    payload = {
        "mac_id": mac_id,
        "path": str(mp3_path.resolve()),
        "duration_s": duration_s,
        "ts": time.time(),
    }
    sig_path = TTS_SIG_DIR / f"{mac_id}.json"
    tmp = sig_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(sig_path)


def _espeak_to_mp3(text: str, out_mp3: Path) -> None:
    wav = out_mp3.with_suffix(".wav")
    for cmd in (
        ["espeak-ng", "-v", ESPEAK_VOICE, "-w", str(wav), text],
        ["espeak", "-v", ESPEAK_VOICE, "-w", str(wav), text],
    ):
        if not shutil.which(cmd[0]):
            continue
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wav.is_file() and wav.stat().st_size > 0:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(wav),
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    str(out_mp3),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            wav.unlink(missing_ok=True)
            return
    raise RuntimeError("espeak-ng/espeak not available")


def _piper_to_mp3(text: str, out_mp3: Path) -> None:
    if not PIPER_MODEL or not shutil.which(PIPER_BIN):
        raise RuntimeError("piper not configured")
    wav = out_mp3.with_suffix(".wav")
    cmd = [PIPER_BIN, "--model", PIPER_MODEL, "--output_file", str(wav)]
    if PIPER_CONFIG:
        cmd.extend(["--config", PIPER_CONFIG])
    proc = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not wav.is_file():
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[:200])
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(wav),
            "-ar",
            "44100",
            "-ac",
            "1",
            str(out_mp3),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wav.unlink(missing_ok=True)


def _synthesize(text: str, out_mp3: Path) -> None:
    if PIPER_MODEL:
        try:
            _piper_to_mp3(text, out_mp3)
            return
        except Exception:
            pass
    _espeak_to_mp3(text, out_mp3)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "engine": "piper" if PIPER_MODEL else "espeak",
        "speech_dir": str(SPEECH_DIR.resolve()),
    }


@app.post("/generate-tts")
@app.post("/generate-tts/")
def generate_tts(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(payload)
    text = str(data.get("text", "")).strip()
    if not text:
        return {"ok": False, "error": "text is required"}
    mac_id = str(data.get("mac_id") or data.get("cluster_id") or "default").strip()
    filename = str(data.get("filename") or "speech.mp3").strip() or "speech.mp3"
    out_dir = SPEECH_DIR / mac_id.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_mp3 = out_dir / (filename if filename.endswith(".mp3") else f"{uuid.uuid4().hex}.mp3")
    try:
        _synthesize(text, out_mp3)
        dur = _probe_duration_seconds(out_mp3)
        _write_tts_signal(mac_id, out_mp3, dur)
        return {"ok": True, "path": str(out_mp3.resolve()), "duration_s": dur}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
