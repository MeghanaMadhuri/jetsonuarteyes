"""Local edge voice assistant (ASR + LLM + TTS) for Nina on Jetson."""

from nina.voice.settings import VoiceEdgeSettings, load_voice_edge_settings

__all__ = ["VoiceEdgeSettings", "load_voice_edge_settings"]
