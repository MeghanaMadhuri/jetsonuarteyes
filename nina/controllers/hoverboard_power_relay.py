"""Optional Jetson GPIO line to cut/restore hoverboard DC power via a relay.

Driven from ``HoverboardAxisDrive`` when ``HoverboardAxisSettings`` carries a
non-``None`` ``power_relay_bcm``. Uses lazy ``Jetson.GPIO`` import so dev
machines without the Jetson stack can still import Nina modules.

Wiring and env semantics: see ``docs/HOVERBOARD_POWER_RELAY.md``.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger("nina.hoverboard_power_relay")


def _relay_line_label(bcm: int) -> str:
    """Human label for logs (Nina default relay IN on 40-pin header pin 37)."""
    if int(bcm) == 26:
        return "Jetson 40-pin header pin 37"
    return f"Jetson GPIO BCM {int(bcm)}"

# One relay instance per process so kiosk boot-prime and HoverboardAxisDrive share GPIO.
_relay_singleton: Optional["HoverboardPowerRelay"] = None
_relay_singleton_sig: Optional[tuple[int, int, int]] = None


def reset_power_relay_singleton_for_tests() -> None:
    """Clear the process-wide relay handle (unit tests only)."""
    global _relay_singleton, _relay_singleton_sig
    _relay_singleton = None
    _relay_singleton_sig = None


class HoverboardPowerRelay:
    """Single BCM output: *power_on_level* means hoverboard pack is energised."""

    def __init__(
        self,
        bcm: int,
        *,
        power_on_level: int,
        status_led_bcm: Optional[int] = None,
    ) -> None:
        self._bcm = int(bcm)
        self._power_on_level = 1 if int(power_on_level) else 0
        self._power_cut_level = 1 - self._power_on_level
        self._lock = threading.Lock()
        self._gpio = None  # lazy module
        self._configured = False
        self._disabled = False
        # Last value sent to GPIO.output (for edge-only INFO logs).
        self._last_out: Optional[int] = None
        sb = int(status_led_bcm) if status_led_bcm is not None else 0
        if sb > 0 and sb == self._bcm:
            log.warning(
                "Hoverboard power relay: status LED BCM same as relay IN (%s) — "
                "ignoring status LED (use a different pin for NINA_HOVER_RELAY_STATUS_LED_BCM)",
                self._bcm,
            )
            sb = 0
        self._status_led_bcm: Optional[int] = sb if sb > 0 else None
        self._status_led_last: Optional[int] = None

    def _setup_once(self) -> bool:
        if self._disabled:
            return False
        with self._lock:
            if self._configured:
                return True
            try:
                import Jetson.GPIO as GPIO  # type: ignore[import-not-found]
            except ImportError as exc:
                log.warning(
                    "Hoverboard power relay: Jetson.GPIO not available (%s) — relay disabled",
                    exc,
                )
                self._disabled = True
                return False
            except Exception as exc:
                log.warning(
                    "Hoverboard power relay: import failed (%s) — relay disabled",
                    exc,
                )
                self._disabled = True
                return False
            self._gpio = GPIO
            try:
                GPIO.setmode(GPIO.BCM)
            except RuntimeError:
                # Already set in this process (e.g. navigation GPIO backend).
                pass
            try:
                # Default the pin to *pack cut* as soon as it is configured so a
                # reboot never leaves the hoverboard energised before Python logic runs.
                GPIO.setup(
                    self._bcm,
                    GPIO.OUT,
                    initial=self._power_cut_level,
                )
            except Exception as exc:
                log.warning(
                    "Hoverboard power relay: setup %s failed (%s) — relay disabled",
                    _relay_line_label(self._bcm),
                    exc,
                )
                self._disabled = True
                self._gpio = None
                return False
            if self._status_led_bcm is not None:
                try:
                    GPIO.setup(
                        self._status_led_bcm,
                        GPIO.OUT,
                        initial=GPIO.LOW,
                    )
                except Exception as exc:
                    log.warning(
                        "Hoverboard pack status LED: setup BCM %s failed (%s) — LED disabled",
                        self._status_led_bcm,
                        exc,
                    )
                    self._status_led_bcm = None
            self._configured = True
            self._last_out = self._power_cut_level
            self._write_pack_status_led(False)
            log.info(
                "Hoverboard power relay: %s power_on_level=%s (cut=%s)",
                _relay_line_label(self._bcm),
                self._power_on_level,
                self._power_cut_level,
            )
            if self._status_led_bcm is not None:
                log.info(
                    "Hoverboard pack status LED: BCM %s (HIGH=pack allowed, LOW=cut)",
                    self._status_led_bcm,
                )
            return True

    def _write_pack_status_led(self, pack_allowed: bool) -> None:
        """Optional second GPIO: HIGH when pack may be energised, LOW when cut."""
        led = self._status_led_bcm
        if led is None or not self._configured or self._gpio is None:
            return
        want = self._gpio.HIGH if pack_allowed else self._gpio.LOW
        try:
            self._gpio.output(led, want)
        except Exception:
            log.exception("Hoverboard pack status LED: output BCM %s failed", led)
            return
        if self._status_led_last != want:
            self._status_led_last = want
            log.info(
                "Hoverboard pack status LED: BCM %s GPIO=%s (%s)",
                led,
                1 if pack_allowed else 0,
                "pack allowed" if pack_allowed else "pack cut",
            )

    def set_power_allowed(self) -> None:
        """GPIO level that corresponds to hoverboard pack energised."""
        if not self._setup_once():
            return
        assert self._gpio is not None
        try:
            self._gpio.output(self._bcm, self._power_on_level)
            if self._last_out != self._power_on_level:
                self._last_out = self._power_on_level
                log.info(
                    "Hoverboard power relay: %s GPIO=%s (pack energised; brake OFF path)",
                    _relay_line_label(self._bcm),
                    self._power_on_level,
                )
            self._write_pack_status_led(True)
        except Exception:
            log.exception("Hoverboard power relay: set_power_allowed failed")

    def set_power_cut(self) -> None:
        """GPIO level that corresponds to hoverboard pack de-energised (brake on)."""
        if not self._setup_once():
            return
        assert self._gpio is not None
        try:
            self._gpio.output(self._bcm, self._power_cut_level)
            if self._last_out != self._power_cut_level:
                self._last_out = self._power_cut_level
                log.info(
                    "Hoverboard power relay: %s GPIO=%s (pack cut; brake ON path)",
                    _relay_line_label(self._bcm),
                    self._power_cut_level,
                )
            self._write_pack_status_led(False)
        except Exception:
            log.exception("Hoverboard power relay: set_power_cut failed")

    def apply_shutdown_policy(self, *, allow_power: bool) -> None:
        """Last write before process exit; does not call ``GPIO.cleanup()`` globally."""
        if self._disabled or not self._configured:
            return
        if allow_power:
            self.set_power_allowed()
        else:
            self.set_power_cut()


def get_power_relay(
    bcm: Optional[int],
    *,
    power_on_level: int,
    status_led_bcm: Optional[int] = None,
) -> Optional[HoverboardPowerRelay]:
    """Return a process-wide relay handle for *bcm* (or None if disabled)."""
    global _relay_singleton, _relay_singleton_sig
    if bcm is None or int(bcm) <= 0:
        return None
    bcm_i = int(bcm)
    pol = 1 if int(power_on_level) else 0
    status_i = 0
    if status_led_bcm is not None:
        try:
            s = int(status_led_bcm)
        except (TypeError, ValueError):
            s = 0
        if s > 0:
            if s == bcm_i:
                log.warning(
                    "Hoverboard power relay: status LED BCM equals relay BCM %s — ignoring",
                    bcm_i,
                )
            else:
                status_i = s
    sig = (bcm_i, pol, status_i)
    if _relay_singleton is not None:
        if _relay_singleton_sig == sig:
            return _relay_singleton
        log.warning(
            "Hoverboard power relay: ignoring second configuration %s (already using %s)",
            sig,
            _relay_singleton_sig,
        )
        return _relay_singleton
    _relay_singleton = HoverboardPowerRelay(
        bcm_i,
        power_on_level=pol,
        status_led_bcm=status_i if status_i else None,
    )
    _relay_singleton_sig = sig
    return _relay_singleton


def build_power_relay(
    bcm: Optional[int],
    *,
    power_on_level: int,
    status_led_bcm: Optional[int] = None,
) -> Optional[HoverboardPowerRelay]:
    """Alias of :func:`get_power_relay` (same shared instance for a given BCM)."""
    return get_power_relay(
        bcm,
        power_on_level=power_on_level,
        status_led_bcm=status_led_bcm,
    )


def prime_hoverboard_relay_cut_at_boot(axis_cfg: object) -> None:
    """Energise GPIO as OUTPUT in *cut* state before the main window appears.

    Call from ``NinaService`` construction so the hoverboard pack is off at kiosk
    startup; ``release_brake`` / ``initialize`` paths then match operator expectations.
    """
    bcm = getattr(axis_cfg, "power_relay_bcm", None)
    pol = int(getattr(axis_cfg, "power_relay_power_on_level", 1) or 0)
    status = getattr(axis_cfg, "power_relay_status_led_bcm", None)
    r = get_power_relay(bcm, power_on_level=pol, status_led_bcm=status)
    if r is None:
        return
    r.set_power_cut()
