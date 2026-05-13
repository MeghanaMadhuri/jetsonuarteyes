"""Optional boot-time hotspot (same behaviour as legacy ``nina.link_daemon.main``)."""

from __future__ import annotations

import logging
import os
import threading
import time

from nina.jetson_net.config import LinkDaemonConfig
from nina.jetson_net.nm import NMBackend, NMError
from nina.jetson_net.state import LinkCoordinator, UserMode

log = logging.getLogger("nina.jetson_net.wifi_boot")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on", "y"):
        return True
    if raw in ("0", "false", "no", "off", "n"):
        return False
    return default


def _spawn_boot_ap_retry(coordinator: LinkCoordinator) -> None:
    def run() -> None:
        cfg = coordinator.cfg
        # Fast first retries for transient NM races, then back off toward ~45s.
        backoff_steps = (3.0, 5.0, 8.0, 12.0, 18.0, 25.0, 35.0, 45.0)
        max_attempts = 45
        coordinator.nm.wifi_ready_timeout = min(
            45.0,
            float(cfg.wifi_ready_timeout_sec),
        )
        for attempt in range(1, max_attempts + 1):
            delay = backoff_steps[min(attempt - 1, len(backoff_steps) - 1)]
            time.sleep(delay)
            try:
                coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
                coordinator.ps.ap_started = True
                coordinator.store.save(coordinator.ps)
                coordinator.set_user_mode(UserMode.FORCE_AP)
                log.info(
                    "Boot AP background retry #%s succeeded SSID=%s",
                    attempt,
                    cfg.ap_ssid,
                )
                return
            except NMError as e:
                log.warning("Boot AP background retry #%s: %s", attempt, e)
        log.error("Boot AP background retries exhausted (%s attempts)", max_attempts)

    threading.Thread(target=run, name="nina-boot-ap-retry", daemon=True).start()


def maybe_boot_ap(coordinator: LinkCoordinator) -> None:
    """Boot Wi-Fi policy:
    - FORCE_AP or NINA_LINK_BOOT_AP=1 -> AP
    - Otherwise keep/restore STA (prefer previously connected profile)
    - Fall back to AP only when no STA can be established
    """
    forced_boot_ap = _env_bool("NINA_LINK_BOOT_AP", False)
    mode = coordinator.user_mode_enum()
    if coordinator.nm.mock:
        try:
            coordinator.nm.start_hotspot(
                coordinator.cfg.ap_ssid,
                coordinator.cfg.ap_password,
            )
            coordinator.ps.ap_started = True
            coordinator.store.save(coordinator.ps)
        except NMError as e:
            log.warning("Mock boot AP: %s", e)
        return

    cfg: LinkDaemonConfig = coordinator.cfg
    if cfg.disable_wifi_autoconnect:
        try:
            coordinator.nm.disable_autoconnect_all_saved_wifi()
        except Exception as e:
            log.warning("disable_autoconnect_all_saved_wifi: %s", e)

    if mode == UserMode.FORCE_AP or forced_boot_ap:
        log.info("Boot network policy: AP (mode=%s forced_boot_ap=%s)", mode.value, forced_boot_ap)
    else:
        # If NM already rejoined infrastructure, keep it.
        try:
            role = coordinator.effective_wifi_role()
            if role == "sta":
                log.info("Boot network policy: STA already active, skip AP")
                try:
                    coordinator.sync_last_sta_from_active_connection()
                except Exception:
                    log.exception("sync_last_sta after boot STA already active")
                return
        except Exception:
            pass

        # Prefer the exact profile used before reboot when available.
        preferred = (coordinator.ps.last_sta_profile_id or "").strip()
        if preferred:
            try:
                coordinator.nm.activate_connection(preferred)
                log.info("Boot STA restored from last profile: %s", preferred)
                return
            except NMError as e:
                log.warning("Boot STA restore failed for %s: %s", preferred, e)

        # Best-effort fallback for FORCE_STA: try first saved profile.
        if mode == UserMode.FORCE_STA:
            try:
                saved = coordinator.refresh_saved_networks()
                if saved:
                    coordinator.nm.activate_connection(saved[0].uuid)
                    coordinator.remember_last_sta_profile(saved[0].uuid)
                    log.info("Boot STA activated via saved profile: %s", saved[0].ssid)
                    return
            except NMError as e:
                log.warning("Boot FORCE_STA profile activation failed: %s", e)

    try:
        coordinator.nm.start_hotspot(cfg.ap_ssid, cfg.ap_password)
        coordinator.ps.ap_started = True
        coordinator.store.save(coordinator.ps)
        log.info("Boot AP started SSID=%s", cfg.ap_ssid)
    except NMError as e:
        coordinator.set_error(str(e))
        detail = (e.details or "").strip()
        if detail:
            log.error("Boot AP failed: %s — %s", e, detail)
        else:
            log.error("Boot AP failed: %s", e)
        _spawn_boot_ap_retry(coordinator)
