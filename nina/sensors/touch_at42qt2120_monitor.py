"""Background poll of AT42QT2120; fires ``NinaService.run_touch_reaction`` on touch."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional, Tuple

from nina.sensors.at42qt2120 import AT42QT2120, is_available

if TYPE_CHECKING:
    from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("nina.sensors.touch_at42qt2120")


def touch_release_rearm_step(
    touched: bool,
    *,
    armed: bool,
    release_reads: int,
    consecutive_clear: int,
) -> tuple[bool, int]:
    """Return ``(armed, new_consecutive_clear)`` after a reaction.

    While disarmed, require *release_reads* consecutive polls with no touch
    before the monitor may fire again.
    """
    if armed:
        return True, 0
    if not touched:
        n = consecutive_clear + 1
        if n >= release_reads:
            return True, 0
        return False, n
    return False, 0


def touch_rising_edge_debounce_step(
    touched: bool,
    prev_touched: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Return ``(should_fire, new_consecutive_hits)`` for one poll.

    Counts consecutive *touched* samples starting at a rising edge
    (``touched and not prev_touched``). Resets when *touched* is false.
    """
    if not touched:
        return False, 0
    if not prev_touched:
        n = 1
    else:
        n = consecutive_hits + 1
    if n >= debounce_reads:
        return True, 0
    return False, n


def touch_stuck_high_step(
    touched_raw: bool,
    *,
    stuck_latched: bool,
    touch_high_since_mono: Optional[float],
    stuck_clear_hits: int,
    now: float,
    stuck_after_sec: float,
    stuck_clear_reads: int,
) -> Tuple[bool, bool, Optional[float], int, bool]:
    """Suppress a latched STATUS-high line (common when the electrode back touches metal).

    Returns:
        ``(effective_touched, new_stuck_latched, new_touch_high_since_mono,
        new_stuck_clear_hits, newly_entered_stuck)``.
    """
    if not touched_raw:
        touch_high_since_mono = None
        if stuck_latched:
            hits = stuck_clear_hits + 1
            if hits >= stuck_clear_reads:
                return False, False, None, 0, False
            return False, True, None, hits, False
        return False, False, None, 0, False

    if stuck_latched:
        return False, True, touch_high_since_mono, 0, False

    if touch_high_since_mono is None:
        touch_high_since_mono = now
    if (now - touch_high_since_mono) >= stuck_after_sec:
        return False, True, touch_high_since_mono, 0, True
    return True, False, touch_high_since_mono, 0, False


def touch_baseline_ready_step(
    effective_touched: bool,
    *,
    baseline_ready: bool,
    baseline_clear_reads: int,
    consecutive_clear: int,
) -> Tuple[bool, int]:
    """Require a clean idle window after boot before touch reactions may fire."""
    if baseline_ready:
        return True, 0
    if effective_touched:
        return False, 0
    n = consecutive_clear + 1
    if n >= baseline_clear_reads:
        return True, 0
    return False, n


# Back-compat alias for older tests / imports.
def touch_debounce_step(
    touched: bool,
    *,
    debounce_reads: int,
    consecutive_hits: int,
) -> tuple[bool, int]:
    """Legacy level-trigger debounce (prefer :func:`touch_rising_edge_debounce_step`)."""
    if touched:
        n = consecutive_hits + 1
        if n >= debounce_reads:
            return True, 0
        return False, n
    return False, 0


class TouchAt42qt2120Monitor:
    """Poll AT42QT2120; on touch, stop drive, speak, park motors."""

    def __init__(self, service: "NinaService") -> None:
        self._svc = service
        s = service.settings.touch_at42qt2120
        self._debounce_reads = int(s.debounce_reads)
        self._release_reads = int(s.release_reads)
        self._baseline_clear_reads = int(s.baseline_clear_reads)
        self._stuck_after_sec = float(s.stuck_high_sec)
        self._stuck_clear_reads = int(s.stuck_clear_reads)
        self._cooldown_sec = float(s.cooldown_sec)
        self._blind_sec = float(s.blind_after_reaction_sec)
        self._poll_sec = float(s.poll_interval_sec)
        self._touch = AT42QT2120(s.i2c_bus, s.i2c_address)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._armed = True
        self._baseline_ready = False
        self._baseline_clear_hits = 0
        self._stuck_latched = False
        self._stuck_clear_hits = 0
        self._touch_high_since_mono: Optional[float] = None
        self._prev_touched = False
        self._hits = 0
        self._release_hits = 0
        self._last_fire_mono = -1e30
        self._blind_until_mono = -1e30

    def start(self) -> None:
        s = self._svc.settings.touch_at42qt2120
        ok, msg = is_available(
            s.i2c_bus,
            s.i2c_address,
            probe_attempts=max(1, int(s.startup_probe_attempts)),
            probe_delay_sec=max(0.0, float(s.startup_probe_delay_sec)),
        )
        if not ok:
            raise RuntimeError(msg)
        self._touch.open()
        try:
            self._touch.verify_chip_id()
        except Exception:
            self._touch.close()
            raise
        self._armed = True
        self._baseline_ready = False
        self._baseline_clear_hits = 0
        self._stuck_latched = False
        self._stuck_clear_hits = 0
        self._touch_high_since_mono = None
        self._prev_touched = False
        self._hits = 0
        self._release_hits = 0
        self._last_fire_mono = -1e30
        self._blind_until_mono = -1e30
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="TouchAt42qt2120Monitor", daemon=True
        )
        self._thread.start()
        log.info(
            "AT42QT2120 touch monitor started (i2c-%s 0x%02X poll=%.2fs "
            "debounce=%d release=%d baseline_clear=%d stuck=%.1fs cooldown=%.1fs blind=%.2fs)",
            self._svc.settings.touch_at42qt2120.i2c_bus,
            self._svc.settings.touch_at42qt2120.i2c_address,
            self._poll_sec,
            self._debounce_reads,
            self._release_reads,
            self._baseline_clear_reads,
            self._stuck_after_sec,
            self._cooldown_sec,
            self._blind_sec,
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

    def is_running(self) -> bool:
        """True when the poll thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._touch.is_calibrating():
                    self._hits = 0
                    self._prev_touched = False
                    time.sleep(self._poll_sec)
                    continue
                touched_raw = self._touch.any_touch()
            except Exception:
                log.debug("AT42QT2120 read failed", exc_info=True)
                self._hits = 0
                self._prev_touched = False
                self._touch_high_since_mono = None
                time.sleep(max(self._poll_sec, 0.1))
                continue

            now = time.monotonic()
            touched, self._stuck_latched, self._touch_high_since_mono, self._stuck_clear_hits, newly_stuck = (
                touch_stuck_high_step(
                    touched_raw,
                    stuck_latched=self._stuck_latched,
                    touch_high_since_mono=self._touch_high_since_mono,
                    stuck_clear_hits=self._stuck_clear_hits,
                    now=now,
                    stuck_after_sec=self._stuck_after_sec,
                    stuck_clear_reads=self._stuck_clear_reads,
                )
            )
            if newly_stuck:
                snap = self._touch.touch_snapshot()
                log.warning(
                    "AT42QT2120 line stuck HIGH for %.1fs (STATUS=0x%02X mask=0x%03X) — "
                    "touch reactions suppressed until the pad idles clear. "
                    "Insulate the electrode back from metal/chassis.",
                    self._stuck_after_sec,
                    int(snap["status"]),
                    int(snap["mask"]),
                )

            self._baseline_ready, self._baseline_clear_hits = touch_baseline_ready_step(
                touched,
                baseline_ready=self._baseline_ready,
                baseline_clear_reads=self._baseline_clear_reads,
                consecutive_clear=self._baseline_clear_hits,
            )
            if not self._baseline_ready:
                self._hits = 0
                self._prev_touched = touched
                time.sleep(self._poll_sec)
                continue

            if now < self._blind_until_mono:
                if not touched:
                    self._armed, self._release_hits = touch_release_rearm_step(
                        touched,
                        armed=self._armed,
                        release_reads=self._release_reads,
                        consecutive_clear=self._release_hits,
                    )
                else:
                    self._release_hits = 0
                self._hits = 0
                self._prev_touched = touched
                time.sleep(self._poll_sec)
                continue

            if not self._armed:
                self._armed, self._release_hits = touch_release_rearm_step(
                    touched,
                    armed=False,
                    release_reads=self._release_reads,
                    consecutive_clear=self._release_hits,
                )
                self._hits = 0
                self._prev_touched = touched
                time.sleep(self._poll_sec)
                continue

            if not touched:
                self._hits = 0
                self._prev_touched = False
                time.sleep(self._poll_sec)
                continue

            if (now - self._last_fire_mono) < self._cooldown_sec:
                self._prev_touched = touched
                time.sleep(self._poll_sec)
                continue

            fire, self._hits = touch_rising_edge_debounce_step(
                touched,
                self._prev_touched,
                debounce_reads=self._debounce_reads,
                consecutive_hits=self._hits,
            )
            self._prev_touched = touched

            if fire:
                self._armed = False
                self._release_hits = 0
                self._hits = 0
                try:
                    self._svc.run_touch_reaction()
                except Exception:
                    log.exception("Touch reaction failed")
                self._last_fire_mono = time.monotonic()
                if self._blind_sec > 0:
                    self._blind_until_mono = self._last_fire_mono + self._blind_sec

            time.sleep(self._poll_sec)
