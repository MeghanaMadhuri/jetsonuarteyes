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

# One relay instance per process so kiosk boot-prime and HoverboardAxisDrive share GPIO.
_relay_singleton: Optional["HoverboardPowerRelay"] = None
_relay_singleton_sig: Optional[tuple[int, int]] = None


class HoverboardPowerRelay:
    """Single BCM output: *power_on_level* means hoverboard pack is energised."""

    def __init__(self, bcm: int, *, power_on_level: int) -> None:
        self._bcm = int(bcm)
        self._power_on_level = 1 if int(power_on_level) else 0
        self._power_cut_level = 1 - self._power_on_level
        self._lock = threading.Lock()
        self._gpio = None  # lazy module
        self._configured = False
        self._disabled = False

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
                    "Hoverboard power relay: setup BCM %s failed (%s) — relay disabled",
                    self._bcm,
                    exc,
                )
                self._disabled = True
                self._gpio = None
                return False
            self._configured = True
            log.info(
                "Hoverboard power relay: BCM %s power_on_level=%s (cut=%s)",
                self._bcm,
                self._power_on_level,
                self._power_cut_level,
            )
            return True

    def set_power_allowed(self) -> None:
        """GPIO level that corresponds to hoverboard pack energised."""
        if not self._setup_once():
            return
        assert self._gpio is not None
        try:
            self._gpio.output(self._bcm, self._power_on_level)
        except Exception:
            log.exception("Hoverboard power relay: set_power_allowed failed")

    def set_power_cut(self) -> None:
        """GPIO level that corresponds to hoverboard pack de-energised (brake on)."""
        if not self._setup_once():
            return
        assert self._gpio is not None
        try:
            self._gpio.output(self._bcm, self._power_cut_level)
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
) -> Optional[HoverboardPowerRelay]:
    """Return a process-wide relay handle for *bcm* (or None if disabled)."""
    global _relay_singleton, _relay_singleton_sig
    if bcm is None or int(bcm) <= 0:
        return None
    bcm_i = int(bcm)
    pol = 1 if int(power_on_level) else 0
    sig = (bcm_i, pol)
    if _relay_singleton is not None:
        if _relay_singleton_sig == sig:
            return _relay_singleton
        log.warning(
            "Hoverboard power relay: ignoring second BCM %s (already using BCM %s)",
            bcm_i,
            _relay_singleton_sig[0] if _relay_singleton_sig else "?",
        )
        return _relay_singleton
    _relay_singleton = HoverboardPowerRelay(bcm_i, power_on_level=pol)
    _relay_singleton_sig = sig
    return _relay_singleton


def build_power_relay(
    bcm: Optional[int],
    *,
    power_on_level: int,
) -> Optional[HoverboardPowerRelay]:
    """Alias of :func:`get_power_relay` (same shared instance for a given BCM)."""
    return get_power_relay(bcm, power_on_level=power_on_level)


def prime_hoverboard_relay_cut_at_boot(axis_cfg: object) -> None:
    """Energise GPIO as OUTPUT in *cut* state before the main window appears.

    Call from ``NinaService`` construction so the hoverboard pack is off at kiosk
    startup; ``release_brake`` / ``initialize`` paths then match operator expectations.
    """
    bcm = getattr(axis_cfg, "power_relay_bcm", None)
    pol = int(getattr(axis_cfg, "power_relay_power_on_level", 1) or 0)
    r = get_power_relay(bcm, power_on_level=pol)
    if r is None:
        return
    r.set_power_cut()
