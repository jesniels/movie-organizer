"""Shared in-memory app state, guarded by a single lock."""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

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
