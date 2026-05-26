"""Environment-backed settings for the on-device voice stack."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class VoiceEdgeSettings:
    """Loopback ASR / LLM / TTS microservices + Nina orchestrator."""

    enabled: bool
    assistant_enabled: bool
    device_id: str
    asr_base_url: str
    llm_base_url: str
    tts_base_url: str
    speech_root: Path
    tts_signal_dir: Path
    conversation_dir: Path
    mic_device: str
    mic_rate_hz: int
    manage_services: bool
    services_script: Path
    goodbye_phrases: tuple[str, ...]


def load_voice_edge_settings(repo_root: Path) -> VoiceEdgeSettings:
    data_root = repo_root / "nina" / "data" / "voice"
    speech_root = Path(
        os.environ.get("NINA_VOICE_SPEECH_DIR", str(data_root / "speech"))
    ).expanduser()
    tts_sig = Path(
        os.environ.get(
            "NINA_VOICE_TTS_SIG_DIR",
            os.environ.get("TTS_SIG_DIR", str(data_root / "tts_signals")),
        )
    ).expanduser()
    conv = Path(
        os.environ.get(
            "NINA_VOICE_CONVERSATION_DIR",
            os.environ.get("CONVERSATION_FOLDER", str(data_root / "conversations")),
        )
    ).expanduser()
    script = repo_root / "scripts" / "start-voice-edge.sh"
    goodbye_raw = (
        os.environ.get("NINA_VOICE_GOODBYE_PHRASES")
        or "goodbye,bye,see you,stop listening"
    )
    phrases = tuple(p.strip().lower() for p in goodbye_raw.split(",") if p.strip())
    return VoiceEdgeSettings(
        enabled=_env_bool("NINA_VOICE_EDGE_ENABLE", False),
        assistant_enabled=_env_bool("NINA_VOICE_ASSISTANT_ENABLE", True),
        device_id=(os.environ.get("NINA_VOICE_DEVICE_ID") or "nina-jetson").strip(),
        asr_base_url=(
            os.environ.get("NINA_VOICE_ASR_URL") or "http://127.0.0.1:6000"
        ).rstrip("/"),
        llm_base_url=(
            os.environ.get("NINA_VOICE_LLM_URL") or "http://127.0.0.1:4000"
        ).rstrip("/"),
        tts_base_url=(
            os.environ.get("NINA_VOICE_TTS_URL") or "http://127.0.0.1:2000"
        ).rstrip("/"),
        speech_root=speech_root,
        tts_signal_dir=tts_sig,
        conversation_dir=conv,
        mic_device=(os.environ.get("NINA_VOICE_MIC_DEVICE") or "default").strip(),
        mic_rate_hz=max(8000, min(48000, _env_int("NINA_VOICE_MIC_RATE", 16000))),
        manage_services=_env_bool("NINA_VOICE_MANAGE_SERVICES", False),
        services_script=script,
        goodbye_phrases=phrases,
    )
