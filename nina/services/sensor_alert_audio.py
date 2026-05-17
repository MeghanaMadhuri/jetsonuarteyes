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
from nina.services.bldc_speech_alerts import maybe_speak_bldc_alert

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


def maybe_speak_low_battery(phrase: str | None = None) -> None:
    """Speak the canonical low-battery warning via the bldc espeak path.

    This is the single TTS entry-point shared by:

    * :class:`nina.sensors.battery_ads1115_monitor.BatteryAds1115Monitor`
      for the initial latch announcement and the periodic 0.2 V repeat
      warnings — phrasing comes from
      :data:`nina.sensors.ads1115.LOW_BATTERY_TTS`,
    * :class:`sirena_ui.workers.drive_controller.DriveController` (and the
      Playback / Record workers) when a user-initiated motion command is
      refused because the pack is latched low.

    Routing every warning through :func:`maybe_speak_bldc_alert` means:

    1. all three paths share the same voice, so the operator hears a
       consistent message regardless of which subsystem refused them,
    2. the bldc-speech-alerts per-message cooldown
       (:envvar:`NINA_BLDC_ALERT_COOLDOWN_SEC`, default 12 s) naturally
       suppresses chatter when the operator mashes the D-pad while
       latched, and
    3. there is no MP3 asset to keep in sync with the new short phrase.
    """
    text = (phrase or "").strip() or LOW_BATTERY_TTS
    try:
        maybe_speak_bldc_alert(text)
    except Exception:
        log.exception("Low battery espeak alert failed")


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
