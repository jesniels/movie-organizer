"""Duplicate detection and user-dismissed not-duplicate pairs."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .settings import NOT_DUP_PATH, _clean_title, log
from .state import _lock, _state


def _load_not_duplicates() -> List[List[str]]:
    try:
        if NOT_DUP_PATH.exists():
            data = json.loads(NOT_DUP_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [sorted(p) for p in data if isinstance(p, list) and len(p) == 2]
    except Exception as exc:
        log.warning("Could not load %s: %s", NOT_DUP_PATH.name, exc)
    return []


def _save_not_duplicates(pairs: List[List[str]]) -> None:
    try:
        NOT_DUP_PATH.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save %s: %s", NOT_DUP_PATH.name, exc)


def _dismissed_pairs() -> set:
    return {tuple(p) for p in _load_not_duplicates()}


def _update_not_duplicates_paths(old_base: str, new_base: str) -> None:
    """Rewrite stored pair ids after a move so dismissals follow the files."""
    pairs = _load_not_duplicates()
    changed = False
    old_prefix = old_base.rstrip("\\/")
    for pair in pairs:
        for idx, pid in enumerate(pair):
            if pid == old_base or pid.startswith(old_prefix + os.sep) or pid.startswith(old_prefix + "/"):
                pair[idx] = new_base + pid[len(old_base):]
                changed = True
    if changed:
        _save_not_duplicates([sorted(p) for p in pairs])


def _find_duplicates(items: List[Dict[str, Any]], dismissed: Optional[set] = None) -> List[Dict[str, Any]]:
    dismissed = dismissed or set()
    seen: Dict[str, List[Dict]] = {}
    for item in items:
        key = _clean_title(item["title"]).lower().strip()
        if not key:
            continue
        seen.setdefault(key, []).append({
            "id":       item["id"],
            "title":    item["title"],
            "location": item["location"],
            "path":     item["path"],
        })
    groups = []
    for k, v in seen.items():
        if len(v) < 2:
            continue
        # Keep only items that still have at least one non-dismissed pairing
        kept = [
            i for i in v
            if any(tuple(sorted((i["id"], o["id"]))) not in dismissed for o in v if o["id"] != i["id"])
        ]
        if len(kept) > 1:
            groups.append({"title": k, "items": kept})
    return groups


def recompute_duplicates() -> None:
    """Refresh duplicate lists in status from in-memory items (no rescan)."""
    dismissed = _dismissed_pairs()
    with _lock:
        items  = _state["library"] + _state["downloads"]
        movies = [i for i in items if i["type"] == "movie"]
        series = [i for i in items if i["type"] == "series"]
        if _state["status"]:
            _state["status"]["duplicate_movies"] = _find_duplicates(movies, dismissed)
            _state["status"]["duplicate_series"] = _find_duplicates(series, dismissed)


def mark_not_duplicate(ids: List[str]) -> None:
    if len(ids) != 2:
        raise HTTPException(400, "Exactly two item ids required")
    pairs = _load_not_duplicates()
    key = sorted(ids)
    if key not in pairs:
        pairs.append(key)
        _save_not_duplicates(pairs)
        log.info("Marked as not-duplicate: %s ↔ %s", key[0], key[1])
    recompute_duplicates()


def remove_not_duplicate_pairs(pairs_to_remove: List[List[str]]) -> Dict[str, Any]:
    existing  = _load_not_duplicates()
    to_remove = {tuple(sorted(p)) for p in pairs_to_remove if len(p) == 2}
    remaining = [p for p in existing if tuple(p) not in to_remove]
    removed   = len(existing) - len(remaining)
    if removed:
        _save_not_duplicates(remaining)
        recompute_duplicates()
        log.info("Removed %d not-duplicate registration(s)", removed)
    return {"ok": True, "removed": removed, "remaining": len(remaining)}
