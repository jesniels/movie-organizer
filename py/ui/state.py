"""Shared in-memory app state, guarded by a single lock."""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "scanning":      False,
    "last_scan":     None,
    "current_path":  None,   # path being scanned right now
    "library":       [],
    "downloads":     [],
    "status":        {},
    "scan_error":    None,
    "scan_info":     None,   # details of ongoing/last scan: started, finished, locations
}


def resolve_item(item_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        for i in _state["library"] + _state["downloads"]:
            if i["id"] == item_id:
                return i
    return None
