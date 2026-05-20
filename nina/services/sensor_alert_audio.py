"""Pre-generated US-English MP3 alerts for low battery, touch, and obstacle (gTTS + ffmpeg).

Clips live under ``nina/audio/alerts/``. Regenerate with::

    python3 scripts/generate-sensor-alert-audio.py
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from nina.sensors.ads1115 import LOW_BATTERY_TTS
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS
from nina.services.audio_generator import AudioGenerator, AudioGeneratorError
from nina.services.audio_player import AudioPlayer

log = logging.getLogger("nina.services.sensor_alert_audio")

_ALERTS_DIR = Path(__file__).resolve().parents[1] / "audio" / "alerts"
LOW_BATTERY_ALERT_MP3 = _ALERTS_DIR / "low_battery.mp3"
TOUCH_ALERT_MP3 = _ALERTS_DIR / "touch.mp3"
OBSTACLE_ALERT_MP3 = _ALERTS_DIR / "obstacle.mp3"
CANT_MOVE_ALERT_MP3 = _ALERTS_DIR / "cant_move.mp3"

DEFAULT_OBSTACLE_TTS = "There is an obstacle in my way"
CANT_MOVE_TTS = "I can't move steadily any further, stopping now."


def low_battery_alert_path() -> Path:
    return LOW_BATTERY_ALERT_MP3


def touch_alert_path() -> Path:
    return TOUCH_ALERT_MP3


def obstacle_alert_path() -> Path:
    return OBSTACLE_ALERT_MP3


def cant_move_alert_path() -> Path:
    return CANT_MOVE_ALERT_MP3


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


# Cooldown for the spoken low-battery alert. Prevents:
#   * D-pad mashing while latched from spawning a new mpg123 every press
#     (two mpg123 processes on the same ALSA device collide / overlap),
#   * the every-0.2 V repeat warning from firing twice in the same poll
#     window when the pack drops sharply,
#   * the worker-refusal alerts from chattering on top of the initial
#     latch announcement.
# Default 8 s — slightly longer than the bundled MP3 itself (~3-4 s) so
# playback never overlaps, short enough to still announce per-0.2 V
# drops under heavy drain. ``0`` disables the cooldown entirely.
DEFAULT_LOW_BATTERY_COOLDOWN_SEC = 8.0


def _low_battery_cooldown_sec() -> float:
    raw = (os.environ.get("NINA_BATTERY_ALERT_COOLDOWN_SEC") or "").strip()
    if not raw:
        return DEFAULT_LOW_BATTERY_COOLDOWN_SEC
    try:
        return max(0.0, min(600.0, float(raw)))
    except ValueError:
        return DEFAULT_LOW_BATTERY_COOLDOWN_SEC


_low_batt_lock = threading.Lock()
# ``None`` = no previous alert in this process. Using a sentinel rather
# than ``0.0`` matters because ``time.monotonic()`` is small inside a
# freshly-started process (its reference point is undefined per the docs)
# — a ``0.0`` baseline would incorrectly suppress the very first alert
# fired in the first few seconds after the monitor thread starts.
_low_batt_last_at: Optional[float] = None


def _reset_low_battery_cooldown_for_tests() -> None:
    """Clear the spoken-alert cooldown timestamp. Test-only seam."""
    global _low_batt_last_at
    with _low_batt_lock:
        _low_batt_last_at = None


def maybe_speak_low_battery(phrase: Optional[str] = None) -> None:
    """Play the canonical low-battery alert MP3 (with gTTS fallback).

    Single TTS entry-point shared by:

    * :class:`nina.sensors.battery_ads1115_monitor.BatteryAds1115Monitor`
      for the initial latch announcement and the periodic 0.2 V repeat
      warnings.
    * :class:`sirena_ui.workers.drive_controller.DriveController` (and the
      Playback / Record workers) when a user-initiated motion command is
      refused because the pack is latched low.

    Routes through :func:`play_low_battery_alert` so the bundled
    ``nina/audio/alerts/low_battery.mp3`` is the actual voice the
    operator hears (the natural gTTS-rendered phrase Sirena ships with).
    Falls back to a one-shot gTTS render of the ``phrase`` argument
    (default :data:`nina.sensors.ads1115.LOW_BATTERY_TTS`) only when the
    bundled MP3 is missing — e.g. a dev workstation that hasn't run
    ``scripts/generate-sensor-alert-audio.py``.

    Playback is dispatched onto a daemon thread so the calling context
    (battery monitor poll loop, Qt drive worker, etc.) is never blocked.
    A module-level cooldown (:envvar:`NINA_BATTERY_ALERT_COOLDOWN_SEC`,
    default 8 s) suppresses overlapping playback when the same alert
    would fire multiple times in quick succession.
    """
    cooldown = _low_battery_cooldown_sec()
    now = time.monotonic()
    global _low_batt_last_at
    with _low_batt_lock:
        if (
            cooldown > 0
            and _low_batt_last_at is not None
            and now - _low_batt_last_at < cooldown
        ):
            return
        _low_batt_last_at = now

    def _run() -> None:
        try:
            play_low_battery_alert(phrase=phrase)
        except Exception:
            log.exception("Low battery alert playback failed")

    threading.Thread(target=_run, daemon=True, name="low-batt-alert").start()


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


def maybe_speak_obstacle_alert(phrase: Optional[str] = None) -> None:
    """Play the obstacle alert on a daemon thread so safety motion is not delayed."""

    def _run() -> None:
        try:
            play_obstacle_alert(phrase=phrase)
        except Exception:
            log.exception("Obstacle alert playback failed")

    threading.Thread(
        target=_run, daemon=True, name="obstacle-alert"
    ).start()


def play_cant_move_alert(*, phrase: str | None = None) -> None:
    """Drift-abort safety stop (bundled US English gTTS clip)."""
    play_bundled_or_gtts(
        cant_move_alert_path(),
        phrase=(phrase or "").strip() or CANT_MOVE_TTS,
        temp_basename="nina_cant_move_alert.mp3",
    )


def maybe_speak_cant_move_alert() -> None:
    """Play :func:`play_cant_move_alert` on a daemon thread (non-blocking)."""

    def _run() -> None:
        try:
            play_cant_move_alert()
        except Exception:
            log.exception("Can't-move alert playback failed")

    threading.Thread(
        target=_run, daemon=True, name="cant-move-alert"
    ).start()
