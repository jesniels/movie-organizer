"""Reorganize: suggestions for standardizing items in place, and their execution.

The analysis only reads the disk. Files are changed only by apply_changes(), with the
choices the user selected in the Reorganize dialog and dry run switched off.
"""
from __future__ import annotations

import copy
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from .duplicates import _update_not_duplicates_paths
from .nfo import _nfo_quality, parse_nfo
from .settings import (
    EPISODE_RE,
    QUALITY_STRIP,
    VIDEO_EXTS,
    YEAR_RE,
    expand_naming,
    load_config,
    log,
    sanitize_component,
    valid_component,
)
from .settings import fs_path as _fs
from .state import _lock, _state, all_items

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
_BARE_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_NFO_RANK  = {"full": 2, "partial": 1, "none": 0}
_NFO_FIELDS = ("title", "year", "plot", "rating", "genre", "studio", "tagline", "uniqueids", "actors")


# ── Filesystem helpers ─────────────────────────────────────────────────────────

def _exists(path: str) -> bool:
    return os.path.exists(_fs(path))


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _walk(folder: str) -> Tuple[List[str], List[str]]:
    """All sub-folders and files below folder (recursive), as plain paths."""
    dirs: List[str] = []
    files: List[str] = []
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(_fs(d)) as it:
                for e in it:
                    p = os.path.join(d, e.name)
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(p)
                        stack.append(p)
                    elif e.is_file():
                        files.append(p)
        except OSError as exc:
            log.warning("Reorganize: cannot read %s: %s", d, exc)
    return dirs, files


def _file_names(folder: str) -> List[str]:
    """Names of the files directly inside folder."""
    try:
        with os.scandir(_fs(folder)) as it:
            return [e.name for e in it if e.is_file()]
    except OSError as exc:
        log.warning("Reorganize: cannot read %s: %s", folder, exc)
        return []


def _root_of(path: str, roots: List[str]) -> Optional[str]:
    """The longest configured location/download folder containing path."""
    pk = _key(path)
    best: Optional[str] = None
    for r in roots:
        rk = _key(r).rstrip("\\/")
        if pk.startswith(rk + os.sep) and (best is None or len(rk) > len(_key(best).rstrip("\\/"))):
            best = r
    return best


# ── Names ──────────────────────────────────────────────────────────────────────
def parse_name(name: str) -> Tuple[str, str]:
    """Title and year from a folder name or file stem; also understands scene names (Inception.2010.1080p)."""
    m = YEAR_RE.search(name)
    if m:
        title, year = name[:m.start()], m.group(1)
    else:
        s = name if " " in name else re.sub(r"[._]+", " ", name)
        max_year = datetime.now().year + 1
        q = QUALITY_STRIP.search(s)
        # A year at the very start is a title ("1917", "2012 (2009)"), not the release year
        years = [y for y in _BARE_YEAR.finditer(s)
                 if y.start() > 0 and int(y.group(1)) <= max_year and (not q or y.start() < q.start())]
        if years:
            title, year = s[:years[-1].start()], years[-1].group(1)
        else:
            title, year = s, ""
    if " " not in title.strip():
        title = re.sub(r"[._]+", " ", title)
    title = QUALITY_STRIP.sub("", title)
    title = re.sub(r"[\s\-\[\(]+$", "", title)
    return re.sub(r"\s+", " ", title).strip(), year


def related_files(video: str, siblings: Iterable[str]) -> List[str]:
    """Exact-stem sidecars of a video file name: same stem, or stem + '.'/'-' (R-09). Other videos are excluded."""
    v = video.lower()
    stem = os.path.splitext(v)[0]
    out = []
    for n in siblings:
        low = n.lower()
        if low == v or os.path.splitext(low)[1] in VIDEO_EXTS:
            continue
        if os.path.splitext(low)[0] == stem or low.startswith((stem + ".", stem + "-")):
            out.append(n)
    return sorted(out, key=str.lower)


def _follow(name: str, old_stem: str, new_stem: str) -> str:
    """A sidecar's new name when its video stem changes, keeping the suffix chain (Old.en.srt → New.en.srt)."""
    return new_stem + name[len(old_stem):]


def _title_year(item: Dict[str, Any], names: List[str]) -> Tuple[str, str, str]:
    """(title, year, source) — NFO values win per field; otherwise the first parsed name that has a year."""
    nfo = item.get("nfo") or {}
    nt = (nfo.get("title") or "").strip()
    ym = re.match(r"\d{4}", (nfo.get("year") or "").strip())
    ny = ym.group(0) if ym else ""
    pt, py = "", ""
    for n in names:
        t, y = parse_name(n)
        if not pt:
            pt = t
        if y:
            pt, py = t, y
            break
    title, year = nt or pt, ny or py
    source = "nfo" if nt and ny else ("name" if not nt and not ny else "nfo+name")
    return title, year, source


def _free_name(parent: str, name: str, planned: Dict[str, str]) -> Optional[str]:
    """First "<name> (n)" that neither exists nor is planned by another suggestion."""
    for n in range(1, 100):
        cand = f"{name} ({n})"
        p = os.path.join(parent, cand)
        if not _exists(p) and _key(p) not in planned:
            return cand
    return None


def _target_conflict(src: str, target: str, planned: Dict[str, str]) -> bool:
    """True when target exists (other than src itself) or another suggestion already plans it."""
    return (_exists(target) and _key(target) != _key(src)) or _key(target) in planned


# ── NFO choice (several NFOs in a movie folder) ────────────────────────────────
def _nfo_choice(folder: str, nfo_names: List[str], old_stem: str, final_stem: str) -> Dict[str, Any]:
    """Options for a movie folder with several NFOs; the default is always to leave them as they are."""
    entries = []
    for n in sorted(nfo_names, key=str.lower):
        nfo = parse_nfo(Path(_fs(os.path.join(folder, n))))
        quality = _nfo_quality(nfo)
        # NFOs named after the video follow a video rename, so show their name after it
        after = _follow(n, old_stem, final_stem) if os.path.splitext(n)[0].lower() == old_stem.lower() else n
        entries.append({"name": n, "after": after, "quality": quality,
                        "fields": sum(1 for k in _NFO_FIELDS if nfo.get(k)),
                        "_score": (_NFO_RANK[quality], sum(1 for k in _NFO_FIELDS if nfo.get(k)))})
    top = max(e["_score"] for e in entries)
    best = [e for e in entries if e["_score"] == top]
    for e in entries:
        del e["_score"]
    unique = len(best) == 1
    # A best NFO already named movie.nfo or <video>.nfo works for Jellyfin — keep its name
    ok_names = ("movie.nfo", (final_stem + ".nfo").lower())
    target = best[0]["after"] if unique and best[0]["after"].lower() in ok_names else final_stem + ".nfo"
    return {
        "files":   entries,
        "best":    best[0]["name"] if unique else None,
        "target":  target,
        "rename_best": bool(unique) and best[0]["after"] != target,
        "options": (["use_best"] if unique else []) + ["delete_all", "leave"],
        "default": "leave",
        "detail":  None if unique else "No single best NFO (equal quality) — use best is not possible",
    }


# ── Sanitize (check 6) ─────────────────────────────────────────────────────────
def _sanitize_entry(path: str, kind: str, new: str, item_id: Optional[str],
                    planned: Dict[str, str], blocked: List[Dict[str, Any]],
                    follows: Optional[List[Dict[str, str]]] = None) -> Optional[Dict[str, Any]]:
    old = os.path.basename(path)
    if not new:
        blocked.append({"item_id": item_id, "path": path, "reason": "sanitize_empty",
                        "detail": f"“{old}” has no legal characters left — rename manually"})
        return None
    m_old, m_new = EPISODE_RE.search(old), EPISODE_RE.search(new)
    if (m_old.groups() if m_old else None) != (m_new.groups() if m_new else None):
        blocked.append({"item_id": item_id, "path": path, "reason": "episode_changed",
                        "detail": f"Sanitizing “{old}” would change the episode number — rename manually"})
        return None
    parent = os.path.dirname(path)
    target = os.path.join(parent, new)
    conflict = _target_conflict(path, target, planned)
    follows = follows or []
    for f in follows:
        conflict = conflict or _target_conflict(os.path.join(parent, f["from"]), os.path.join(parent, f["to"]), planned)
    planned.setdefault(_key(target), item_id or "")
    for f in follows:
        planned.setdefault(_key(os.path.join(parent, f["to"])), item_id or "")
    return {"path": path, "kind": kind, "current": old, "proposed": new, "follows": follows, "conflict": conflict}


def _sanitize_entries(dirs: List[str], files: List[str], covered: set, item_id: Optional[str],
                      planned: Dict[str, str], blocked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check-6 entries for the given folders/files; sidecars of a sanitized video follow it."""
    entries: List[Dict[str, Any]] = []
    by_dir: Dict[str, List[str]] = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(f), []).append(os.path.basename(f))
    followers: set = set()
    for d, names in by_dir.items():
        # Videos first, so their sidecars are claimed as followers
        for n in sorted(names, key=lambda x: (os.path.splitext(x)[1].lower() not in VIDEO_EXTS, x.lower())):
            p = os.path.join(d, n)
            if _key(p) in covered or _key(p) in followers:
                continue
            stem, ext = os.path.splitext(n)
            new_stem = sanitize_component(stem)
            if new_stem == stem:
                continue
            follows = []
            if ext.lower() in VIDEO_EXTS and new_stem:
                for s in related_files(n, names):
                    sp = os.path.join(d, s)
                    if _key(sp) not in covered:
                        followers.add(_key(sp))
                        follows.append({"from": s, "to": _follow(s, stem, new_stem)})
            e = _sanitize_entry(p, "file", new_stem + ext if new_stem else "", item_id, planned, blocked, follows)
            if e:
                entries.append(e)
    for d in sorted(dirs, key=lambda x: x.count(os.sep), reverse=True):
        if _key(d) in covered:
            continue
        name = os.path.basename(d)
        new = sanitize_component(name)
        if new != name:
            e = _sanitize_entry(d, "folder", new, item_id, planned, blocked)
            if e:
                entries.append(e)
    return entries


# ── Per-item analysis ──────────────────────────────────────────────────────────
def _stale(folder_files: List[str], item: Dict[str, Any]) -> Optional[str]:
    """Detail text when the videos on disk differ from the last scan; None when they match."""
    on_disk = {_key(f) for f in folder_files if os.path.splitext(f)[1].lower() in VIDEO_EXTS}
    known = {_key(f) for f in item["files"]}
    extra, missing = on_disk - known, known - on_disk
    if not extra and not missing:
        return None
    return (f"{len(extra)} video file(s) not known from the last scan, {len(missing)} missing on disk — rescan first")


def _new_proposal(item: Dict[str, Any], kind: str, title: str, year: str, source: str) -> Dict[str, Any]:
    return {"item_id": item["id"], "type": item["type"], "kind": kind, "location": item.get("location"),
            "path": item["id"], "title": title, "year": year, "source": source,
            "folder": None, "file": None, "move": [], "images": [], "nfo": None, "nfos": None, "sanitize": []}


def _folder_op(folder: str, new_name: str, planned: Dict[str, str], item_id: str) -> Dict[str, Any]:
    parent, name = os.path.split(folder)
    target = os.path.join(parent, new_name)
    conflict = _target_conflict(folder, target, planned)
    planned.setdefault(_key(target), item_id)
    return {"from": name, "to": new_name, "create": False, "conflict": conflict,
            "suggested": _free_name(parent, new_name, planned) if conflict else None}


def _analyse_loose(item: Dict[str, Any], naming: Dict[str, Any], planned: Dict[str, str],
                   blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check 1: a Loose File gets its own folder next to it (the file itself is acted on, never item['path'])."""
    video, parent = item["id"], item["path"]
    vname = os.path.basename(video)
    stem, ext = os.path.splitext(vname)
    title, year, source = _title_year(item, [stem])
    prop = _new_proposal(item, "loose_file", title, year, source)
    if not _exists(video):
        blocked.append({"item_id": item["id"], "path": video, "reason": "stale_cache",
                        "detail": "The file no longer exists — rescan first"})
        return prop
    siblings = _file_names(parent)
    related = related_files(vname, siblings)
    folder_name = expand_naming(naming["movie_folder"], title, year)
    if folder_name is None:
        blocked.append({"item_id": item["id"], "path": video, "reason": "no_year",
                        "detail": "No year known (NFO or name) — add a year (NFO) first"})
        if naming.get("sanitize_names"):
            prop["sanitize"] = _sanitize_entries([], [os.path.join(parent, n) for n in [vname] + related],
                                                 set(), item["id"], planned, blocked)
        return prop
    new_stem = sanitize_component(stem) or stem
    if naming.get("rename_files"):
        file_stem = expand_naming(naming["movie_file"], title, year)
        if file_stem:
            new_stem = file_stem
    target = os.path.join(parent, folder_name)
    conflict = _exists(target) or _key(target) in planned
    planned.setdefault(_key(target), item["id"])
    prop["folder"] = {"from": None, "to": folder_name, "create": True, "conflict": conflict,
                      "suggested": _free_name(parent, folder_name, planned) if conflict else None}
    images = [r for r in related if os.path.splitext(r)[1].lower() in IMAGE_EXTS] if naming.get("delete_images") else []
    prop["images"] = images
    prop["move"] = [{"from": n, "to": _follow(n, stem, new_stem)}
                    for n in [vname] + related if n not in images]
    if new_stem != stem:
        prop["file"] = {"from": vname, "to": new_stem + ext, "follows": [m for m in prop["move"][1:]], "conflict": False}
    return prop


def _analyse_movie_folder(item: Dict[str, Any], naming: Dict[str, Any], planned: Dict[str, str],
                          blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Checks 2–4 for a movie in its own folder, plus check 6 inside it."""
    folder = item["path"]
    dirs, files = _walk(folder)
    top = [os.path.basename(f) for f in files if _key(os.path.dirname(f)) == _key(folder)]
    main = next((n for n in sorted(top, key=str.lower) if os.path.splitext(n)[1].lower() in VIDEO_EXTS), None)
    names = [item["folder"]] + ([os.path.splitext(main)[0]] if main else [])
    title, year, source = _title_year(item, names)
    prop = _new_proposal(item, "movie_folder", title, year, source)
    covered: set = set()
    stale = _stale(files, item)
    if stale:
        blocked.append({"item_id": item["id"], "path": folder, "reason": "stale_cache", "detail": stale})
    else:
        folder_name = expand_naming(naming["movie_folder"], title, year)
        if folder_name is None:
            blocked.append({"item_id": item["id"], "path": folder, "reason": "no_year",
                            "detail": "No year known (NFO or name) — add a year (NFO) first"})
        elif folder_name.lower() != item["folder"].lower():
            prop["folder"] = _folder_op(folder, folder_name, planned, item["id"])
            covered.add(_key(folder))
        old_stem = os.path.splitext(main)[0] if main else ""
        final_stem = old_stem
        if main and naming.get("rename_files"):
            file_stem = expand_naming(naming["movie_file"], title, year)
            if file_stem and file_stem.lower() != old_stem.lower():
                if len(item["files"]) > 1:
                    blocked.append({"item_id": item["id"], "path": folder, "reason": "multi_video",
                                    "detail": f"Contains {len(item['files'])} video files — rename the video manually"})
                else:
                    ext = os.path.splitext(main)[1]
                    follows = [{"from": r, "to": _follow(r, old_stem, file_stem)} for r in related_files(main, top)]
                    conflict = _target_conflict(os.path.join(folder, main), os.path.join(folder, file_stem + ext), planned)
                    for f in follows:
                        conflict = conflict or _target_conflict(os.path.join(folder, f["from"]),
                                                                os.path.join(folder, f["to"]), planned)
                    prop["file"] = {"from": main, "to": file_stem + ext, "follows": follows, "conflict": conflict}
                    covered.update(_key(os.path.join(folder, n)) for n in [main] + [f["from"] for f in follows])
                    final_stem = file_stem
        if main and naming.get("resolve_nfos"):
            nfos = [n for n in top if n.lower().endswith(".nfo")]
            if len(nfos) > 1:
                prop["nfos"] = _nfo_choice(folder, nfos, old_stem, final_stem)
            elif nfos:
                n = nfos[0]
                follows_video = prop["file"] and any(f["from"] == n for f in prop["file"]["follows"])
                # Jellyfin only reads movie.nfo or <video>.nfo — a lone NFO with another name is ignored
                if not follows_video and n.lower() not in ("movie.nfo", (old_stem + ".nfo").lower()):
                    src, dst = os.path.join(folder, n), os.path.join(folder, final_stem + ".nfo")
                    prop["nfo"] = {"from": n, "to": final_stem + ".nfo", "conflict": _target_conflict(src, dst, planned)}
                    planned.setdefault(_key(dst), item["id"])
                    covered.add(_key(src))
    if naming.get("sanitize_names"):
        prop["sanitize"] = _sanitize_entries(dirs + [folder], files, covered, item["id"], planned, blocked)
    return prop


def _analyse_series(item: Dict[str, Any], naming: Dict[str, Any], planned: Dict[str, str],
                    blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check 5: series folder name only (episodes are never renamed to a format), plus check 6 inside it."""
    folder = item["path"]
    dirs, files = _walk(folder)
    title, year, source = _title_year(item, [item["folder"]])
    prop = _new_proposal(item, "series", title, year, source)
    covered: set = set()
    stale = _stale(files, item)
    if stale:
        blocked.append({"item_id": item["id"], "path": folder, "reason": "stale_cache", "detail": stale})
    else:
        folder_name = expand_naming(naming["series_folder"], title, year)
        if folder_name is None:
            blocked.append({"item_id": item["id"], "path": folder, "reason": "no_year",
                            "detail": "No year known (tvshow.nfo or folder name) — add a year first"})
        elif folder_name.lower() != item["folder"].lower():
            prop["folder"] = _folder_op(folder, folder_name, planned, item["id"])
            covered.add(_key(folder))
    if naming.get("sanitize_names"):
        prop["sanitize"] = _sanitize_entries(dirs + [folder], files, covered, item["id"], planned, blocked)
    return prop


def _analyse_item(item: Dict[str, Any], naming: Dict[str, Any], planned: Dict[str, str],
                  blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
    if item["type"] == "series":
        return _analyse_series(item, naming, planned, blocked)
    if item["id"] != item["path"]:
        return _analyse_loose(item, naming, planned, blocked)
    return _analyse_movie_folder(item, naming, planned, blocked)


def _roots(config: Dict[str, Any]) -> List[str]:
    return [p for p in config.get("locations", []) + config.get("downloads", []) if p]


def _parent_folders(item: Dict[str, Any], roots: List[str]) -> List[str]:
    """Folders above the item, up to (not including) its location/download folder."""
    root = _root_of(item["path"], roots)
    out: List[str] = []
    if root:
        p = item["path"] if item["id"] != item["path"] else os.path.dirname(item["path"])
        while _key(p) != _key(root) and _key(p).startswith(_key(root).rstrip("\\/") + os.sep):
            out.append(p)
            p = os.path.dirname(p)
    return out


# ── Entry points ───────────────────────────────────────────────────────────────
def build_proposals(config: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Suggestions for the given items (read-only). Transcoded items are never analysed."""
    naming = config["naming"]
    roots = _roots(config)
    planned: Dict[str, str] = {}   # normcase target path → item id, to catch two suggestions with one target
    proposals: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    parents: Dict[str, str] = {}
    compliant = analysed = 0
    for item in items:
        if item.get("location") == "transcoded":
            continue
        analysed += 1
        n_blocked = len(blocked)
        prop = _analyse_item(item, naming, planned, blocked)
        if prop["folder"] or prop["file"] or prop["nfo"] or prop["nfos"] or prop["sanitize"]:
            proposals.append(prop)
        elif len(blocked) == n_blocked:
            compliant += 1
        if naming.get("sanitize_names"):
            for p in _parent_folders(item, roots):
                parents.setdefault(_key(p), p)
    parent_entries: List[Dict[str, Any]] = []
    if naming.get("sanitize_names"):
        parent_entries = _sanitize_entries(list(parents.values()), [], set(), None, planned, blocked)
    result = {
        "generated":       datetime.now().isoformat(timespec="seconds"),
        "analysed_count":  analysed,
        "compliant_count": compliant,
        "proposals":       proposals,
        "parent_folders":  parent_entries,
        "blocked":         blocked,
    }
    log.info("Reorganize analysis: %d item(s) analysed, %d with suggestions, %d blocked, %d compliant, "
             "%d parent folder(s) to sanitize", analysed, len(proposals), len(blocked), compliant, len(parent_entries))
    return result


def analyse(item_ids: List[str]) -> Dict[str, Any]:
    """Analyse the items shown by the user's current filters. Refused while a scan is running."""
    config = load_config()
    wanted = set(item_ids)
    with _lock:
        if _state["scanning"]:
            raise HTTPException(409, "A scan is running — wait until it has finished")
        items = copy.deepcopy([i for i in all_items() if i["id"] in wanted])
    return build_proposals(config, items)


# ── Execution ──────────────────────────────────────────────────────────────────
_ITEM_REASONS = ("stale_cache", "no_year", "multi_video")


class _Refused(Exception):
    """An item cannot be executed; the message is shown to the user."""


class _View:
    """The disk as it will look after the operations planned so far (dry runs span several items)."""

    def __init__(self, added: Optional[set] = None, removed: Optional[set] = None):
        self.added = set(added or ())
        self.removed = set(removed or ())

    def copy(self) -> "_View":
        return _View(self.added, self.removed)

    def exists(self, path: str) -> bool:
        k = _key(path)
        if k in self.added:
            return True
        return k not in self.removed and _exists(path)

    def add(self, path: str) -> None:
        self.removed.discard(_key(path))
        self.added.add(_key(path))

    def remove(self, path: str) -> None:
        self.added.discard(_key(path))
        self.removed.add(_key(path))


class _Planner:
    """Collects one item's operations, checking each against the planned view before anything is touched."""

    def __init__(self, view: _View, roots: List[str]):
        self.view = view
        self.roots = roots
        self.ops: List[Dict[str, Any]] = []
        self._now: Dict[str, str] = {}   # original path → path after the renames planned so far

    def cur(self, path: str) -> str:
        return self._now.get(_key(path), path)

    def _scope(self, path: str) -> None:
        if not _root_of(path, self.roots):
            raise _Refused(f"Outside the configured locations/download folders: {path}")

    def mkdir(self, path: str) -> None:
        self._scope(path)
        err = valid_component(os.path.basename(path))
        if err:
            raise _Refused(err)
        if self.view.exists(path):
            raise _Refused(f"Target already exists: {path}")
        self.view.add(path)
        self.ops.append({"op": "mkdir", "kind": "folder", "from": None, "to": path})

    def rename(self, src: str, dst: str, kind: str, op: str = "rename",
               track: Optional[Tuple[str, str]] = None) -> None:
        cur = self.cur(src)
        self._scope(cur)
        self._scope(dst)
        err = valid_component(os.path.basename(dst))
        if err:
            raise _Refused(err)
        if not self.view.exists(cur):
            raise _Refused(f"Not found: {cur}")
        if cur == dst:
            return
        # A change of letter case only is the same path on Windows — allowed, like the Rename dialog
        if _key(cur) != _key(dst) and self.view.exists(dst):
            raise _Refused(f"Target already exists: {dst}")
        self.view.remove(cur)
        self.view.add(dst)
        self._now[_key(src)] = dst
        if track is None and (kind == "folder" or os.path.splitext(dst)[1].lower() in VIDEO_EXTS):
            track = (cur, dst)
        self.ops.append({"op": op, "kind": kind, "from": cur, "to": dst, "_track": track})

    def delete(self, path: str) -> None:
        cur = self.cur(path)
        self._scope(cur)
        if not self.view.exists(cur):
            raise _Refused(f"Not found: {cur}")
        self.view.remove(cur)
        self.ops.append({"op": "delete", "kind": "file", "from": cur, "to": None})


def _component(name: str) -> str:
    """A user-edited name, checked as a whole (a basename check would let 'a\\b' through as 'b')."""
    err = valid_component(name)
    if err:
        raise _Refused(err)
    return name


def _wants_format(c: Any) -> bool:
    return c.apply_folder or c.apply_file or c.apply_nfo or c.nfo_action != "leave" or c.delete_images


def _plan_sanitize(entries: List[Dict[str, Any]], pl: _Planner) -> None:
    """Sanitize renames, deepest path first; a sanitized video's sidecars follow it."""
    for e in sorted(entries, key=lambda e: e["path"].count(os.sep), reverse=True):
        parent = os.path.dirname(e["path"])
        pl.rename(e["path"], os.path.join(parent, e["proposed"]), e["kind"])
        for f in e["follows"]:
            pl.rename(os.path.join(parent, f["from"]), os.path.join(parent, f["to"]), "file")


def _plan_folder(c: Any, item: Dict[str, Any], prop: Dict[str, Any], reasons: Dict[str, str],
                 own: Optional[Dict[str, Any]], pl: _Planner) -> None:
    """The item folder last: the format rename, or else sanitizing its name."""
    folder = item["path"]
    if c.apply_folder:
        if own:
            raise _Refused("Choose either the folder rename or sanitizing the folder name, not both")
        name = c.folder_name if c.folder_name is not None else (prop["folder"] or {}).get("to")
        if name is None:
            raise _Refused(reasons.get("no_year") or "The folder rename is no longer proposed — refresh the analysis")
        pl.rename(folder, os.path.join(os.path.dirname(folder), _component(name)), "folder")
    elif own:
        _plan_sanitize([own], pl)


def _plan_loose(c: Any, item: Dict[str, Any], prop: Dict[str, Any], reasons: Dict[str, str],
                chosen: List[Dict[str, Any]], pl: _Planner) -> None:
    """Check 1: create the folder next to the file and move the video + its sidecars into it."""
    video, parent = item["id"], item["path"]
    if c.apply_file and not c.apply_folder:
        raise _Refused("A loose file is only renamed together with creating its folder")
    if c.apply_nfo or c.nfo_action != "leave":
        raise _Refused("NFO actions do not apply to a loose file")
    if c.delete_images and not prop["images"]:
        raise _Refused("No images are proposed for deletion — refresh the analysis")
    _plan_sanitize(chosen, pl)
    if c.apply_folder:
        if not prop["folder"]:
            raise _Refused(reasons.get("no_year") or "Creating a folder is no longer proposed — refresh the analysis")
        target = os.path.join(parent, _component(c.folder_name if c.folder_name is not None else prop["folder"]["to"]))
        stem, ext = os.path.splitext(os.path.basename(video))
        new_stem = sanitize_component(stem) or stem
        if c.apply_file:
            if c.file_name is not None:
                new_stem = c.file_name
            elif prop["file"]:
                new_stem = os.path.splitext(prop["file"]["to"])[0]
        _component(new_stem)   # the stem alone too: "Name .mkv" passes as a whole name
        _component(new_stem + ext)
        pl.mkdir(target)
        names = [m["from"] for m in prop["move"]] + ([] if c.delete_images else prop["images"])
        for n in names:
            # The item id becomes the new folder, so "not a duplicate" marks follow the video there
            track = (video, target) if n == os.path.basename(video) else None
            pl.rename(os.path.join(parent, n), os.path.join(target, _follow(n, stem, new_stem)), "file",
                      op="move", track=track)
    if c.delete_images:
        for img in prop["images"]:
            pl.delete(os.path.join(parent, img))


def _plan_movie(c: Any, item: Dict[str, Any], prop: Dict[str, Any], reasons: Dict[str, str],
                chosen: List[Dict[str, Any]], pl: _Planner) -> None:
    """Checks 2–4 and sanitize: sanitize → NFO deletions → file rename → NFO rename → folder."""
    folder = item["path"]
    if c.delete_images:
        raise _Refused("Image deletion is only proposed for loose files")
    own = next((e for e in chosen if _key(e["path"]) == _key(folder)), None)
    _plan_sanitize([e for e in chosen if e is not own], pl)

    top = _file_names(folder)
    main = next((n for n in sorted(top, key=str.lower) if os.path.splitext(n)[1].lower() in VIDEO_EXTS), None)
    old_stem = os.path.splitext(main)[0] if main else ""

    deleted: set = set()
    best: Optional[str] = None
    if c.nfo_action != "leave":
        nfos = prop["nfos"]
        if not nfos:
            raise _Refused("Several NFOs are no longer found — refresh the analysis")
        if {n.lower() for n in c.nfo_files} != {f["name"].lower() for f in nfos["files"]}:
            raise _Refused("The NFO files changed since the analysis — refresh and choose again")
        if c.nfo_action == "use_best":
            if not nfos["best"]:
                raise _Refused(nfos["detail"] or "No single best NFO")
            best = nfos["best"]
        for f in nfos["files"]:
            if f["name"] != best:
                pl.delete(os.path.join(folder, f["name"]))
                deleted.add(f["name"].lower())
    if c.apply_nfo and not prop["nfo"]:
        raise _Refused("The NFO rename is no longer proposed — refresh the analysis")

    final_stem = os.path.splitext(os.path.basename(pl.cur(os.path.join(folder, main))))[0] if main else ""
    if c.apply_file:
        if not main:
            raise _Refused("No video file in the folder itself")
        if len(item["files"]) > 1:
            raise _Refused(reasons.get("multi_video")
                           or f"Contains {len(item['files'])} video files — rename the video manually")
        if c.file_name is not None:
            stem = c.file_name
        else:
            stem = os.path.splitext(prop["file"]["to"])[0] if prop["file"] else old_stem
        _component(stem)   # the stem alone too: "Name .mkv" passes as a whole name
        _component(stem + os.path.splitext(main)[1])
        if stem != old_stem:
            pl.rename(os.path.join(folder, main), os.path.join(folder, stem + os.path.splitext(main)[1]), "file")
            for r in related_files(main, top):
                if r.lower() not in deleted:
                    pl.rename(os.path.join(folder, r), os.path.join(folder, _follow(r, old_stem, stem)), "file")
            final_stem = stem

    if c.apply_nfo:
        pl.rename(os.path.join(folder, prop["nfo"]["from"]), os.path.join(folder, final_stem + ".nfo"), "file")
    if best:
        # Never renamed when already movie.nfo / <video>.nfo (decision #18)
        name = os.path.basename(pl.cur(os.path.join(folder, best)))
        if name.lower() not in ("movie.nfo", (final_stem + ".nfo").lower()):
            pl.rename(os.path.join(folder, best), os.path.join(folder, final_stem + ".nfo"), "file")

    _plan_folder(c, item, prop, reasons, own, pl)


def _plan_series(c: Any, item: Dict[str, Any], prop: Dict[str, Any], reasons: Dict[str, str],
                 chosen: List[Dict[str, Any]], pl: _Planner) -> None:
    """Check 5 and sanitize: episodes and season folders are only sanitized, never renamed to a format."""
    if c.apply_file or c.apply_nfo or c.nfo_action != "leave" or c.delete_images:
        raise _Refused("For a series only the folder is renamed — episodes and season folders are only sanitized")
    own = next((e for e in chosen if _key(e["path"]) == _key(item["path"])), None)
    _plan_sanitize([e for e in chosen if e is not own], pl)
    _plan_folder(c, item, prop, reasons, own, pl)


_PLANNERS = {"loose_file": _plan_loose, "movie_folder": _plan_movie, "series": _plan_series}


def _plan_item(c: Any, item: Dict[str, Any], naming: Dict[str, Any], pl: _Planner) -> None:
    """Re-run the analysis on the current disk and plan only what the user selected from it."""
    blocked: List[Dict[str, Any]] = []
    prop = _analyse_item(item, naming, {}, blocked)
    reasons = {b["reason"]: b["detail"] for b in blocked if b["reason"] in _ITEM_REASONS}
    if "stale_cache" in reasons and _wants_format(c):
        raise _Refused(reasons["stale_cache"])
    entries = {_key(e["path"]): e for e in prop["sanitize"]}
    chosen = []
    for p in c.sanitize:
        e = entries.get(_key(p))
        if not e:
            why = next((b["detail"] for b in blocked if _key(b["path"]) == _key(p)), None)
            raise _Refused(why or f"Sanitizing {p} is no longer proposed — refresh the analysis")
        chosen.append(e)
    _PLANNERS[prop["kind"]](c, item, prop, reasons, chosen, pl)


def _plan_parents(c: Any, allowed: Dict[str, str], pl: _Planner) -> None:
    """The “Parent folders” group: sanitize only, and only folders above a scanned item."""
    if _wants_format(c):
        raise _Refused("Parent folders can only be sanitized")
    dirs = []
    for p in c.sanitize:
        if _key(p) not in allowed:
            raise _Refused(f"Not a folder above a scanned item: {p}")
        dirs.append(allowed[_key(p)])
    blocked: List[Dict[str, Any]] = []
    entries = _sanitize_entries(dirs, [], set(), None, {}, blocked)
    if blocked:
        raise _Refused(blocked[0]["detail"])
    found = {_key(e["path"]) for e in entries}
    missing = [d for d in dirs if _key(d) not in found]
    if missing:
        raise _Refused(f"Sanitizing {missing[0]} is no longer proposed — refresh the analysis")
    _plan_sanitize(entries, pl)


def _execute(ops: List[Dict[str, Any]]) -> Tuple[int, Optional[str]]:
    """Run the planned operations in order; stops at the first failure. Returns (done, error)."""
    for i, o in enumerate(ops):
        try:
            if o["op"] == "mkdir":
                os.mkdir(_fs(o["to"]))
            elif o["op"] == "delete":
                os.remove(_fs(o["from"]))
            else:
                # Never overwrite (R-03) — os.rename replaces an existing file silently on Linux
                if _exists(o["to"]) and _key(o["to"]) != _key(o["from"]):
                    return i, f"Target already exists: {o['to']}"
                os.rename(_fs(o["from"]), _fs(o["to"]))
        except OSError as exc:
            return i, f"{o['op']} failed for {o['from'] or o['to']}: {exc}"
        log.info("Reorganize: %s %s%s", o["op"], o["from"] or o["to"], f" → {o['to']}" if o["from"] and o["to"] else "")
        if o.get("_track"):
            _update_not_duplicates_paths(*o["_track"])
    return len(ops), None


def apply_changes(changes: List[Any], dry_run: bool) -> List[Dict[str, Any]]:
    """Execute (or dry-run) the user's selections. Items first, then “Parent folders”; no rollback."""
    config = load_config()
    naming = config["naming"]
    roots = _roots(config)
    wanted = {c.item_id for c in changes if c.item_id}
    allowed: Dict[str, str] = {}
    with _lock:
        if _state["scanning"]:
            raise HTTPException(409, "A scan is running — wait until it has finished")
        job = _state["transcode"]
        if job and job.get("running"):
            raise HTTPException(409, "A transcoding job is running — wait until it has finished")
        items = {i["id"]: copy.deepcopy(i) for i in all_items() if i["id"] in wanted}
        if any(c.item_id is None for c in changes):
            for i in all_items():
                if i.get("location") != "transcoded":
                    for p in _parent_folders(i, roots):
                        allowed.setdefault(_key(p), p)

    results: List[Dict[str, Any]] = []
    view = _View()
    seen: set = set()
    # Parent folders last, so no item path changes under a pending operation (decision #22)
    for c in sorted(changes, key=lambda c: c.item_id is None):
        res: Dict[str, Any] = {"item_id": c.item_id, "ok": False, "dry_run": dry_run, "operations": [], "error": None}
        results.append(res)
        if c.item_id in seen:
            res["error"] = "Listed more than once"
            continue
        seen.add(c.item_id)
        # A dry run plans on top of the earlier items' plans; a real run re-reads the disk per item
        pl = _Planner(view.copy() if dry_run else _View(), roots)
        try:
            if c.item_id is None:
                _plan_parents(c, allowed, pl)
            else:
                item = items.get(c.item_id)
                if not item:
                    raise _Refused("Not found — rescan first")
                if item.get("location") == "transcoded":
                    raise _Refused("Transcoded items are not reorganized")
                _plan_item(c, item, naming, pl)
        except _Refused as exc:
            res["error"] = str(exc)
            continue
        res["operations"] = [{k: v for k, v in o.items() if not k.startswith("_")} for o in pl.ops]
        if dry_run:
            view = pl.view
            res["ok"] = True
            continue
        done, err = _execute(pl.ops)
        res["ok"] = err is None
        if err:
            res["error"] = f"{err} — {done} of {len(pl.ops)} operation(s) were done"
        res["done"] = done
    log.info("Reorganize %s: %d change(s), %d ok, %d refused/failed", "dry run" if dry_run else "execute",
             len(results), sum(r["ok"] for r in results), sum(not r["ok"] for r in results))
    return results
