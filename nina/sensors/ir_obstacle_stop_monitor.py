"""GP2Y0E02B IR obstacle stop on header I²C (bus 7 with IMU / ADC / touch).

Polls the Sharp IR continuously by default. When a valid reading is at or below
the configured threshold (default **400 mm** = 40 cm), fires
``NinaService.run_obstacle_stop_reaction``.

Enable with ``NINA_IR_OBSTACLE_STOP_ENABLE=1`` (default on). Shares
``/dev/i2c-7`` @ **0x40** with MPU-9250 (**0x68**), ADS1115 (**0x48**),
AT42QT2120 (**0x1C**). Useful sensor range is about **4–50 cm**; readings
outside that band are ignored (``distance_mm`` is ``None``).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from nina.sensors.gp2y0e02b import GP2Y0E02B, is_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.ir_obstacle_stop")

# When the sensor is missing or the bus NAKs, avoid ~20 Hz WARNING spam.
_OPEN_WARN_INTERVAL_SEC = 60.0
_OPEN_BACKOFF_MAX_SEC = 30.0


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


class IrObstacleStopMonitor:
    """Motion-gated GP2Y0E02B poll; stops drive + neutral pose + obstacle TTS."""

    def __init__(
        self,
        service: "NinaService",
        *,
        in_motion_fn: Callable[[], bool],
    ) -> None:
        self._svc = service
        self._in_motion = in_motion_fn
        s = service.settings.ir_obstacle_stop
        self._motion_gated = bool(getattr(s, "motion_gated", False))
        self._threshold_mm = int(s.threshold_mm)
        self._debounce_reads = int(s.debounce_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._poll_sec = max(0.02, float(s.poll_interval_sec))
        self._sensor = GP2Y0E02B(
            bus=int(s.i2c_bus),
            address=int(s.i2c_address),
            position="forward_ir",
        )
        self._sensor_open = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hits = 0
        self._last_fire_mono = -1e30
        self._last_distance_mm: Optional[int] = None
        self._last_read_mono: Optional[float] = None
        self._open_backoff_sec = self._poll_sec
        self._last_open_warn_mono = -1e30

    def start(self) -> None:
        s = self._svc.settings.ir_obstacle_stop
        ok, msg = is_available(s.i2c_bus, s.i2c_address)
        if not ok:
            raise RuntimeError(msg)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="IrObstacleStopMonitor", daemon=True
        )
        self._thread.start()
        log.info(
            "IR obstacle stop monitor started (i2c-%s 0x%02X threshold=%s mm, mode=%s)",
            self._svc.settings.ir_obstacle_stop.i2c_bus,
            self._svc.settings.ir_obstacle_stop.i2c_address,
            self._threshold_mm,
            "motion-gated" if self._motion_gated else "continuous",
        )

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=5.0)
            if t.is_alive():
                log.warning("IR obstacle stop monitor thread did not exit in time")
        self._close_sensor()
        log.info("IR obstacle stop monitor stopped")

    def status(self) -> dict:
        """Small UI-facing status snapshot for Health/Home indicators."""
        thread = self._thread
        running = bool(thread is not None and thread.is_alive())
        age_sec = (
            None
            if self._last_read_mono is None
            else max(0.0, time.monotonic() - self._last_read_mono)
        )
        blocked = (
            self._last_distance_mm is not None
            and self._last_distance_mm <= self._threshold_mm
        )
        if not running:
            detail = "monitor stopped"
        elif not self._sensor_open:
            detail = "sensor not open"
        elif self._last_distance_mm is None:
            detail = f"ready, no valid reading (threshold {self._threshold_mm} mm)"
        elif blocked:
            detail = f"BLOCKED {self._last_distance_mm} mm <= {self._threshold_mm} mm"
        else:
            detail = f"clear {self._last_distance_mm} mm > {self._threshold_mm} mm"
        return {
            "running": running,
            "sensor_open": bool(self._sensor_open),
            "motion_gated": bool(self._motion_gated),
            "threshold_mm": int(self._threshold_mm),
            "distance_mm": self._last_distance_mm,
            "age_sec": age_sec,
            "blocked": bool(blocked),
            "detail": detail,
        }

    def _open_sensor(self) -> float:
        """Try to open the sensor. Returns seconds to sleep before the next poll."""
        if self._sensor_open:
            self._open_backoff_sec = self._poll_sec
            return self._poll_sec
        try:
            self._sensor.open()
            self._sensor_open = True
            self._open_backoff_sec = self._poll_sec
            return self._poll_sec
        except Exception as exc:
            now = time.monotonic()
            if (now - self._last_open_warn_mono) >= _OPEN_WARN_INTERVAL_SEC:
                self._last_open_warn_mono = now
                log.warning("GP2Y0E02B open failed: %s", exc)
            self._open_backoff_sec = min(
                _OPEN_BACKOFF_MAX_SEC,
                max(self._poll_sec, self._open_backoff_sec * 2.0),
            )
            return self._open_backoff_sec

    def _close_sensor(self) -> None:
        if not self._sensor_open:
            return
        try:
            self._sensor.close()
        except Exception:
            log.debug("GP2Y0E02B close failed", exc_info=True)
        self._sensor_open = False
        self._hits = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._motion_gated and not self._in_motion():
                self._close_sensor()
                time.sleep(self._poll_sec)
                continue

            sleep_sec = self._open_sensor()
            if not self._sensor_open:
                time.sleep(sleep_sec)
                continue

            r = self._sensor.read()
            dmm = r.distance_mm if r is not None else None
            self._last_distance_mm = dmm
            self._last_read_mono = time.monotonic()
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
                    log.exception("IR obstacle stop reaction failed")
            time.sleep(self._poll_sec)
