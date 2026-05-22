"""HTTP bridge for saved drive sequences (``nina/movements``)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from nina.movements.model import movement_from_dict, movement_to_dict
from sirena_ui.workers.nina_service import NinaService

log = logging.getLogger("sirena_ui.android_gateway.movements_http")

_last_run_lock = threading.Lock()
_last_run: Dict[str, Any] = {
    "movement_id": None,
    "status": "idle",  # idle | running | ok | failed
    "error": None,
}


def wire_movement_run_signals(service: NinaService) -> None:
    """Record saved-sequence completion for tablet polling (once per process)."""
    if getattr(service, "_movement_http_signals_wired", False):
        return
    dc = service.drive

    def _on_ok(movement_id: str) -> None:
        with _last_run_lock:
            _last_run.update(
                movement_id=movement_id,
                status="ok",
                error=None,
            )

    def _on_fail(movement_id: str, message: str) -> None:
        with _last_run_lock:
            _last_run.update(
                movement_id=movement_id,
                status="failed",
                error=str(message or "Movement failed"),
            )

    dc.saved_movement_finished.connect(_on_ok)
    dc.saved_movement_failed.connect(_on_fail)
    service._movement_http_signals_wired = True  # type: ignore[attr-defined]


def movements_list(service: NinaService) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = [
        movement_to_dict(mv) for mv in service.movement_store.load_all()
    ]
    return {"movements": items}


def movement_get(service: NinaService, movement_id: str) -> Dict[str, Any]:
    mv = service.movement_store.get(movement_id)
    if mv is None:
        return {"ok": False, "error": "Movement not found"}
    return {"ok": True, "movement": movement_to_dict(mv)}


def movement_upsert(service: NinaService, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        mv = movement_from_dict(body)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    service.movement_store.upsert(mv)
    return {"ok": True, "movement": movement_to_dict(mv)}


def movement_delete(service: NinaService, movement_id: str) -> Dict[str, Any]:
    if service.movement_store.get(movement_id) is None:
        return {"ok": False, "error": "Movement not found"}
    service.movement_store.delete(movement_id)
    return {"ok": True}


def movement_run(service: NinaService, movement_id: str) -> Dict[str, Any]:
    mv = service.movement_store.get(movement_id)
    if mv is None:
        return {"ok": False, "error": "Movement not found"}
    with _last_run_lock:
        if _last_run.get("status") == "running":
            return {
                "ok": False,
                "error": "Another movement is already running",
            }
        _last_run.update(
            movement_id=movement_id,
            status="running",
            error=None,
        )
    service.drive.ensure_hardware()
    service.drive.run_saved_movement(mv)
    return {"ok": True, "queued": True, "movement_id": movement_id}


def movement_run_status() -> Dict[str, Any]:
    with _last_run_lock:
        return dict(_last_run)
