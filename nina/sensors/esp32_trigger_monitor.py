"""Poll a Jetson GPIO line for an ESP32 trigger (default BCM 17 / pin 11, active-high).

When the line goes high (debounced rising edge), runs a named arm action on
``NinaService`` (default ``namaste``). Default pin is the Orin Nano header
**physical pin 11** (BCM 17, legacy E-stop 1 pad); navigation does not drive it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.esp32_trigger")


def esp32_rising_edge_step(
    high: bool,
    prev_high: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_hits)`` for one poll."""
    if not high:
        return False, 0
    n = 1 if not prev_high else consecutive_hits + 1
    if n >= debounce_reads:
        return True, 0
    return False, n


def esp32_release_rearm_step(
    high: bool,
    *,
    armed: bool,
    release_reads: int,
    consecutive_low: int,
) -> tuple[bool, int]:
    """Return ``(armed, new_consecutive_low)`` after a reaction."""
    if armed:
        return True, 0
    if high:
        return False, 0
    n = consecutive_low + 1
    if n >= release_reads:
        return True, 0
    return False, n


class Esp32GpioInput:
    """BCM input reader. Does not call ``GPIO.cleanup()`` (shared with navigation)."""

    def __init__(self, bcm_pin: int, *, active_high: bool = True) -> None:
        self._pin = int(bcm_pin)
        self._active_high = bool(active_high)
        self._gpio = None

    def open(self) -> None:
        try:
            import Jetson.GPIO as GPIO  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Jetson.GPIO is required for ESP32 trigger input. "
                "Install with: pip install Jetson.GPIO"
            ) from exc
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        self._gpio = GPIO
        log.info("ESP32 trigger GPIO BCM %s configured as INPUT (PUD_DOWN)", self._pin)

    def read_high(self) -> bool:
        if self._gpio is None:
            raise RuntimeError("ESP32 GPIO not opened")
        raw = bool(self._gpio.input(self._pin))
        return raw if self._active_high else not raw

    def close(self) -> None:
        self._gpio = None


class Esp32TriggerMonitor:
    """Background poll; fires ``NinaService.run_esp32_trigger_reaction`` on HIGH."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.esp32_trigger
        self._debounce_reads = int(s.debounce_reads)
        self._release_reads = int(s.release_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._poll_sec = max(0.02, float(s.poll_interval_sec))
        self._action_name = str(s.action_name)
        self._gpio = Esp32GpioInput(int(s.gpio_bcm), active_high=s.active_high)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._armed = True
        self._prev_high = False
        self._hits = 0
        self._release_hits = 0
        self._last_fire_mono = -1e30
        self._reaction_lock = threading.Lock()

    def start(self) -> None:
        self._gpio.open()
        self._armed = True
        self._prev_high = False
        self._hits = 0
        self._release_hits = 0
        self._last_fire_mono = -1e30
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="Esp32TriggerMonitor", daemon=True
        )
        self._thread.start()
        s = self._svc.settings.esp32_trigger
        log.info(
            "ESP32 trigger monitor started (BCM %s active_high=%s action=%s "
            "poll=%.2fs debounce=%d release=%d cooldown=%.1fs)",
            s.gpio_bcm,
            s.active_high,
            self._action_name,
            self._poll_sec,
            self._debounce_reads,
            self._release_reads,
            self._cooldown_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=5.0)
            if t.is_alive():
                log.warning("ESP32 trigger monitor thread did not exit in time")
        try:
            self._gpio.close()
        except Exception:
            log.debug("ESP32 GPIO close failed", exc_info=True)
        log.info("ESP32 trigger monitor stopped")

    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                high = self._gpio.read_high()
            except Exception:
                log.debug("ESP32 GPIO read failed", exc_info=True)
                self._hits = 0
                self._prev_high = False
                time.sleep(max(self._poll_sec, 0.1))
                continue

            now = time.monotonic()
            fire, self._hits = esp32_rising_edge_step(
                high,
                self._prev_high,
                debounce_reads=self._debounce_reads,
                consecutive_hits=self._hits,
            )
            self._prev_high = high

            if fire and self._armed and (now - self._last_fire_mono) >= self._cooldown_sec:
                if self._reaction_lock.acquire(blocking=False):
                    try:
                        self._last_fire_mono = now
                        self._armed = False
                        self._release_hits = 0
                        log.info(
                            "ESP32 trigger fired — playing action '%s'",
                            self._action_name,
                        )
                        try:
                            self._svc.run_esp32_trigger_reaction(self._action_name)
                        except Exception:
                            log.exception("ESP32 trigger reaction failed")
                    finally:
                        self._reaction_lock.release()

            self._armed, self._release_hits = esp32_release_rearm_step(
                high,
                armed=self._armed,
                release_reads=self._release_reads,
                consecutive_low=self._release_hits,
            )

            time.sleep(self._poll_sec)
