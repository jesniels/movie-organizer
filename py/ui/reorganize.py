"""Reorganize analysis: suggestions for standardizing items in place.

Read-only — nothing in this module changes files. Every suggestion is only applied
after the user selects it in the Reorganize dialog (execution is a separate step).
"""
from __future__ import annotations

import copy
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

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
)
from .state import _lock, _state, all_items

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
_BARE_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_NFO_RANK  = {"full": 2, "partial": 1, "none": 0}
_NFO_FIELDS = ("title", "year", "plot", "rating", "genre", "studio", "tagline", "uniqueids", "actors")


# ── Filesystem helpers ─────────────────────────────────────────────────────────
def _fs(path: str) -> str:
    """Filesystem form of a path; on Windows the \\\\?\\ prefix makes names ending in a dot/space reachable."""
    if sys.platform != "win32" or path.startswith("\\\\?\\"):
        return path
    path = path.replace("/", "\\")
    return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path


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
    return {
        "files":   entries,
        "best":    best[0]["name"] if unique else None,
        "target":  final_stem + ".nfo",
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


# ── Entry points ───────────────────────────────────────────────────────────────
def build_proposals(config: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Suggestions for the given items (read-only). Transcoded items are never analysed."""
    naming = config["naming"]
    roots = [p for p in config.get("locations", []) + config.get("downloads", []) if p]
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
        if item["type"] == "series":
            prop = _analyse_series(item, naming, planned, blocked)
        elif item["id"] != item["path"]:
            prop = _analyse_loose(item, naming, planned, blocked)
        else:
            prop = _analyse_movie_folder(item, naming, planned, blocked)
        if prop["folder"] or prop["file"] or prop["nfo"] or prop["nfos"] or prop["sanitize"]:
            proposals.append(prop)
        elif len(blocked) == n_blocked:
            compliant += 1
        # Folders above the item, up to (not including) its location/download folder
        root = _root_of(item["path"], roots)
        if root and naming.get("sanitize_names"):
            p = item["path"] if item["id"] != item["path"] else os.path.dirname(item["path"])
            while _key(p) != _key(root) and _key(p).startswith(_key(root).rstrip("\\/") + os.sep):
                parents.setdefault(_key(p), p)
                p = os.path.dirname(p)
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
