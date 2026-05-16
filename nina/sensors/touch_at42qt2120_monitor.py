"""Background poll of AT42QT2120; fires ``NinaService.run_touch_reaction`` on touch."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

from nina.sensors.at42qt2120 import AT42QT2120, is_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.touch_at42qt2120")


def touch_debounce_step(
    touched: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_hits)`` while *touched* is true."""
    if touched:
        n = consecutive_hits + 1
        if n >= debounce_reads:
            return True, 0
        return False, n
    return False, 0


class TouchAt42qt2120Monitor:
    """Poll AT42QT2120; on touch, park motors + US-English gTTS."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.touch_at42qt2120
        self._debounce_reads = int(s.debounce_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._poll_sec = float(s.poll_interval_sec)
        self._touch = AT42QT2120(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30

    def start(self) -> None:
        ok, msg = is_available(self._svc.settings.touch_at42qt2120.i2c_bus)
        if not ok:
            raise RuntimeError(msg)
        self._touch.open()
        try:
            self._touch.verify_chip_id()
        except Exception:
            self._touch.close()
            raise
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TouchAt42qt2120Monitor", daemon=True
        )
        self._thread.start()
        log.info(
            "AT42QT2120 touch monitor started (i2c-%s 0x%02X poll=%.2f s)",
            self._svc.settings.touch_at42qt2120.i2c_bus,
            self._svc.settings.touch_at42qt2120.i2c_address,
            self._poll_sec,
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=5.0)
            if t.is_alive():
                log.warning("AT42QT2120 touch monitor thread did not exit in time")
        try:
            self._touch.close()
        except Exception:
            log.debug("AT42QT2120 close failed", exc_info=True)
        log.info("AT42QT2120 touch monitor stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._touch.is_calibrating():
                    self._hits = 0
                    time.sleep(self._poll_sec)
                    continue
                touched = self._touch.any_touch()
            except Exception:
                log.debug("AT42QT2120 read failed", exc_info=True)
                self._hits = 0
                time.sleep(max(self._poll_sec, 0.1))
                continue

            if not touched:
                self._hits = 0
                time.sleep(self._poll_sec)
                continue

            now = time.monotonic()
            if (now - self._last_fire_mono) < self._cooldown_sec:
                time.sleep(self._poll_sec)
                continue

            fire, self._hits = touch_debounce_step(
                True,
                debounce_reads=self._debounce_reads,
                consecutive_hits=self._hits,
            )
            if fire:
                self._last_fire_mono = now
                self._hits = 0
                try:
                    self._svc.run_touch_reaction()
                except Exception:
                    log.exception("Touch reaction failed")

            time.sleep(self._poll_sec)
