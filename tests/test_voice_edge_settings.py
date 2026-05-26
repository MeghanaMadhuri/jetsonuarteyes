"""Voice edge settings and client helpers."""

from __future__ import annotations

import os
from pathlib import Path

from nina.config.settings import load_settings
from nina.voice.clients import asr_websocket_url
from nina.voice.settings import load_voice_edge_settings


def test_load_voice_edge_settings_defaults() -> None:
    repo = Path(__file__).resolve().parents[1]
    os.environ.pop("NINA_VOICE_EDGE_ENABLE", None)
    s = load_voice_edge_settings(repo)
    assert s.device_id == "nina-jetson"
    assert "127.0.0.1" in s.llm_base_url


def test_asr_websocket_url_builds_mac_query() -> None:
    url = asr_websocket_url("http://127.0.0.1:6000", "nina-jetson")
    assert url.startswith("ws://127.0.0.1:6000/ws/audio")
    assert "mac=nina-jetson" in url


def test_nina_settings_includes_voice_edge() -> None:
    repo = Path(__file__).resolve().parents[1]
    ns = load_settings(repo)
    assert hasattr(ns, "voice_edge")
    assert ns.voice_edge.device_id
