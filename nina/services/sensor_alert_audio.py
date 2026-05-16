"""Pre-generated US-English MP3 alerts for low battery, touch, and obstacle (gTTS + ffmpeg).

Clips live under ``nina/audio/alerts/``. Regenerate with::

    python3 scripts/generate-sensor-alert-audio.py
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from nina.sensors.ads1115 import LOW_BATTERY_TTS
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS
from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer

log = logging.getLogger("nina.services.sensor_alert_audio")

_ALERTS_DIR = Path(__file__).resolve().parents[1] / "audio" / "alerts"
LOW_BATTERY_ALERT_MP3 = _ALERTS_DIR / "low_battery.mp3"
TOUCH_ALERT_MP3 = _ALERTS_DIR / "touch.mp3"
OBSTACLE_ALERT_MP3 = _ALERTS_DIR / "obstacle.mp3"

DEFAULT_OBSTACLE_TTS = "There is an obstacle in my way"


def low_battery_alert_path() -> Path:
    return LOW_BATTERY_ALERT_MP3


def touch_alert_path() -> Path:
    return TOUCH_ALERT_MP3


def obstacle_alert_path() -> Path:
    return OBSTACLE_ALERT_MP3


def play_bundled_or_gtts(
    bundled: Path,
    *,
    phrase: str,
    temp_basename: str,
    lang: str = "en",
    tld: str = "us",
) -> None:
    """Play ``bundled`` when the file exists; otherwise synthesize *phrase* with gTTS."""
    text = (phrase or "").strip()
    if bundled.is_file():
        AudioPlayer().play(bundled)
        return
    if not text:
        log.warning("No bundled alert at %s and empty phrase", bundled)
        return
    out = Path(tempfile.gettempdir()) / temp_basename
    try:
        AudioGenerator.generate(text, out, lang=lang, tld=tld, slow=False)
        AudioPlayer().play(out)
    except AudioGeneratorError as exc:
        log.warning("Sensor alert TTS unavailable (%s): %s", bundled.name, exc)
    except Exception:
        log.exception("Sensor alert playback failed for %s", bundled.name)


def play_low_battery_alert(*, phrase: str | None = None) -> None:
    play_bundled_or_gtts(
        low_battery_alert_path(),
        phrase=(phrase or "").strip() or LOW_BATTERY_TTS,
        temp_basename="nina_low_battery_alert.mp3",
    )


def play_touch_alert(*, phrase: str | None = None) -> None:
    play_bundled_or_gtts(
        touch_alert_path(),
        phrase=(phrase or "").strip() or DEFAULT_TOUCH_TTS,
        temp_basename="nina_touch_alert.mp3",
    )


def play_obstacle_alert(*, phrase: str | None = None) -> None:
    play_bundled_or_gtts(
        obstacle_alert_path(),
        phrase=(phrase or "").strip() or DEFAULT_OBSTACLE_TTS,
        temp_basename="nina_obstacle_alert.mp3",
    )
