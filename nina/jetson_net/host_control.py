"""Host (Jetson) power control from nina-link / Sirena UI (optional, requires sudoers).

Two operator actions are exposed: ``queue_poweroff()`` and
``queue_reboot()``. Both schedule the OS-level command in a daemon
thread so the HTTP / Qt caller can return immediately.

Passwordless execution for the kiosk user is installed by::

    sudo bash scripts/install-nina-host-power.sh [username]

That writes ``/etc/sudoers.d/nina-host-power`` with the exact command
paths tried below. The older ``/etc/sudoers.d/nina-link`` name in
early docs is obsolete — use ``install-nina-host-power.sh``.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Any, Dict, Iterable, Sequence, Tuple

log = logging.getLogger("nina.jetson_net.host_control")

_INSTALL_HINT = (
    "Passwordless sudo is not configured. On the Jetson run once:\n"
    "  sudo bash scripts/install-nina-host-power.sh\n"
    "(or pass your kiosk username, e.g. `sudo bash .../install-nina-host-power.sh nina`)"
)

# Sudo paths — keep in sync with scripts/install-nina-host-power.sh
_POWEROFF_SUDO: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "poweroff"),
    ("/usr/bin/sudo", "-n", "/sbin/poweroff"),
    ("/usr/bin/sudo", "-n", "/usr/sbin/poweroff"),
    ("/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"),
)

_REBOOT_SUDO: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "reboot"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "reboot"),
    ("/usr/bin/sudo", "-n", "/sbin/reboot"),
    ("/usr/bin/sudo", "-n", "/usr/sbin/reboot"),
    ("/usr/bin/sudo", "-n", "/sbin/shutdown", "-r", "now"),
)

# When already root (rare), skip sudo.
_POWEROFF_DIRECT: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/systemctl", "poweroff"),
    ("/bin/systemctl", "poweroff"),
    ("/sbin/poweroff"),
    ("/usr/sbin/poweroff"),
    ("/sbin/shutdown", "-h", "now"),
)

_REBOOT_DIRECT: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/systemctl", "reboot"),
    ("/bin/systemctl", "reboot"),
    ("/sbin/reboot"),
    ("/usr/sbin/reboot"),
    ("/sbin/shutdown", "-r", "now"),
)

# Non-destructive probes (first match wins).
_POWEROFF_PROBE: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff", "--dry-run"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "poweroff", "--dry-run"),
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "--version"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "--version"),
    ("/usr/bin/sudo", "-n", "/sbin/poweroff", "--help"),
)


def _candidates_for(action: str) -> Tuple[Tuple[str, ...], ...]:
    if action == "poweroff":
        direct = _POWEROFF_DIRECT if os.geteuid() == 0 else ()
        return direct + _POWEROFF_SUDO
    if action == "reboot":
        direct = _REBOOT_DIRECT if os.geteuid() == 0 else ()
        return direct + _REBOOT_SUDO
    raise ValueError(f"unknown action: {action}")


def _run_first_success(
    candidates: Iterable[Sequence[str]],
    action: str,
) -> Tuple[bool, str]:
    """Try each candidate argv in order; return (ok, last_error)."""
    last_err = ""
    for cmd in candidates:
        try:
            r = subprocess.run(
                list(cmd), timeout=8, capture_output=True, text=True
            )
            if r.returncode == 0:
                log.info("%s: %s", action, " ".join(cmd))
                return True, ""
            last_err = (r.stderr or r.stdout or "").strip()
            low = last_err.lower()
            if "password" in low or "not allowed" in low or "a password is required" in low:
                last_err = "sudo requires a password (NOPASSWD rule missing)"
            log.warning(
                "%s try %s exited rc=%s: %s",
                action,
                " ".join(cmd),
                r.returncode,
                last_err[:200],
            )
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - logged + tried next
            last_err = f"{type(exc).__name__}: {exc}"
            log.warning("%s try %s: %s", action, " ".join(cmd), exc)
    if not last_err:
        last_err = "no candidate command succeeded"
    log.error(
        "%s: every candidate failed. %s Last error: %s",
        action,
        _INSTALL_HINT.split("\n")[0],
        last_err,
    )
    return False, last_err


def probe_power_privilege() -> Dict[str, Any]:
    """Check whether shutdown/reboot is likely to work for the current user."""
    if os.name != "posix":
        return {
            "ok": False,
            "detail": "Host power control is only supported on Linux.",
            "install_hint": "",
        }
    if os.geteuid() == 0:
        return {
            "ok": True,
            "detail": "Running as root — poweroff/reboot should work.",
            "install_hint": "",
        }
    for cmd in _POWEROFF_PROBE:
        try:
            r = subprocess.run(
                list(cmd), timeout=5, capture_output=True, text=True
            )
            if r.returncode == 0:
                return {
                    "ok": True,
                    "detail": "Passwordless sudo for systemctl poweroff is configured.",
                    "install_hint": "",
                }
        except FileNotFoundError:
            continue
        except Exception as exc:
            log.debug("power probe %s: %s", " ".join(cmd), exc)
    # Fall back: any sudo poweroff path that fails only on dry-run unsupported
    ok, err = _run_first_success(
        (
            ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "--version"),
            ("/usr/bin/sudo", "-n", "/bin/systemctl", "--version"),
        ),
        "probe",
    )
    if ok:
        return {
            "ok": True,
            "detail": "Passwordless sudo for systemctl is available.",
            "install_hint": "",
        }
    return {
        "ok": False,
        "detail": err or "sudo -n systemctl is not permitted for this user.",
        "install_hint": _INSTALL_HINT,
    }


def queue_poweroff() -> Dict[str, Any]:
    """Request OS poweroff in a background thread."""
    return _queue_action("poweroff")


def queue_reboot() -> Dict[str, Any]:
    """Request OS reboot in a background thread."""
    return _queue_action("reboot")


def _queue_action(action: str) -> Dict[str, Any]:
    priv = probe_power_privilege()
    if not priv.get("ok"):
        return {
            "ok": False,
            "queued": False,
            "action": action,
            "message": str(priv.get("detail") or f"{action} not permitted"),
            "install_hint": str(priv.get("install_hint") or _INSTALL_HINT),
        }

    candidates = _candidates_for(action)

    def run() -> None:
        _run_first_success(candidates, action)

    threading.Thread(
        target=run, daemon=True, name=f"nina-{action}"
    ).start()
    verb = "Poweroff" if action == "poweroff" else "Reboot"
    return {
        "ok": True,
        "queued": True,
        "action": action,
        "message": f"{verb} requested. Host may go down in a few seconds.",
    }
