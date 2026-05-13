"""Host (Jetson) power control from nina-link / Sirena UI (optional, requires sudoers).

Two operator actions are exposed: ``queue_poweroff()`` and
``queue_reboot()``. Both schedule the OS-level command in a daemon
thread so the HTTP / Qt caller can return immediately - by the time
the request handler unwinds, ``systemd`` is already in the middle of
bringing services down and the UI gets a final ``ok`` response before
the panel goes dark.

Both helpers try a small, ordered list of binaries (`systemctl`,
`/sbin/poweroff` / `/sbin/reboot`, `shutdown -h now` / `shutdown -r
now`) so the same code path works on Jetson images that ship with
either path layout (JetPack 5 uses `/sbin/poweroff`, JetPack 6 prefers
`systemctl poweroff` via systemd-shim).

For passwordless execution from the kiosk user, add this to
``/etc/sudoers.d/nina-link`` (replace ``nina`` with the actual user
running the GUI / nina-link)::

    nina ALL=(ALL) NOPASSWD: /usr/bin/systemctl poweroff, \\
        /usr/bin/systemctl reboot, \\
        /sbin/poweroff, /usr/sbin/poweroff, \\
        /sbin/reboot, /usr/sbin/reboot, \\
        /sbin/shutdown

Without the sudoers entry the GUI button will surface a clear
``operation not permitted`` log line; the polkit-friendly
``loginctl`` paths are not used because the kiosk user is typically
not on the active seat under a systemd-less LXDE autostart.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import Any, Dict, Iterable, Sequence, Tuple

log = logging.getLogger("nina.jetson_net.host_control")


# Each entry is a candidate argv. We try them in order until one
# returns 0 (or the process is gone before we can check). ``shutdown``
# variants are last because they print a wall message that takes a
# second to settle - the direct binaries cut the dark-screen latency.
_POWEROFF_CANDIDATES: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "poweroff"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "poweroff"),
    ("/usr/bin/sudo", "-n", "/sbin/poweroff"),
    ("/usr/bin/sudo", "-n", "/usr/sbin/poweroff"),
    ("/usr/bin/sudo", "-n", "/sbin/shutdown", "-h", "now"),
)

_REBOOT_CANDIDATES: Tuple[Tuple[str, ...], ...] = (
    ("/usr/bin/sudo", "-n", "/usr/bin/systemctl", "reboot"),
    ("/usr/bin/sudo", "-n", "/bin/systemctl", "reboot"),
    ("/usr/bin/sudo", "-n", "/sbin/reboot"),
    ("/usr/bin/sudo", "-n", "/usr/sbin/reboot"),
    ("/usr/bin/sudo", "-n", "/sbin/shutdown", "-r", "now"),
)


def _run_first_success(
    candidates: Iterable[Sequence[str]],
    action: str,
) -> None:
    """Try each candidate argv in order; log+return on first 0 exit."""
    last_err = ""
    for cmd in candidates:
        try:
            r = subprocess.run(
                list(cmd), timeout=5, capture_output=True, text=True
            )
            if r.returncode == 0:
                log.info("%s: %s", action, " ".join(cmd))
                return
            last_err = (r.stderr or r.stdout or "").strip()
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
    log.error(
        "%s: every candidate failed. Configure passwordless sudo "
        "for systemctl/poweroff/reboot. Last error: %s",
        action,
        last_err or "(no output)",
    )


def queue_poweroff() -> Dict[str, Any]:
    """Request OS poweroff in a background thread (caller returns immediately)."""

    def run() -> None:
        _run_first_success(_POWEROFF_CANDIDATES, "poweroff")

    threading.Thread(target=run, daemon=True, name="nina-poweroff").start()
    return {
        "ok": True,
        "queued": True,
        "action": "poweroff",
        "message": "Poweroff requested. Host may go down in a few seconds.",
    }


def queue_reboot() -> Dict[str, Any]:
    """Request OS reboot in a background thread (caller returns immediately)."""

    def run() -> None:
        _run_first_success(_REBOOT_CANDIDATES, "reboot")

    threading.Thread(target=run, daemon=True, name="nina-reboot").start()
    return {
        "ok": True,
        "queued": True,
        "action": "reboot",
        "message": "Reboot requested. Host may restart in a few seconds.",
    }
