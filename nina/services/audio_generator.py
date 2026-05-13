"""
Thin wrapper around gTTS for generating Nina action audio clips.

Audio is re-encoded after download to **44.1 kHz**, **mono**, **48 kbps** MP3
(CD-rate PCM resample + fixed bitrate; replaces raw gTTS sizing).

Kept tiny on purpose so the import is cheap and the GUI can probe
availability (`is_available()`) without paying the cost of importing
the rest of gTTS until the user actually clicks "Generate".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Standard music/PCM rate for re-encoded speech clips.
_OUTPUT_SAMPLE_RATE_HZ = 44100
# Target MP3 bitrate after ffmpeg (mono).
_OUTPUT_AUDIO_BITRATE = "48k"


class AudioGeneratorError(RuntimeError):
    pass


def _python_diagnostic() -> str:
    """Where the running interpreter lives - useful when the user
    `pip install`s gTTS into a different Python than the one running
    the GUI (common on Jetson Nano, where the desktop entry can launch
    `/usr/bin/python3` while `pip` lives in a venv)."""
    return (
        f"Python: {sys.executable}\n"
        f"Version: {sys.version.split()[0]}\n"
        f"Install matching gTTS with:\n"
        f"    {sys.executable} -m pip install --user gTTS"
    )


def _ffmpeg_binary() -> Optional[str]:
    return shutil.which("ffmpeg")


def _reencode_mp3_normalized(src: Path, dst: Path) -> None:
    """Resample/re-encode MP3 to 44.1 kHz mono at `_OUTPUT_AUDIO_BITRATE`."""
    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        raise AudioGeneratorError(
            "ffmpeg is required to normalize MP3 (44.1 kHz, 48 kbps).\n"
            "Install with:\n"
            "    sudo apt install -y ffmpeg"
        )
    tmp_path = dst.with_suffix(dst.suffix + ".tmp")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-ar",
                str(_OUTPUT_SAMPLE_RATE_HZ),
                "-ac",
                "1",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                _OUTPUT_AUDIO_BITRATE,
                # Strict CBR-style frames; bit reservoir can confuse tools that guess
                # bitrate from frame sizes (some file-manager property dialogs).
                "-reservoir",
                "0",
                "-joint_stereo",
                "0",
                # Output name is *.mp3.tmp so the extension is not ".mp3"; muxer
                # must be explicit or ffmpeg errors with "Unable to find a suitable
                # output format".
                "-f",
                "mp3",
                # Skip ID3 at the start so quick probes see MPEG frames immediately;
                # keep Xing/Info for duration (helps some readers).
                "-write_xing",
                "1",
                "-id3v2_version",
                "0",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        os.replace(tmp_path, dst)
    except subprocess.CalledProcessError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        detail = (exc.stderr or exc.stdout or "").strip()
        raise AudioGeneratorError(
            "ffmpeg failed while encoding 44.1 kHz / 48 kbps MP3."
            + (f"\n{detail}" if detail else "")
        ) from exc


class AudioGenerator:
    @staticmethod
    def is_available() -> Optional[str]:
        """Return None if gTTS + ffmpeg are usable, else a human-readable error.

        We catch *any* exception (not just ImportError) because gTTS or
        one of its deps (click, requests, urllib3) occasionally raises
        OSError/SyntaxError/AttributeError on the Jetson Nano's stock
        Python when wheels are mismatched.
        """
        try:
            import gtts  # noqa: F401
        except ImportError as exc:
            return (
                "gTTS is not installed for this Python.\n\n"
                f"{_python_diagnostic()}\n\n"
                f"(import error: {exc})"
            )
        except Exception as exc:  # broken install, dep mismatch, etc.
            return (
                "gTTS is installed but failed to import. This usually "
                "means a dependency (requests / urllib3 / click) is "
                "broken or built for a different Python.\n\n"
                f"{_python_diagnostic()}\n\n"
                f"(import error: {type(exc).__name__}: {exc})"
            )
        if not _ffmpeg_binary():
            return (
                "ffmpeg was not found on PATH. It is required to re-encode "
                "gTTS output to 44.1 kHz / 48 kbps MP3.\n"
                "Install with:\n"
                "    sudo apt install -y ffmpeg"
            )
        return None

    @staticmethod
    def generate(
        text: str,
        out_path: Path,
        *,
        lang: str = "en",
        tld: str = "us",
        slow: bool = False,
    ) -> Path:
        """
        Render `text` to an MP3 at `out_path` using gTTS, then re-encode with
        ffmpeg to **44.1 kHz**, **mono**, **48 kbps** MP3.

        Google's encoder typically emits **mono MPEG audio at 24000 Hz** (see
        ``ffprobe`` on the file). Playback defaults in ``audio_player`` match
        that rate so greetings avoid an extra resample step before ALSA.

        `tld` selects **which Google Translate host** handles the request (this
        is **not** device GPS). Accent tracks that endpoint: ``us`` →
        ``translate.google.us`` (most reliably **American English**, including
        outside North America); ``com`` → ``translate.google.com`` (often similar,
        but routing can vary); ``co.uk`` → UK; ``com.au`` → Australian;
        ``co.in`` → Indian English.

        Note: gTTS only accepts coarse ``lang`` codes like ``en`` (there is no
        working ``en-US`` tag in the upstream API).

        Raises `AudioGeneratorError` if generation fails (network down,
        bad language code, etc.).
        """
        text = (text or "").strip()
        if not text:
            raise AudioGeneratorError("Cannot generate audio: text is empty.")

        err = AudioGenerator.is_available()
        if err:
            raise AudioGeneratorError(err)

        try:
            from gtts import gTTS
        except Exception as exc:
            raise AudioGeneratorError(
                f"Failed to import gTTS even though the package is "
                f"present:\n{type(exc).__name__}: {exc}\n\n"
                f"{_python_diagnostic()}"
            ) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path_str = tempfile.mkstemp(suffix=".mp3", prefix="gtts_")
        os.close(fd)
        raw_path = Path(raw_path_str)
        try:
            try:
                gTTS(text=text, lang=lang, tld=tld, slow=slow).save(str(raw_path))
            except Exception as exc:  # gTTS surfaces network/lang errors loosely
                raise AudioGeneratorError(f"gTTS failed: {exc}") from exc
            _reencode_mp3_normalized(raw_path, out_path)
        finally:
            try:
                raw_path.unlink(missing_ok=True)
            except OSError:
                pass
        return out_path
