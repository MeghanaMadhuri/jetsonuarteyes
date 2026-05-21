"""Persist saved movements as JSON next to the action manifest."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from nina.movements.model import (
    SavedMovement,
    movement_from_dict,
    movement_to_dict,
)

log = logging.getLogger("nina.movements.store")


def default_movements_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[2]
    return root / "nina" / "actions" / "saved_movements.json"


class MovementStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> List[SavedMovement]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s: %s", self._path, exc)
            return []
        items = data.get("movements") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out: List[SavedMovement] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                out.append(movement_from_dict(raw))
            except ValueError as exc:
                log.warning("Skipping bad movement entry: %s", exc)
        return sorted(out, key=lambda m: m.name.lower())

    def save_all(self, movements: List[SavedMovement]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"movements": [movement_to_dict(m) for m in movements]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def upsert(self, movement: SavedMovement) -> None:
        items = self.load_all()
        by_id: Dict[str, SavedMovement] = {m.movement_id: m for m in items}
        by_id[movement.movement_id] = movement
        self.save_all(list(by_id.values()))

    def delete(self, movement_id: str) -> bool:
        items = self.load_all()
        kept = [m for m in items if m.movement_id != movement_id]
        if len(kept) == len(items):
            return False
        self.save_all(kept)
        return True

    def get(self, movement_id: str) -> Optional[SavedMovement]:
        for m in self.load_all():
            if m.movement_id == movement_id:
                return m
        return None
