"""Leftover Folders: detection (during a scan and after an action), the in-memory list, and the delete (R-29).

A leftover folder lies inside an enabled Library or Download Location (never the location itself), has no
video anywhere below it and holds nothing or only files from the configured ``leftover:`` lists. It is never
a configured folder or one containing a configured folder, never inside a folder that produces an item, and
never contains a ``system_folders`` entry. Design: docs/organization/README.md (Leftover Folders).

Nothing here deletes anything on its own: ``delete_leftovers`` re-checks every folder on disk and then
removes exactly the listed files one by one and the folders with ``rmdir`` (never ``rmtree``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from .settings import (
    VIDEO_EXTS,
    enabled_paths,
    file_matches,
    folder_matches,
    fs_path,
    leftover_lists,
    load_config,
    log,
    transcoded_root,
)
from .state import _lock, _state

LABEL_EMPTY    = "empty"
LABEL_JELLYFIN = "Jellyfin data"
LABEL_SUBTITLE = "subtitle, no video"
LABEL_THUMBS   = "NAS thumbnails"
_LABEL_ORDER   = (LABEL_JELLYFIN, LABEL_SUBTITLE, LABEL_THUMBS)


class _NotLeftover(Exception):
    """Why a folder is not a leftover folder (the message completes "The folder …")."""


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _inside(path: str, folder: str) -> bool:
    """True when ``path`` lies strictly below ``folder``."""
    p, f = _key(path), _key(folder)
    return p != f and p.startswith(f.rstrip("\\/") + os.sep)


def configured_keys(config: Dict[str, Any]) -> FrozenSet[str]:
    """Normalized paths of every configured folder: locations, downloads and transcode output, enabled or not."""
    paths = config.get("locations", []) + config.get("downloads", []) + [transcoded_root(config)]
    return frozenset(_key(p) for p in paths if p and p.strip())


def _location_of(path: str, config: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(root, "library" | "download") of the deepest enabled location strictly containing ``path``, or None."""
    best: Optional[Tuple[str, str]] = None
    for loc_type, key in (("library", "locations"), ("download", "downloads")):
        for root in enabled_paths(config, key):
            if _inside(path, root) and (best is None or len(_key(root)) > len(_key(best[0]))):
                best = (root, loc_type)
    return best


def describe(files: List[str], lists: Dict[str, List[str]]) -> Tuple[str, List[str]]:
    """The label of a leftover folder's content and its hidden files (``ignored_files``), from ``files``
    relative to the folder. Pure — also used for cached entries."""
    labels: set = set()
    hidden: List[str] = []
    for f in files:
        parts = f.replace("/", os.sep).split(os.sep)
        dirs, name = parts[:-1], parts[-1]
        if any(folder_matches(d, lists["thumbnail_folders"]) for d in dirs):
            labels.add(LABEL_THUMBS)
        elif file_matches(name, lists["ignored_files"]):
            hidden.append(f)
        elif any(folder_matches(d, lists["jellyfin_folders"]) for d in dirs) or file_matches(name, lists["jellyfin_files"]):
            labels.add(LABEL_JELLYFIN)
        elif file_matches(name, lists["subtitle_files"]):
            labels.add(LABEL_SUBTITLE)
    label = " · ".join(lb for lb in _LABEL_ORDER if lb in labels) or LABEL_EMPTY
    return label, hidden


def _read_tree(path: str, lists: Dict[str, List[str]], configured: FrozenSet[str]) -> Tuple[List[str], List[str]]:
    """The files (relative to ``path``) and sub-folders (absolute, deepest first) of a leftover folder.

    Raises ``_NotLeftover`` at the first thing that makes it none: its own name is a system or NAS thumbnail
    folder, it is or contains a configured folder, it holds a video, a link, a system folder, or a file on no
    list (inside a ``thumbnail_folders`` folder any non-video file is allowed), or it cannot be read.
    """
    name = os.path.basename(os.path.normpath(path))
    if folder_matches(name, lists["system_folders"]):
        raise _NotLeftover("is a system folder")
    if folder_matches(name, lists["thumbnail_folders"]):
        raise _NotLeftover("is a NAS thumbnail folder (never listed on its own)")
    key = _key(path)
    if any(c == key or c.startswith(key.rstrip("\\/") + os.sep) for c in configured):
        raise _NotLeftover("is or contains a configured folder")
    allowed = lists["jellyfin_files"] + lists["subtitle_files"] + lists["ignored_files"]
    files: List[str] = []
    dirs: List[str] = []
    stack: List[Tuple[str, str, bool]] = [(path, "", False)]
    while stack:
        d, rel, in_thumbs = stack.pop()
        try:
            with os.scandir(fs_path(d)) as it:
                entries = list(it)
        except OSError as exc:
            raise _NotLeftover(f"cannot be read: {exc.strerror or exc}")
        for e in entries:
            r = os.path.join(rel, e.name) if rel else e.name
            if e.is_symlink() or e.is_junction():
                raise _NotLeftover(f"holds a link ({r})")
            if e.is_dir(follow_symlinks=False):
                if folder_matches(e.name, lists["system_folders"]):
                    raise _NotLeftover(f"holds a system folder ({r})")
                full = os.path.join(d, e.name)
                dirs.append(full)
                stack.append((full, r, in_thumbs or folder_matches(e.name, lists["thumbnail_folders"])))
            elif os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                raise _NotLeftover(f"holds a video ({r})")
            elif in_thumbs or file_matches(e.name, allowed):
                files.append(r)
            else:
                raise _NotLeftover(f"holds a file that is not on the leftover lists ({r})")
    return sorted(files, key=str.lower), sorted(dirs, key=lambda p: p.count(os.sep), reverse=True)


def classify_folder(path: str, lists: Dict[str, List[str]], configured: Iterable[str]) -> Optional[Dict[str, Any]]:
    """``{path, files, hidden, label}`` when the folder tree at ``path`` is a leftover folder, else None.

    Reads the disk. ``configured`` = normalized paths of all configured folders (``configured_keys``).
    Whether the folder lies inside a movie or series folder is not checked here: the scan only calls it
    where that cannot be the case, ``_check_on_disk`` checks it on disk.
    """
    try:
        files, _ = _read_tree(path, lists, frozenset(configured))
    except _NotLeftover:
        return None
    label, hidden = describe(files, lists)
    return {"path": path, "files": files, "hidden": hidden, "label": label}


def _produces_item(folder: str) -> bool:
    """True when the scanner treats ``folder`` as a movie or series folder that yields an item; a Bundle
    holding Loose Files next to movie folders is not one. Unreadable counts as True (safe side)."""
    from .scanner import _is_leaf_folder, _video_files   # late import: scanner imports this module
    try:
        os.scandir(fs_path(folder)).close()
    except OSError:
        return True
    return _is_leaf_folder(Path(folder)) and bool(_video_files(Path(folder)))


def _check_on_disk(path: str, config: Dict[str, Any],
                   lists: Dict[str, List[str]]) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str]]:
    """Every R-29 rule for one folder, on disk: (entry, sub-folders deepest first, None) or (None, [], reason).

    Inside an enabled Library/Download Location and not the location itself; not (inside or containing)
    another configured folder; no folder between the location root and it is a system / NAS thumbnail
    folder or produces an item (checked on disk, never from the cached items); and ``_read_tree``.
    """
    path = os.path.normpath(path)
    loc = _location_of(path, config)
    if not loc:
        return None, [], "is not inside an enabled Library or Download Location"
    root, loc_type = loc
    if not os.path.isdir(fs_path(path)):
        return None, [], "no longer exists"
    key = _key(path)
    configured = configured_keys(config)
    for c in configured:
        if _inside(c, root) and (key == c or key.startswith(c.rstrip("\\/") + os.sep)):
            return None, [], f"is or lies inside another configured folder ({c})"
    a = os.path.dirname(path)
    while _inside(a, root):
        name = os.path.basename(a)
        if folder_matches(name, lists["system_folders"]) or folder_matches(name, lists["thumbnail_folders"]):
            return None, [], f"lies inside a system or NAS thumbnail folder ({a})"
        if _produces_item(a):
            return None, [], f"lies inside a movie or series folder ({a})"
        a = os.path.dirname(a)
    try:
        files, dirs = _read_tree(path, lists, configured)
    except _NotLeftover as exc:
        return None, [], str(exc)
    label, hidden = describe(files, lists)
    entry = {"path": path, "root": root, "location": loc_type, "files": files, "hidden": hidden, "label": label}
    return entry, dirs, None


# ── Scan ──────────────────────────────────────────────────────────────────────
class LeftoverCollector:
    """Collects the leftover folders of one Library or Download Location while the scanner walks it."""

    def __init__(self, config: Dict[str, Any], root: str, loc_type: str) -> None:
        self.lists = leftover_lists(config)
        self.configured = configured_keys(config)
        self.root = root
        self.loc_type = loc_type
        self.found: List[Dict[str, Any]] = []

    def enters(self, name: str) -> bool:
        """False for a system or NAS thumbnail folder: nothing below it is checked."""
        return not (folder_matches(name, self.lists["system_folders"])
                    or folder_matches(name, self.lists["thumbnail_folders"]))

    def collect(self, folder: str) -> bool:
        """Check one folder; True (and kept) when it is a leftover folder."""
        entry = classify_folder(folder, self.lists, self.configured)
        if not entry:
            return False
        entry.update(root=self.root, location=self.loc_type)
        self.found.append(entry)
        return True


def leftover_to_cache(entry: Dict[str, Any], root: str) -> Dict[str, Any]:
    """Cache form: ``path`` relative to the location root, ``files`` relative to the folder."""
    return {"path": os.path.relpath(entry["path"], root), "files": entry["files"]}


def leftover_from_cache(raw: Dict[str, Any], root: str, loc_type: str,
                        lists: Dict[str, List[str]]) -> Dict[str, Any]:
    """In-memory form of a cached entry: absolute path; label and hidden files from the current lists."""
    path = raw.get("path") or ""
    path = path if os.path.isabs(path) else os.path.normpath(os.path.join(root, path))
    files = [f for f in raw.get("files") or [] if isinstance(f, str)]
    label, hidden = describe(files, lists)
    return {"path": path, "root": root, "location": loc_type, "files": files, "hidden": hidden, "label": label}


# ── In-memory list ────────────────────────────────────────────────────────────
def current_leftovers() -> List[Dict[str, Any]]:
    """The leftover folders known now (scan + after-action checks), sorted by path."""
    with _lock:
        out = [dict(e) for e in _state["leftovers"]]
    return sorted(out, key=lambda e: e["path"].lower())


def _remember(entries: List[Dict[str, Any]]) -> None:
    """Add fresh entries; a known entry that is, contains or lies inside one of them is replaced."""
    with _lock:
        keep = [e for e in _state["leftovers"]
                if not any(_key(e["path"]) == _key(n["path"]) or _inside(e["path"], n["path"])
                           or _inside(n["path"], e["path"]) for n in entries)]
        _state["leftovers"] = keep + entries


def _forget(paths: List[str]) -> None:
    """Drop entries that are, or lie inside, one of ``paths`` (deleted folders)."""
    with _lock:
        _state["leftovers"] = [e for e in _state["leftovers"]
                               if not any(_key(e["path"]) == _key(p) or _inside(e["path"], p) for p in paths)]


def check_after_action(changed_paths: Iterable[str], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The leftover folders an action left behind, added to the in-memory list (not to the cache).

    For each changed path (a moved, deleted or renamed file/folder) walks from its parent folder up to its
    location root and keeps the topmost folder that is a leftover on disk. Call after a real run only.
    """
    lists = leftover_lists(config)
    found: Dict[str, Dict[str, Any]] = {}
    for p in changed_paths:
        if not p:
            continue
        d = os.path.dirname(os.path.normpath(p))
        loc = _location_of(d, config)
        if not loc:
            continue
        root = loc[0]
        while _inside(d, root) and not os.path.isdir(fs_path(d)):
            d = os.path.dirname(d)   # removed by the action (e.g. an emptied common folder)
        top = None
        while _inside(d, root):
            entry, _, _ = _check_on_disk(d, config, lists)
            if not entry:
                break
            top = entry
            d = os.path.dirname(d)
        if top:
            found[_key(top["path"])] = top
    tops = [e for e in found.values() if not any(_inside(e["path"], o["path"]) for o in found.values())]
    if tops:
        _remember(tops)
        log.info("Leftover folders after action: %s", ", ".join(e["path"] for e in tops))
    return tops


def leftover_paths_after(changed_paths: Iterable[str], config: Dict[str, Any]) -> List[str]:
    """``check_after_action`` as the list of folder paths that action results carry (``leftovers``)."""
    return [e["path"] for e in check_after_action(changed_paths, config)]


# ── Delete (R-29) ─────────────────────────────────────────────────────────────
def _execute(ops: List[Dict[str, str]]) -> Tuple[int, Optional[str]]:
    """Run delete/rmdir operations in order; stops at the first failure. Returns (done, error)."""
    for i, o in enumerate(ops):
        try:
            if o["op"] == "delete":
                os.remove(fs_path(o["path"]))
            else:
                os.rmdir(fs_path(o["path"]))   # fails on anything that appeared in the meantime
        except OSError as exc:
            return i, f"{o['op']} failed for {o['path']}: {exc.strerror or exc}"
    return len(ops), None


def delete_leftovers(folders: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    """Delete the selected leftover folders (dry run by default in the API).

    ``folders``: ``[{path, files}]`` with ``files`` exactly as the user saw them. Each folder is re-checked
    on disk with the current settings (``_check_on_disk``) and its files must equal ``files``; otherwise
    that folder is refused. Then the files are deleted one by one and the folders removed with ``rmdir``,
    deepest first — anything unexpected stops that folder. Deleted folders leave the in-memory list and the
    location's cache. Per folder: ``ok``, ``operations``, ``error``.
    """
    with _lock:
        if _state["scanning"]:
            raise HTTPException(409, "A scan is running — wait until it has finished")
    config = load_config()
    lists = leftover_lists(config)
    results: List[Dict[str, Any]] = []
    gone: Dict[str, List[str]] = {}   # location root → deleted folders
    seen: set = set()
    for f in folders:
        path = os.path.normpath(f["path"])
        res: Dict[str, Any] = {"path": path, "ok": False, "dry_run": dry_run, "operations": [], "error": None}
        results.append(res)
        if _key(path) in seen:
            res["error"] = "Listed more than once"
            continue
        seen.add(_key(path))
        entry, dirs, reason = _check_on_disk(path, config, lists)
        if not entry:
            res["error"] = f"Refused — the folder {reason}"
            continue
        sent = {_key(x) for x in f["files"]}
        disk = {_key(x) for x in entry["files"]}
        if sent != disk:
            added = [x for x in entry["files"] if _key(x) not in sent]
            missing = [x for x in f["files"] if _key(x) not in disk]
            detail = "; ".join(filter(None, [f"new: {', '.join(added[:3])}" if added else "",
                                             f"gone: {', '.join(missing[:3])}" if missing else ""]))
            res["error"] = f"Refused — the content changed since it was listed ({detail}). Rescan and check again."
            continue
        ops = ([{"op": "delete", "path": os.path.join(path, x)} for x in entry["files"]]
               + [{"op": "rmdir", "path": d} for d in dirs + [path]])
        res["operations"] = ops
        if dry_run:
            res["ok"] = True
            continue
        done, err = _execute(ops)
        res["done"] = done
        res["ok"] = err is None
        if err:
            res["error"] = f"{err} — {done} of {len(ops)} operation(s) were done; the rest stays"
            log.warning("Leftover delete stopped in %s: %s", path, err)
        else:
            gone.setdefault(entry["root"], []).append(path)
            log.info("Leftover folder deleted: %s (%d file(s))", path, len(entry["files"]))
    if gone:
        from .scanner import drop_cached_leftovers   # late import: scanner imports this module
        _forget([p for paths in gone.values() for p in paths])
        for root, paths in gone.items():
            drop_cached_leftovers(root, paths)
    return results
