"""Unit tests for `nina.services.audio_player` sample-rate helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def test_pcm_output_rate_defaults_to_gtts_nominal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nina.services.audio_player import _GREETING_MP3_SAMPLE_RATE_HZ, _pcm_output_rate_hz

    monkeypatch.delenv("NINA_AUDIO_OUTPUT_RATE", raising=False)
    assert _pcm_output_rate_hz() == _GREETING_MP3_SAMPLE_RATE_HZ


def test_pcm_output_rate_auto_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import _pcm_output_rate_hz

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "auto")
    assert _pcm_output_rate_hz() is None

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "native")
    assert _pcm_output_rate_hz() is None


def test_preroll_rate_matches_greeting_when_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import (
        _GREETING_MP3_SAMPLE_RATE_HZ,
        _preroll_wav_sample_rate_hz,
    )

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "auto")
    assert _preroll_wav_sample_rate_hz() == _GREETING_MP3_SAMPLE_RATE_HZ


def test_pcm_output_rate_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import _pcm_output_rate_hz

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "44100")
    assert _pcm_output_rate_hz() == 44100


def test_mpg123_command_includes_default_gtts_rate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho ok\n")
    fake_mpg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.delenv("NINA_AUDIO_OUTPUT_RATE", raising=False)
    from nina.services.audio_player import _GREETING_MP3_SAMPLE_RATE_HZ, mpg123_command_for

    wav = tmp_path / "x.mp3"
    wav.touch()
    cmd = mpg123_command_for(wav)
    assert cmd is not None
    assert "-r" in cmd
    assert str(_GREETING_MP3_SAMPLE_RATE_HZ) in cmd


def test_wav_command_includes_aplay_device_when_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_aplay = tmp_path / "aplay"
    fake_aplay.write_text("#!/bin/sh\necho ok\n")
    fake_aplay.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_GREET_APLAY_DEVICE", "plughw:CARD=max98357a,DEV=0")
    from nina.services.audio_player import AudioPlayer

    wav = tmp_path / "x.wav"
    wav.touch()
    cmd = AudioPlayer()._command_for(wav)
    assert cmd is not None
    assert "-D" in cmd
    assert "plughw:CARD=max98357a,DEV=0" in cmd


def test_mpg123_command_includes_rate_when_forced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho ok\n")
    fake_mpg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "48000")
    from nina.services.audio_player import mpg123_command_for

    wav = tmp_path / "x.mp3"
    wav.touch()
    cmd = mpg123_command_for(wav)
    assert cmd is not None
    assert "-r" in cmd
    assert "48000" in cmd


def test_audio_player_can_decode_mp3_via_temp_wav_and_aplay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho mpg\n")
    fake_mpg.chmod(0o755)
    fake_aplay = tmp_path / "aplay"
    fake_aplay.write_text("#!/bin/sh\necho aplay\n")
    fake_aplay.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_AUDIO_MP3_VIA_APLAY", "1")
    monkeypatch.setenv("NINA_GREET_APLAY_DEVICE", "plughw:CARD=APE,DEV=0")
    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "48000")
    monkeypatch.setenv("NINA_AUDIO_APLAY_STEREO_MODE", "left")
    from nina.services.audio_player import AudioPlayer

    mp3 = tmp_path / "x.mp3"
    mp3.touch()
    cmd = AudioPlayer()._command_for(mp3)
    assert cmd is not None
    assert cmd[:3] == ["/bin/sh", "-c", cmd[2]]
    assert "mpg123 -q -r" in cmd[2]
    assert "aplay -q -D" in cmd[2]
    assert str(mp3) in cmd
    assert "plughw:CARD=APE,DEV=0" in cmd
    assert "48000" in cmd
    assert "left" in cmd
    assert "python3 -" in cmd[2]


def test_invalid_aplay_stereo_mode_falls_back_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NINA_AUDIO_APLAY_STEREO_MODE", "garbage")
    from nina.services.audio_player import _aplay_stereo_mode

    assert _aplay_stereo_mode() == "none"


def test_mpg123_command_omits_rate_when_auto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho ok\n")
    fake_mpg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "auto")
    from nina.services.audio_player import mpg123_command_for

    wav = tmp_path / "x.mp3"
    wav.touch()
    cmd = mpg123_command_for(wav)
    assert cmd is not None
    assert "-r" not in cmd


def test_real_mpg123_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    if not shutil.which("mpg123"):
        pytest.skip("mpg123 not installed")
    monkeypatch.delenv("NINA_AUDIO_OUTPUT_RATE", raising=False)
    from nina.services.audio_player import _GREETING_MP3_SAMPLE_RATE_HZ, mpg123_command_for

    cmd = mpg123_command_for(Path("/no/such/file.mp3"))
    assert cmd is not None
    assert "-r" in cmd
    assert str(_GREETING_MP3_SAMPLE_RATE_HZ) in cmd
