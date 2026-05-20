"""Aggregate health rows for the Android Health screen (NinaService + Wi‑Fi)."""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List

from nina.jetson_net.config import LinkDaemonConfig
from nina.jetson_net.state import LinkCoordinator
from sirena_ui.workers.health_collector import collect
from sirena_ui.workers.nina_service import NinaService

_ROW_OK = "ok"
_ROW_WARN = "warn"
_ROW_ERR = "error"
_ROW_PENDING = "pending"


def safe_map_filename(name: str) -> str:
    raw = os.path.basename((name or "").strip())
    if not raw or ".." in raw:
        return "nina_map.pgm"
    if not re.match(r"^[\w.\-]+$", raw):
        return "nina_map.pgm"
    lower = raw.lower()
    if not lower.endswith((".pgm", ".pnm")):
        raw = raw + ".pgm"
    return raw


def _row(key: str, label: str, detail: str, status: str) -> Dict[str, str]:
    return {
        "key": key,
        "label": label,
        "detail": detail[:2000],
        "status": status,
    }


def _cpu_line() -> str:
    try:
        with open("/proc/loadavg", encoding="utf-8") as fh:
            parts = fh.read().split()
        if len(parts) >= 3:
            return f"load {parts[0]} {parts[1]} {parts[2]}"
    except OSError:
        pass
    return "n/a"


def _append_row(rows: List[Dict[str, str]], seen: set[str], key: str, label: str, detail: str, status: str) -> None:
    """Append a row once per key (duplicate keys crash Android LazyColumn)."""
    if key in seen:
        return
    seen.add(key)
    rows.append(_row(key, label, detail, status))


def build_robot_health(
    service: NinaService,
    cfg: LinkDaemonConfig,
    coordinator: LinkCoordinator,
) -> Dict[str, Any]:
    rows: List[Dict[str, str]] = []
    seen: set[str] = set()

    try:
        role = coordinator.effective_wifi_role()
        _append_row(
            rows,
            seen,
            "wifi",
            "Wi-Fi",
            f"role={role}",
            _ROW_OK if role != "unknown" else _ROW_WARN,
        )
    except Exception as exc:  # noqa: BLE001
        _append_row(rows, seen, "wifi", "Wi-Fi", str(exc)[:400], _ROW_WARN)

    last_err = (coordinator.ps.last_error or "").strip()
    if last_err:
        _append_row(rows, seen, "daemon", "Gateway (last error)", last_err[:500], _ROW_WARN)

    # NinaService health rows (Dynamixel, vision, slam, …) — same as Qt Health screen.
    try:
        for hr in collect(service):
            _append_row(rows, seen, hr.key, hr.label, hr.detail, hr.status)
    except Exception as exc:  # noqa: BLE001
        _append_row(rows, seen, "sirena", "Sirena health", str(exc)[:500], _ROW_ERR)

    try:
        du = shutil.disk_usage("/")
        free_gb = du.free / (1024**3)
        total_gb = du.total / (1024**3)
        warn = free_gb < max(1.0, total_gb * 0.15)
        _append_row(
            rows,
            seen,
            "disk",
            "Disk",
            f"{free_gb:.1f} GB free of {total_gb:.0f} GB",
            _ROW_WARN if warn else _ROW_OK,
        )
    except Exception as exc:  # noqa: BLE001
        _append_row(rows, seen, "disk", "Disk", str(exc)[:200], _ROW_WARN)

    _append_row(rows, seen, "cpu", "CPU (load avg)", _cpu_line(), _ROW_OK)

    return {
        "ok": True,
        "rows": rows,
        "source": "sirena_ui android_gateway + jetson_net",
    }
