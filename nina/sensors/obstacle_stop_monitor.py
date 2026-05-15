"""Forward HC-SR04 obstacle stop (Jetson): wheels idle, lean brake, arms neutral, TTS.

Hardware (HC-SR04)
------------------
- **Vcc**: 5 V from a header that can supply the module (not 3.3 V-only).
- **Gnd**: common ground with the Jetson.
- **Trig** → Jetson GPIO **output** (BCM from ``NINA_OBSTACLE_HCSR04_TRIG``; default
  **BCM 4** = physical pin **7** on the reference Orin NX / Orin Nano 40-pin header,
  avoiding physical **35** / **BCM 19** which is often tied to audio on carriers).
  3.3 V high is normally recognized by the 5 V-powered module trigger input.
- **Echo** → **must** be level-shifted or divided before the Jetson GPIO **input**
  (BCM from ``NINA_OBSTACLE_HCSR04_ECHO``; default **BCM 9** = physical pin **21**):
  HC-SR04 Echo swings toward **5 V**; Orin NX header GPIO is **3.3 V** logic. Use a
  bidirectional level shifter or a divider (e.g. Echo → 2 kΩ → pin, pin → 1 kΩ → Gnd)
  so the Jetson sees ≤ 3.3 V.

Pin order on the PCB varies by batch: always follow the **silkscreen** (Vcc / Trig /
Echo / Gnd), not cable color.

BCM vs 40-pin location depends on carrier; verify with your pinout and optionally
``python3 -m nina.app.pin_probe --pin <bcm>``. Set ``NINA_JETSON_MODEL`` / ``JETSON_MODEL_NAME``
to match your module for ``Jetson.GPIO`` (e.g. Orin NX vs Orin Nano tables differ).

**GPIO conflict:** default Echo **BCM 9** still matches the autonomy **front_left**
Echo slot; default Trig **BCM 4** matches the ring's **rear_left Echo** BCM—do not
use the stock four-sensor ring on the same BCM **4** line without remapping. Do not
run two ``HCSR04Array`` instances on the same Trig/Echo pair. If the ring is disabled
with ``NINA_HCSR04_DISABLE=1``, this monitor still starts (see ``is_jetson_gpio_available``
in ``hcsr04.py``).

Software
--------
- **Jetson.GPIO** — JetPack image; install per NVIDIA if missing.
- **gTTS** — ``python3 -m pip install --user gTTS`` (same interpreter as the app).
- **ffmpeg** — ``sudo apt install -y ffmpeg`` (``AudioGenerator`` normalizes MP3).
- **mpg123** / **alsa-utils** — ``sudo apt install -y mpg123 alsa-utils`` for playback.

Enable with ``NINA_OBSTACLE_STOP_ENABLE=1``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from nina.sensors.hcsr04 import HCSR04Array, HCSR04Channel, is_jetson_gpio_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.obstacle_stop")

# Logical channel name for the single-sensor array (see ``HCSR04Array.read``).
FORWARD_OBSTACLE_POSITION = "forward_obstacle"


def obstacle_debounce_step(
    distance_mm: Optional[int],
    *,
    threshold_mm: int,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_hits)`` for threshold + debounce."""
    if distance_mm is not None and distance_mm <= threshold_mm:
        n = consecutive_hits + 1
        if n >= debounce_reads:
            return True, 0
        return False, n
    return False, 0


class ObstacleStopMonitor:
    """Background poll of one HC-SR04; fires ``NinaService.run_obstacle_stop_reaction``."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.obstacle_stop
        self._threshold_mm = int(s.threshold_mm)
        self._debounce_reads = int(s.debounce_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._position = FORWARD_OBSTACLE_POSITION
        self._array = HCSR04Array(
            [
                HCSR04Channel(
                    self._position,
                    int(s.hcsr04_trig_bcm),
                    int(s.hcsr04_echo_bcm),
                )
            ]
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30

    def start(self) -> None:
        ok, msg = is_jetson_gpio_available()
        if not ok:
            raise RuntimeError(msg)
        self._array.open()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ObstacleStopMonitor", daemon=True
        )
        self._thread.start()
        log.info(
            "Obstacle stop monitor started (trig=BCM %s echo=BCM %s threshold=%s mm)",
            self._svc.settings.obstacle_stop.hcsr04_trig_bcm,
            self._svc.settings.obstacle_stop.hcsr04_echo_bcm,
            self._threshold_mm,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=90.0)
            if t.is_alive():
                log.warning("Obstacle stop monitor thread did not exit in time")
        try:
            self._array.close()
        except Exception:
            log.debug("HC-SR04 array close failed", exc_info=True)
        log.info("Obstacle stop monitor stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            r = self._array.read(self._position)
            dmm = r.distance_mm if r is not None else None
            fire, self._hits = obstacle_debounce_step(
                dmm,
                threshold_mm=self._threshold_mm,
                debounce_reads=self._debounce_reads,
                consecutive_hits=self._hits,
            )
            now = time.monotonic()
            if fire and (now - self._last_fire_mono) >= self._cooldown_sec:
                self._last_fire_mono = now
                try:
                    self._svc.run_obstacle_stop_reaction()
                except Exception:
                    log.exception("Obstacle stop reaction failed")
            time.sleep(0.05)
