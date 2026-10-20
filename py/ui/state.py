"""Shared in-memory app state, guarded by a single lock."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "scanning":      False,
    "last_scan":     None,
    "current_path":  None,   # path being scanned right now
    "library":       [],
    "downloads":     [],
    "transcoded":    [],     # items found under the transcode output folder
    "status":        {},
    "scan_error":    None,
    "scan_info":     None,   # details of ongoing/last scan: started, finished, locations
    "transcode":     None,   # current/last transcode job (see transcode.py)
}


def all_items() -> List[Dict[str, Any]]:
    """Library + downloads + transcoded items. Caller must hold `_lock`."""
    return _state["library"] + _state["downloads"] + _state["transcoded"]


def resolve_item(item_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        for i in all_items():
            if i["id"] == item_id:
                return i
    return None


def require_item(item_id: str) -> Dict[str, Any]:
    """The item with this id, or a 404 naming the id when the current scan does not contain it."""
    item = resolve_item(item_id)
    if item is None:
        raise HTTPException(404, f"Item not found in the current scan: {item_id} — it was renamed, moved "
                                 "or deleted, or a rescan changed it. Refresh the page and try again.")
    return item


def forget_items(item_ids: List[str]) -> None:
    """Drop items from the in-memory lists (e.g. after a delete) until the next scan."""
    gone = set(item_ids)
    with _lock:
        for key in ("library", "downloads", "transcoded"):
            _state[key] = [i for i in _state[key] if i["id"] not in gone]
