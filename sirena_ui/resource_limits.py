"""Process open-file limits for the combined Qt UI + tablet gateway."""

from __future__ import annotations

import logging
import os
import resource
from typing import Tuple

log = logging.getLogger("sirena_ui.resource_limits")


def open_fd_count() -> int:
    """Best-effort count of open file descriptors for this process."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


def raise_nofile_limit() -> Tuple[int, int]:
    """Raise RLIMIT_NOFILE toward 8192 (or the kernel hard cap).

    Desktop launchers and ``ulimit -n`` in shell scripts often fail silently
    when the hard limit is still 1024. Doing this in Python guarantees the
    combined Sirena UI + port-8787 gateway process gets headroom before Qt,
    cameras, and MJPEG streams start.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = soft
    for want in (8192, 4096, 2048, 1024):
        if want <= hard:
            target = max(soft, want)
            break
    else:
        target = hard
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except OSError:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        except OSError:
            log.warning(
                "could not raise RLIMIT_NOFILE (soft=%s hard=%s)",
                soft,
                hard,
            )
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return soft, hard


def log_startup_limits() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    n = open_fd_count()
    log.info(
        "process limits: RLIMIT_NOFILE soft=%s hard=%s open_fds=%s",
        soft,
        hard,
        n if n >= 0 else "?",
    )


def fd_pressure_ratio() -> float:
    """Fraction of soft NOFILE in use, or 0.0 if unknown."""
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    n = open_fd_count()
    if soft <= 0 or n < 0:
        return 0.0
    return float(n) / float(soft)
