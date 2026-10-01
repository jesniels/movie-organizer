"""Library/download scanning, cache persistence, and status computation."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .duplicates import _dismissed_pairs, _find_duplicates
from .nfo import _nfo_quality, parse_nfo
from .settings import (
    CACHE_DIR,
    EPISODE_RE,
    SEASON_DIR_RE,
    VIDEO_EXTS,
    YEAR_RE,
    _clean_title,
    fs_path,
    load_config,
    log,
    safe_transcoded_root,
    transcoded_root,
    transcoded_root_conflict,
)
from .state import _lock, _state


# ── Scanner helpers ────────────────────────────────────────────────────────────
def _is_dir(p: Path) -> bool:
    """Path.is_dir() that also sees folders whose name ends with a space/dot (Windows strips those)."""
    return p.is_dir() or os.path.isdir(fs_path(str(p)))


def _is_season_dir(p: Path) -> bool:
    return SEASON_DIR_RE.fullmatch(p.name.strip()) is not None and _is_dir(p)


def _walk_entries(folder: Path) -> List[Tuple[Path, bool]]:
    """(path, is_dir) for everything below folder; unlike rglob it also enters folders ending with a space/dot."""
    out: List[Tuple[Path, bool]] = []
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(fs_path(str(d))) as it:
                for e in it:
                    p = d / e.name
                    is_dir = e.is_dir(follow_symlinks=False)
                    out.append((p, is_dir))
                    if is_dir:
                        stack.append(p)
        except OSError as exc:
            log.warning("Cannot read directory %s: %s", d, exc)
    return out


def _video_files(folder: Path) -> List[Path]:
    return [p for p, is_dir in _walk_entries(folder) if not is_dir and p.suffix.lower() in VIDEO_EXTS]


def _is_series(folder: Path) -> bool:
    for f, is_dir in _walk_entries(folder):
        if not is_dir and f.suffix.lower() in VIDEO_EXTS and EPISODE_RE.search(f.name):
            return True
        if is_dir and SEASON_DIR_RE.fullmatch(f.name.strip()):
            return True
    return False


def _series_episodes(folder: Path) -> Dict[int, List[int]]:
    seasons: Dict[int, set] = {}
    for f in _video_files(folder):
        m = EPISODE_RE.search(f.name)
        if m:
            seasons.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    return {s: sorted(eps) for s, eps in sorted(seasons.items())}


def _missing_episodes(episodes: Dict[int, List[int]]) -> Dict[int, List[int]]:
    result: Dict[int, List[int]] = {}
    for season, eps in episodes.items():
        if eps:
            full = set(range(min(eps), max(eps) + 1))
            missing = sorted(full - set(eps))
            if missing:
                result[season] = missing
    return result


def _is_leaf_folder(entry: Path) -> bool:
    """
    True when this directory is itself a movie or series folder.
    False when it is a grouping/category folder that should be descended into.

    Rules (in order):
      1. Contains at least one video file directly → leaf (movie or flat-episode series).
      2. Contains at least one Season-named subdirectory → leaf (series with season folders).
      3. Otherwise → grouping folder; recurse into it.
    """
    try:
        children = list(entry.iterdir())
    except PermissionError:
        return False
    if any(f.is_file() and f.suffix.lower() in VIDEO_EXTS for f in children):
        return True
    if any(_is_season_dir(f) for f in children):
        return True
    return False


def _movie_item_from_file(vf: Path, loc: str) -> Dict[str, Any]:
    """Create a single movie item from one standalone video file."""
    m = YEAR_RE.search(vf.stem)
    title = _clean_title(vf.stem)
    nfo_path = vf.with_suffix(".nfo")
    nfo = parse_nfo(nfo_path) if nfo_path.exists() else {}
    return {
        "id":          str(vf),
        "title":       nfo.get("title") or title,
        "raw_name":    vf.name,
        "type":        "movie",
        "location":    loc,
        "path":        str(vf.parent),
        "folder":      vf.parent.name,
        "files":       [str(vf)],
        "year":        nfo.get("year") or (m.group(1) if m else ""),
        "nfo":         nfo,
        "nfo_quality": _nfo_quality(nfo),
    }


def _process_leaf(entry: Path, loc: str, items: List[Dict[str, Any]]) -> None:
    if _is_series(entry):
        videos = _video_files(entry)
        if not videos:
            return
        folder_name = entry.name
        nfo_path = entry / "tvshow.nfo"
        nfo = parse_nfo(nfo_path) if nfo_path.exists() else {}
        title = nfo.get("title") or _clean_title(folder_name)
        episodes = _series_episodes(entry)
        missing  = _missing_episodes(episodes)
        # Jellyfin writes per-episode NFOs next to each video; a series-level
        # tvshow.nfo often doesn't exist, so quality is based on episode coverage.
        episode_nfo_count = sum(1 for v in videos if v.with_suffix(".nfo").exists())
        if episode_nfo_count == len(videos):
            quality = "full"
        elif episode_nfo_count or nfo:
            quality = "partial"
        else:
            quality = "none"
        items.append({
            "id":               str(entry),
            "title":            title,
            "raw_name":         folder_name,
            "type":             "series",
            "location":         loc,
            "path":             str(entry),
            "folder":           folder_name,
            "files":            [str(f) for f in videos],
            "episodes":         {str(k): v for k, v in episodes.items()},
            "missing_episodes": {str(k): v for k, v in missing.items()},
            "total_episodes":   sum(len(v) for v in episodes.values()),
            "nfo":              nfo,
            "episode_nfo_count": episode_nfo_count,
            "nfo_quality":      quality,
        })
        return

    # Not a series — check whether there are multiple unrelated movies inside
    direct_videos = sorted(
        [f for f in entry.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS],
        key=lambda f: f.name.lower(),
    )
    if len(direct_videos) > 1:
        # Multi-movie container (e.g. a flat downloads folder) — one item per file
        log.debug("Multi-movie folder %s: %d files → fanning out", entry.name, len(direct_videos))
        for vf in direct_videos:
            items.append(_movie_item_from_file(vf, loc))
    else:
        # Normal single-movie folder
        videos = _video_files(entry)
        if not videos:
            return
        folder_name = entry.name
        title = _clean_title(folder_name)
        nfo_path = next((f for f in entry.iterdir() if f.suffix.lower() == ".nfo" and f.is_file()), None)
        nfo = parse_nfo(nfo_path) if nfo_path else {}
        m = YEAR_RE.search(folder_name)
        items.append({
            "id":          str(entry),
            "title":       nfo.get("title") or title,
            "raw_name":    folder_name,
            "type":        "movie",
            "location":    loc,
            "path":        str(entry),
            "folder":      folder_name,
            "files":       [str(f) for f in videos],
            "year":        nfo.get("year") or (m.group(1) if m else ""),
            "nfo":         nfo,
            "nfo_quality": _nfo_quality(nfo),
        })


def _scan_recursive(folder: Path, loc: str, items: List[Dict[str, Any]],
                    excluded: frozenset = frozenset()) -> None:
    try:
        entries = sorted(folder.iterdir(), key=lambda e: e.name.lower())
    except PermissionError as exc:
        log.warning("Cannot read directory %s: %s", folder, exc)
        return

    # Loose video files sitting directly in this folder — each is its own movie.
    # (These are skipped by the subdir loop below, so we handle them here.)
    for f in entries:
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            items.append(_movie_item_from_file(f, loc))

    for entry in entries:
        if not _is_dir(entry):
            continue
        if os.path.normcase(os.path.normpath(str(entry))) in excluded:
            log.info("Skipping %s — it is a separately configured location.", entry)
            continue
        if _is_leaf_folder(entry):
            _process_leaf(entry, loc, items)
        else:
            log.debug("Descending into grouping folder: %s", entry.name)
            _scan_recursive(entry, loc, items, excluded)


def scan_path(base: Path, loc: str, excluded: frozenset = frozenset()) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not base.is_dir():
        log.warning("Skipping %s location — not a directory: %s", loc, base)
        return items
    log.info("Scanning %s: %s", loc, base)
    _scan_recursive(base, loc, items, excluded)
    log.info("Finished scanning %s: %d item(s) found", loc, len(items))
    return items


def _nested_locations(path: str, all_paths: List[str]) -> frozenset:
    """Normalized paths of other configured locations nested inside `path`."""
    base = os.path.normcase(os.path.normpath(path))
    return frozenset(
        o for o in (os.path.normcase(os.path.normpath(p)) for p in all_paths)
        if o != base and o.startswith(base + os.sep)
    )


def _location_entries(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(path, location_type) for every configured location, incl. the transcode output folder."""
    entries = ([(p, "library") for p in config.get("locations", []) if p]
               + [(p, "download") for p in config.get("downloads", []) if p])
    root = transcoded_root(config)
    conflict = transcoded_root_conflict(config)
    if conflict:
        log.warning("Not scanning the transcode output folder: %s", conflict)
    elif root:
        entries.append((root, "transcoded"))
    return entries


# ── Cache persistence ─────────────────────────────────────────────────────────
def _rel_or_abs(p: str, root: str) -> str:
    """Path relative to root when inside it, otherwise unchanged."""
    try:
        r = os.path.relpath(p, root)
    except ValueError:
        return p
    return p if r.startswith("..") else r


def _item_to_cache(item: Dict[str, Any], root: str) -> Dict[str, Any]:
    """Cache-friendly copy of an item.

    - item id/path stored relative to the location root (id omitted when == path)
    - movies: file entries stored relative to the item's path
    - series: files grouped per season folder as {"path": rel_dir, "files": [names]}
      so file names contain no path delimiters at all
    Paths outside their base (or on another drive) stay absolute.
    """
    base  = item.get("path")
    files = item.get("files")
    out = dict(item)
    if base and files:
        if item.get("type") == "series":
            seasons: Dict[str, List[str]] = {}
            for f in files:
                folder = os.path.dirname(f)
                key = _rel_or_abs(folder, base)
                seasons.setdefault(key, []).append(os.path.basename(f))
            out["seasons"] = [
                {"path": p, "files": sorted(names, key=str.lower)}
                for p, names in sorted(seasons.items(), key=lambda kv: kv[0].lower())
            ]
            out.pop("files", None)
        else:
            out["files"] = [_rel_or_abs(f, base) for f in files]
    if base:
        if out.get("id") == base:
            out.pop("id", None)   # restored from path on load
        elif isinstance(out.get("id"), str):
            out["id"] = _rel_or_abs(out["id"], root)
        out["path"] = _rel_or_abs(base, root)
    return out


def _item_from_cache(item: Dict[str, Any], root: str) -> Dict[str, Any]:
    """Restore an item to the in-memory model: absolute id/path and flat absolute files[].

    Accepts all cache formats: relative id/path/seasons (new) and absolute files (old).
    """
    out  = dict(item)
    base = out.get("path")
    if base is not None and not os.path.isabs(base):
        base = os.path.normpath(os.path.join(root, base))
        out["path"] = base
    if "id" not in out:
        out["id"] = base
    elif isinstance(out["id"], str) and not os.path.isabs(out["id"]):
        out["id"] = os.path.normpath(os.path.join(root, out["id"]))
    seasons = out.pop("seasons", None)
    if seasons is not None and base:
        files: List[str] = []
        for season in seasons:
            sp = season.get("path", ".")
            folder = sp if os.path.isabs(sp) else os.path.normpath(os.path.join(base, sp))
            files.extend(os.path.join(folder, name) for name in season.get("files", []))
        out["files"] = files
        return out
    files = out.get("files")
    if base and files:
        out["files"] = [f if os.path.isabs(f) else os.path.join(base, f) for f in files]
    return out


def _cache_file_for(path: str) -> Path:
    """Map an absolute path to its cache JSON filename, e.g. M:\\Movies → M-Movies.json"""
    safe = re.sub(r'[^\w]', '-', path)
    safe = re.sub(r'-+', '-', safe).strip('-')
    return CACHE_DIR / f"{safe}.json"


def _save_location_cache(path: str, loc_type: str, items: List[Dict], scan_time: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "path":          path,
        "location_type": loc_type,
        "last_scan":     scan_time,
        "items":         [_item_to_cache(i, path) for i in items],
    }
    try:
        cf  = _cache_file_for(path)
        tmp = cf.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cf)
        log.info("Cache saved: %s (%d items)", cf.name, len(items))
    except OSError as exc:
        log.warning("Could not save cache for %s: %s", path, exc)


def _compute_status(lib: List[Dict[str, Any]], dl: List[Dict[str, Any]],
                    tr: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    tr = tr or []
    lib_movies = [i for i in lib if i["type"] == "movie"]
    lib_series = [i for i in lib if i["type"] == "series"]
    dl_movies  = [i for i in dl  if i["type"] == "movie"]
    dl_series  = [i for i in dl  if i["type"] == "series"]
    tr_movies  = [i for i in tr  if i["type"] == "movie"]
    tr_series  = [i for i in tr  if i["type"] == "series"]
    dismissed  = _dismissed_pairs()
    return {
        "library_movies":           len(lib_movies),
        "library_series":           len(lib_series),
        "download_movies":          len(dl_movies),
        "download_series":          len(dl_series),
        "transcoded_movies":        len(tr_movies),
        "transcoded_series":        len(tr_series),
        "total_library_episodes":   sum(i.get("total_episodes", 0) for i in lib_series),
        "total_download_episodes":  sum(i.get("total_episodes", 0) for i in dl_series),
        "total_transcoded_episodes": sum(i.get("total_episodes", 0) for i in tr_series),
        "duplicate_movies":         _find_duplicates(lib_movies + dl_movies + tr_movies, dismissed),
        "duplicate_series":         _find_duplicates(lib_series + dl_series + tr_series, dismissed),
        "missing_episodes": [
            {
                "title":   i["title"],
                "path":    i["path"],
                "missing": i["missing_episodes"],
            }
            for i in lib_series + dl_series + tr_series
            if i.get("missing_episodes")
        ],
    }


def _load_all_caches(config: Dict[str, Any]) -> bool:
    """Load cache files for configured locations. Returns True if at least one was loaded."""
    if not CACHE_DIR.exists():
        return False
    types = {_cache_file_for(p): t for p, t in _location_entries(config)}
    files = sorted(CACHE_DIR.glob("*.json"))
    if not files:
        return False
    lib: List[Dict] = []
    dl:  List[Dict] = []
    tr:  List[Dict] = []
    scan_times: List[str] = []
    loaded = 0
    for cf in files:
        if cf not in types:
            log.info("Ignoring stale cache %s — no matching configured location.", cf.name)
            continue
        try:
            payload = json.loads(cf.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "items" not in payload:
                continue
            root = payload.get("path", "")
            loaded_items = [_item_from_cache(i, root) for i in payload["items"]]
            # The configured role wins over the cached one (a folder may have been reassigned)
            {"download": dl, "transcoded": tr}.get(types[cf], lib).extend(loaded_items)
            if payload.get("last_scan"):
                scan_times.append(payload["last_scan"])
            loaded += 1
        except Exception as exc:
            log.warning("Could not load cache %s: %s", cf.name, exc)
    if loaded == 0:
        return False
    status = _compute_status(lib, dl, tr)
    with _lock:
        _state["library"]    = lib
        _state["downloads"]  = dl
        _state["transcoded"] = tr
        _state["last_scan"]  = max(scan_times) if scan_times else None
        _state["status"]     = status
    log.info("Loaded %d cache file(s): %d library + %d download + %d transcoded item(s)",
             loaded, len(lib), len(dl), len(tr))
    return True


def _all_locations_cached(config: Dict[str, Any]) -> bool:
    """True when every configured *reachable* location has a cache file on disk.

    Unreachable paths (offline drives) are ignored — a rescan cannot produce
    a cache for them anyway, so requiring one would force a scan on every startup.
    """
    paths = [p for p, _ in _location_entries(config)]
    if not paths:
        return False
    missing = [p for p in paths if Path(p).is_dir() and not _cache_file_for(p).exists()]
    for p in missing:
        log.info("No cache yet for %s — startup scan needed.", p)
    return not missing


def run_scan(_ignored_config: Any = None) -> None:
    """Always re-reads config from disk so path changes are picked up without restart."""
    with _lock:
        if _state["scanning"]:
            return
        _state["scanning"] = True
        _state["scan_error"] = None
        _state["scan_info"] = {
            "started":   datetime.now().isoformat(),
            "finished":  None,
            "error":     None,
            "locations": [],
        }

    try:
        config = load_config()   # fresh read every time
        log.info("Scan starting with config: %d location(s), %d download(s)",
                 len(config.get("locations", [])), len(config.get("downloads", [])))

        scan_time = datetime.now().isoformat()

        loc_entries: List[Dict[str, Any]] = []
        for p, loc_type in _location_entries(config):
            loc_entries.append({
                "path":          p,
                "location_type": loc_type,
                "status":        "pending" if Path(p).is_dir() else "missing",
                "items":         None,
            })
        with _lock:
            _state["scan_info"]["locations"] = loc_entries

        lib: List[Dict[str, Any]] = []
        dl:  List[Dict[str, Any]] = []
        tr:  List[Dict[str, Any]] = []
        buckets = {"library": lib, "download": dl, "transcoded": tr}
        all_paths = [e["path"] for e in loc_entries]
        for entry in loc_entries:
            if entry["status"] == "missing":
                continue
            p = entry["path"]
            with _lock:
                _state["current_path"] = p
                entry["status"] = "scanning"
            location_items = scan_path(Path(p), entry["location_type"],
                                       _nested_locations(p, all_paths))
            with _lock:
                entry["status"] = "done"
                entry["items"]  = len(location_items)
            buckets[entry["location_type"]].extend(location_items)
            _save_location_cache(p, entry["location_type"], location_items, scan_time)

        status = _compute_status(lib, dl, tr)

        with _lock:
            _state["library"]    = lib
            _state["downloads"]  = dl
            _state["transcoded"] = tr
            _state["last_scan"] = scan_time
            _state["status"]    = status

    except Exception as exc:
        log.error("Scan failed: %s", exc)
        with _lock:
            _state["scan_error"] = str(exc)
            if _state["scan_info"]:
                _state["scan_info"]["error"] = str(exc)
    finally:
        with _lock:
            _state["scanning"]     = False
            _state["current_path"] = None
            if _state["scan_info"]:
                _state["scan_info"]["finished"] = datetime.now().isoformat()


def refresh_transcoded() -> None:
    """Rescan only the transcode output folder (after a job) and recompute status."""
    config = load_config()
    root = safe_transcoded_root(config)
    with _lock:
        if _state["scanning"]:
            log.info("Full scan in progress — skipping transcoded-folder refresh.")
            return
    items: List[Dict[str, Any]] = []
    if root and Path(root).is_dir():
        all_paths = [p for p, _ in _location_entries(config)]
        items = scan_path(Path(root), "transcoded", _nested_locations(root, all_paths))
        _save_location_cache(root, "transcoded", items, datetime.now().isoformat())
    with _lock:
        _state["transcoded"] = items
    recompute_status()


def recompute_status() -> None:
    """Recompute counts and duplicates from the in-memory item lists (no disk access)."""
    with _lock:
        lib, dl, tr = list(_state["library"]), list(_state["downloads"]), list(_state["transcoded"])
    status = _compute_status(lib, dl, tr)
    with _lock:
        _state["status"] = status
