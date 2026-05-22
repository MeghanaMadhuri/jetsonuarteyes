"""Persist low-battery latch across Nina UI restarts (``~/.cache/sirena/battery_latch.json``)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("nina.sensors.battery_latch_store")

_STORE_VERSION = 1
_store_lock = threading.Lock()


@dataclass
class BatteryLatchPersisted:
    """On-disk latch bookkeeping for restarts and repeat-warning anchors."""

    version: int = _STORE_VERSION
    latched: bool = False
    latched_at_unix: float = 0.0
    pack_v_at_latch: float = 0.0
    last_announced_v: Optional[float] = None
    pack_min_v: Optional[float] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "BatteryLatchPersisted":
        last_ann = data.get("last_announced_v")
        pack_min = data.get("pack_min_v")
        return cls(
            version=int(data.get("version", _STORE_VERSION)),
            latched=bool(data.get("latched", False)),
            latched_at_unix=float(data.get("latched_at_unix", 0.0)),
            pack_v_at_latch=float(data.get("pack_v_at_latch", 0.0)),
            last_announced_v=float(last_ann) if last_ann is not None else None,
            pack_min_v=float(pack_min) if pack_min is not None else None,
        )


def _default_state_path() -> Path:
    candidate = (os.environ.get("NINA_BATTERY_LATCH_STATE_PATH") or "").strip()
    if candidate:
        return Path(candidate)
    home = Path.home()
    for p in (Path("/var/lib/nina"), home / ".cache" / "sirena"):
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.access(p, os.W_OK):
                return p / "battery_latch.json"
        except OSError:
            continue
    return home / ".cache" / "sirena" / "battery_latch.json"


def _persist_enabled() -> bool:
    raw = (os.environ.get("NINA_BATTERY_LATCH_PERSIST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "n")


def load_battery_latch_state(path: Optional[Path] = None) -> Optional[BatteryLatchPersisted]:
    """Return persisted latch state, or ``None`` if missing / disabled / corrupt."""
    if not _persist_enabled():
        return None
    p = path or _default_state_path()
    with _store_lock:
        try:
            raw = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.debug("Battery latch state read failed: %s", exc)
            return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Battery latch state corrupt — ignoring %s", p)
        return None
    if not isinstance(data, dict):
        return None
    state = BatteryLatchPersisted.from_json(data)
    if not state.latched:
        return None
    return state


def save_battery_latch_state(
    state: BatteryLatchPersisted,
    *,
    path: Optional[Path] = None,
) -> None:
    if not _persist_enabled():
        return
    p = path or _default_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("Battery latch state directory not writable: %s", exc)
        return
    payload = json.dumps(state.to_json(), indent=2) + "\n"
    with _store_lock:
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
        except OSError as exc:
            log.warning("Battery latch state write failed: %s", exc)


def clear_battery_latch_state(*, path: Optional[Path] = None) -> None:
    if not _persist_enabled():
        return
    p = path or _default_state_path()
    with _store_lock:
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Battery latch state unlink failed: %s", exc)


def snapshot_latched(
    *,
    pack_v: float,
    last_announced_v: Optional[float],
    pack_min_v: Optional[float],
) -> None:
    """Persist an active low-battery latch."""
    save_battery_latch_state(
        BatteryLatchPersisted(
            latched=True,
            latched_at_unix=time.time(),
            pack_v_at_latch=float(pack_v),
            last_announced_v=last_announced_v,
            pack_min_v=pack_min_v,
        )
    )
