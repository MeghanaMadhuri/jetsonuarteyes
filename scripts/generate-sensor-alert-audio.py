#!/usr/bin/env python3
"""Generate US-English gTTS MP3s for low-battery, touch, and obstacle sensor alerts.

Output (committed in repo)::

    nina/audio/alerts/low_battery.mp3
    nina/audio/alerts/touch.mp3
    nina/audio/alerts/obstacle.mp3
    nina/audio/alerts/cant_move.mp3

Examples::

    python3 scripts/generate-sensor-alert-audio.py
    python3 scripts/generate-sensor-alert-audio.py --lang en --tld us

Requirements::

    pip install --user gTTS
    sudo apt install -y ffmpeg mpg123
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nina.sensors.ads1115 import LOW_BATTERY_TTS  # noqa: E402
from nina.sensors.at42qt2120 import DEFAULT_TOUCH_TTS  # noqa: E402
from nina.services.sensor_alert_audio import (  # noqa: E402
    CANT_MOVE_ALERT_MP3,
    CANT_MOVE_TTS,
    DEFAULT_OBSTACLE_TTS,
    LOW_BATTERY_ALERT_MP3,
    OBSTACLE_ALERT_MP3,
    TOUCH_ALERT_MP3,
)


def _generate_one(
    out_path: Path,
    text: str,
    *,
    lang: str,
    tld: str,
) -> int:
    from nina.services.audio_generator import AudioGenerator, AudioGeneratorError

    err = AudioGenerator.is_available()
    if err:
        print(err, file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating {out_path.name}: {text!r} (lang={lang}, tld={tld})")
    try:
        AudioGenerator.generate(text, out_path, lang=lang, tld=tld, slow=False)
    except AudioGeneratorError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"  -> {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate low-battery and touch alert MP3s (US English gTTS)."
    )
    parser.add_argument("--lang", default="en", help="gTTS language (default: en).")
    parser.add_argument(
        "--tld",
        default="us",
        help="Google TLD for US English (default: us).",
    )
    args = parser.parse_args()

    rc = _generate_one(
        LOW_BATTERY_ALERT_MP3,
        LOW_BATTERY_TTS,
        lang=args.lang,
        tld=args.tld,
    )
    if rc != 0:
        return rc
    rc = _generate_one(
        TOUCH_ALERT_MP3,
        DEFAULT_TOUCH_TTS,
        lang=args.lang,
        tld=args.tld,
    )
    if rc != 0:
        return rc
    rc = _generate_one(
        OBSTACLE_ALERT_MP3,
        DEFAULT_OBSTACLE_TTS,
        lang=args.lang,
        tld=args.tld,
    )
    if rc != 0:
        return rc
    return _generate_one(
        CANT_MOVE_ALERT_MP3,
        CANT_MOVE_TTS,
        lang=args.lang,
        tld=args.tld,
    )


if __name__ == "__main__":
    raise SystemExit(main())
