#!/usr/bin/env python3
"""
Movie Organizer Web UI — FastAPI backend.
Run from project root:  python py/movie-organizer-ui.py
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("movie-organizer")

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
CONFIG_PATH   = BASE_DIR / "web" / "config.yaml"
CACHE_DIR     = BASE_DIR / "web" / "cache"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR    = BASE_DIR / "web" / "static"
NOT_DUP_PATH  = BASE_DIR / "web" / "not-duplicates.json"

# ── Constants ──────────────────────────────────────────────────────────────────
VIDEO_EXTS     = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
EPISODE_RE     = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
SEASON_DIR_RE  = re.compile(r"^(?:season[\s_.-]*\d+|s\d{1,2})$", re.IGNORECASE)
YEAR_RE        = re.compile(r"\((\d{4})\)")
QUALITY_STRIP  = re.compile(
    r"\s*(1080p|720p|2160p|4[Kk]|HDR|BluRay|WEB[-.]DL|WEBRip|HEVC|x264|x265|REMUX).*$",
    re.IGNORECASE,
)

DEFAULT_CONFIG: Dict[str, Any] = {"locations": [], "downloads": []}


# ── Config ─────────────────────────────────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_config(DEFAULT_CONFIG)
        log.info("Created default config at %s", CONFIG_PATH)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        # Show exactly where the problem is — no stacktrace noise
        mark = getattr(exc, 'problem_mark', None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem  = getattr(exc, 'problem', str(exc))
        log.error("Invalid YAML in %s%s: %s", CONFIG_PATH, location, problem)
        log.error("Fix the config file and restart the server.")
        sys.exit(1)
    except OSError as exc:
        log.error("Cannot read config file %s: %s", CONFIG_PATH, exc)
        sys.exit(1)
    if not isinstance(raw, dict):
        log.error("Config file %s must be a YAML mapping, got %s.", CONFIG_PATH, type(raw).__name__)
        sys.exit(1)
    return {**DEFAULT_CONFIG, **raw}


def _write_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)
    except OSError as exc:
        log.error("Cannot write config file %s: %s", CONFIG_PATH, exc)
        raise


# ── NFO parsing ────────────────────────────────────────────────────────────────
def parse_nfo(nfo_path: Path) -> Dict[str, Any]:
    try:
        root = ET.parse(nfo_path).getroot()
        uniqueids: Dict[str, str] = {}
        # Jellyfin's NFO saver writes <imdbid>/<tmdbid>/<tvdbid>/<id>; <uniqueid> is Kodi-style
        for tag, t in (("imdbid", "imdb"), ("imdb_id", "imdb"), ("tmdbid", "tmdb"), ("tvdbid", "tvdb")):
            v = root.findtext(tag)
            if v and v.strip() and t not in uniqueids:
                uniqueids[t] = v.strip()
        for uid in root.findall("uniqueid"):
            t = uid.get("type", "")
            if uid.text and t and t not in uniqueids:
                uniqueids[t] = uid.text
        if not uniqueids:
            v = root.findtext("id")
            if v and v.strip().startswith("tt"):
                uniqueids["imdb"] = v.strip()
        actors: List[str] = []
        for actor in root.findall("actor"):
            name = actor.findtext("name")
            if name:
                actors.append(name)
        return {
            "title":     root.findtext("title") or "",
            "year":      root.findtext("year") or "",
            "plot":      root.findtext("plot") or "",
            "rating":    root.findtext("rating") or "",
            "genre":     [g.text for g in root.findall("genre") if g.text],
            "studio":    root.findtext("studio") or "",
            "tagline":   root.findtext("tagline") or "",
            "uniqueids": uniqueids,
            "actors":    actors,
        }
    except Exception:
        return {}


def _nfo_quality(nfo: Dict[str, Any]) -> str:
    """Classify NFO completeness for Jellyfin recognition.

    - 'none'    : no NFO file (empty dict)
    - 'partial' : NFO present but missing year or a unique external ID
    - 'full'    : NFO has at least year + unique ID (tmdb/imdb/tvdb etc.)
    """
    if not nfo:
        return "none"
    if nfo.get("year") and nfo.get("uniqueids"):
        return "full"
    return "partial"


# ── Scanner helpers ────────────────────────────────────────────────────────────
def _video_files(folder: Path) -> List[Path]:
    return [f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in VIDEO_EXTS]


def _is_series(folder: Path) -> bool:
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS and EPISODE_RE.search(f.name):
            return True
        if f.is_dir() and SEASON_DIR_RE.fullmatch(f.name):
            return True
    return False


def _series_episodes(folder: Path) -> Dict[int, List[int]]:
    seasons: Dict[int, set] = {}
    for f in folder.rglob("*"):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
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


def _clean_title(name: str) -> str:
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name)
    name = QUALITY_STRIP.sub("", name)
    return name.strip()


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
    if any(f.is_dir() and SEASON_DIR_RE.fullmatch(f.name) for f in children):
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
            "nfo_quality":      _nfo_quality(nfo),
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
        nfo_path = next(entry.glob("*.nfo"), None)
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
        if not entry.is_dir():
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


# ── Not-duplicate pairs (user-dismissed duplicate warnings) ────────────────────
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


# ── Duplicate detection ────────────────────────────────────────────────────────
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


# ── Safety helpers ─────────────────────────────────────────────────────────────
def _path_under_base(path: Path, bases: List[str]) -> bool:
    """Return True if path is a subdirectory of at least one configured base."""
    for base in bases:
        if not base:
            continue
        try:
            path.relative_to(Path(base))
            return True
        except ValueError:
            continue
    return False


# ── Cache persistence ─────────────────────────────────────────────────────────
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
        "items":         items,
    }
    try:
        cf  = _cache_file_for(path)
        tmp = cf.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(cf)
        log.info("Cache saved: %s (%d items)", cf.name, len(items))
    except OSError as exc:
        log.warning("Could not save cache for %s: %s", path, exc)


def _compute_status(lib: List[Dict[str, Any]], dl: List[Dict[str, Any]]) -> Dict[str, Any]:
    lib_movies = [i for i in lib if i["type"] == "movie"]
    lib_series = [i for i in lib if i["type"] == "series"]
    dl_movies  = [i for i in dl  if i["type"] == "movie"]
    dl_series  = [i for i in dl  if i["type"] == "series"]
    dismissed  = _dismissed_pairs()
    return {
        "library_movies":           len(lib_movies),
        "library_series":           len(lib_series),
        "download_movies":          len(dl_movies),
        "download_series":          len(dl_series),
        "total_library_episodes":   sum(i.get("total_episodes", 0) for i in lib_series),
        "total_download_episodes":  sum(i.get("total_episodes", 0) for i in dl_series),
        "duplicate_movies":         _find_duplicates(lib_movies + dl_movies, dismissed),
        "duplicate_series":         _find_duplicates(lib_series + dl_series, dismissed),
        "missing_episodes": [
            {
                "title":   i["title"],
                "path":    i["path"],
                "missing": i["missing_episodes"],
            }
            for i in lib_series + dl_series
            if i.get("missing_episodes")
        ],
    }


def _load_all_caches(config: Dict[str, Any]) -> bool:
    """Load cache files for configured locations. Returns True if at least one was loaded."""
    if not CACHE_DIR.exists():
        return False
    paths = [p for p in config.get("locations", []) + config.get("downloads", []) if p]
    expected = {_cache_file_for(p) for p in paths}
    files = sorted(CACHE_DIR.glob("*.json"))
    if not files:
        return False
    lib: List[Dict] = []
    dl:  List[Dict] = []
    scan_times: List[str] = []
    loaded = 0
    for cf in files:
        if cf not in expected:
            log.info("Ignoring stale cache %s — no matching configured location.", cf.name)
            continue
        try:
            payload = json.loads(cf.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "items" not in payload:
                continue
            if payload.get("location_type") == "download":
                dl.extend(payload["items"])
            else:
                lib.extend(payload["items"])
            if payload.get("last_scan"):
                scan_times.append(payload["last_scan"])
            loaded += 1
        except Exception as exc:
            log.warning("Could not load cache %s: %s", cf.name, exc)
    if loaded == 0:
        return False
    status = _compute_status(lib, dl)
    with _lock:
        _state["library"]   = lib
        _state["downloads"] = dl
        _state["last_scan"] = max(scan_times) if scan_times else None
        _state["status"]    = status
    log.info("Loaded %d cache file(s): %d library + %d download item(s)",
             loaded, len(lib), len(dl))
    return True


def _all_locations_cached(config: Dict[str, Any]) -> bool:
    """True when every configured *reachable* location has a cache file on disk.

    Unreachable paths (offline drives) are ignored — a rescan cannot produce
    a cache for them anyway, so requiring one would force a scan on every startup.
    """
    paths = [p for p in config.get("locations", []) + config.get("downloads", []) if p]
    if not paths:
        return False
    missing = [p for p in paths if Path(p).is_dir() and not _cache_file_for(p).exists()]
    for p in missing:
        log.info("No cache yet for %s — startup scan needed.", p)
    return not missing


# ── App state ──────────────────────────────────────────────────────────────────
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
        for p, loc_type in (
            [(p, "library") for p in config.get("locations", [])]
            + [(p, "download") for p in config.get("downloads", [])]
        ):
            if not p:
                continue
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
            (lib if entry["location_type"] == "library" else dl).extend(location_items)
            _save_location_cache(p, entry["location_type"], location_items, scan_time)

        status = _compute_status(lib, dl)

        with _lock:
            _state["library"]   = lib
            _state["downloads"] = dl
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


# ── FastAPI ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()   # validate config on startup — exits with a clear message if broken
    cached = _load_all_caches(config)
    if cached and _all_locations_cached(config):
        log.info("All locations cached — startup scan skipped. Use Rescan to refresh.")
    else:
        if cached:
            log.info("Some locations have no cache — serving cached data while background scan runs.")
        else:
            log.info("No cache found — UI will update once the initial scan completes.")
        t = threading.Thread(target=run_scan, daemon=True, name="scanner")
        t.start()
        log.info("Background scanner started.")
    log.info("Open http://localhost:8998")
    yield


app = FastAPI(title="Movie Organizer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ── Config API ─────────────────────────────────────────────────────────────────
@app.get("/api/config")
def api_get_config():
    try:
        return load_config()
    except SystemExit:
        raise HTTPException(500, "Config file is invalid. Check server logs for details.")


class ConfigBody(BaseModel):
    locations: List[str]
    downloads: List[str]


@app.post("/api/config")
def api_save_config(body: ConfigBody):
    _write_config({"locations": body.locations, "downloads": body.downloads})
    return {"ok": True}


# ── Scan API ───────────────────────────────────────────────────────────────────
@app.post("/api/scan")
def api_trigger_scan(background: BackgroundTasks):
    background.add_task(run_scan)   # run_scan re-reads config itself
    return {"ok": True}


@app.get("/api/scan/status")
def api_scan_status():
    with _lock:
        return {
            "scanning":     _state["scanning"],
            "last_scan":    _state["last_scan"],
            "error":        _state["scan_error"],
            "current_path": _state["current_path"],
            "scan_info":    copy.deepcopy(_state["scan_info"]),
        }


# ── Library API ────────────────────────────────────────────────────────────────
@app.get("/api/library")
def api_library(
    type: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
):
    with _lock:
        items: List[Dict[str, Any]] = list(_state["library"]) + list(_state["downloads"])
    if type:
        items = [i for i in items if i["type"] == type]
    if location:
        items = [i for i in items if i["location"] == location]
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i["title"].lower() or ql in i["folder"].lower()
        ]
    return items


@app.get("/api/status")
def api_status():
    with _lock:
        return dict(_state["status"])


# ── Action helpers ─────────────────────────────────────────────────────────────
def _resolve_item(item_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        for i in _state["library"] + _state["downloads"]:
            if i["id"] == item_id:
                return i
    return None


def _recompute_duplicates() -> None:
    """Refresh duplicate lists in status from in-memory items (no rescan)."""
    dismissed = _dismissed_pairs()
    with _lock:
        items  = _state["library"] + _state["downloads"]
        movies = [i for i in items if i["type"] == "movie"]
        series = [i for i in items if i["type"] == "series"]
        if _state["status"]:
            _state["status"]["duplicate_movies"] = _find_duplicates(movies, dismissed)
            _state["status"]["duplicate_series"] = _find_duplicates(series, dismissed)


# ── File info API ──────────────────────────────────────────────────────────────
@app.get("/api/fileinfo")
def api_fileinfo(item_id: str):
    item = _resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    out = []
    for f in item["files"]:
        p = Path(f)
        try:
            st = p.stat()
            out.append({
                "path":  f,
                "name":  p.name,
                "size":  st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except OSError:
            out.append({"path": f, "name": p.name, "size": None, "mtime": None})
    return out


# ── Play API ───────────────────────────────────────────────────────────────────
class PlayBody(BaseModel):
    item_id: str
    file:    Optional[str] = None   # must be one of the item's files


@app.post("/api/play")
def api_play(body: PlayBody):
    item = _resolve_item(body.item_id)
    if not item:
        raise HTTPException(404, "Not found")
    if body.file:
        if body.file not in item["files"]:
            raise HTTPException(400, "File does not belong to this item")
        target = body.file
    elif item["files"]:
        target = item["files"][0]
    else:
        raise HTTPException(400, "Item has no video files")
    if not Path(target).is_file():
        raise HTTPException(404, f"File not found on disk: {target}")
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        raise HTTPException(500, f"Could not launch player: {exc}")
    return {"ok": True, "file": target}


# ── NFO copy API ───────────────────────────────────────────────────────────────
def _find_nfo_path(item: Dict[str, Any]) -> Optional[Path]:
    if item["type"] == "series":
        p = Path(item["path"]) / "tvshow.nfo"
        return p if p.exists() else None
    if item["id"] != item["path"]:   # loose file item
        p = Path(item["id"]).with_suffix(".nfo")
        return p if p.exists() else None
    return next(Path(item["path"]).glob("*.nfo"), None)


@app.get("/api/nfo")
def api_nfo(item_id: str):
    """Return the raw NFO file content for an item (pretty-printed if valid XML)."""
    item = _resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    nfo_path = _find_nfo_path(item)
    if not nfo_path:
        raise HTTPException(404, "No NFO file found for this item")
    try:
        raw = nfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(500, f"Could not read NFO: {exc}")
    content = raw
    try:
        root = ET.fromstring(raw)
        ET.indent(root, space="  ")
        content = ET.tostring(root, encoding="unicode")
    except ET.ParseError:
        pass  # not clean XML (e.g. trailing URL) — show the raw file
    return {"path": str(nfo_path), "content": content}


class NfoCopyBody(BaseModel):
    source_id: str
    target_id: str
    fields:    Optional[List[str]] = None   # None/empty → copy whole file


# Maps UI field names to NFO XML element tags
_NFO_FIELD_TAGS = {
    "title":     "title",
    "year":      "year",
    "plot":      "plot",
    "rating":    "rating",
    "genre":     "genre",
    "studio":    "studio",
    "tagline":   "tagline",
    "uniqueids": "uniqueid",
    "actors":    "actor",
}


def _merge_nfo_fields(src_nfo: Path, dst_nfo: Path, fields: List[str], default_root: str) -> None:
    """Replace only the selected elements in the target NFO with the source's."""
    src_root = ET.parse(src_nfo).getroot()
    dst_root: Optional[ET.Element] = None
    if dst_nfo.exists():
        try:
            dst_root = ET.parse(dst_nfo).getroot()
        except ET.ParseError:
            dst_root = None   # unparsable target → start fresh
    if dst_root is None:
        dst_root = ET.Element(default_root)
    for field in fields:
        tag = _NFO_FIELD_TAGS[field]
        for el in dst_root.findall(tag):
            dst_root.remove(el)
        for el in src_root.findall(tag):
            dst_root.append(copy.deepcopy(el))
    try:
        ET.indent(dst_root)
    except AttributeError:
        pass   # Python < 3.9
    ET.ElementTree(dst_root).write(dst_nfo, encoding="utf-8", xml_declaration=True)


@app.post("/api/nfo/copy")
def api_nfo_copy(body: NfoCopyBody):
    src_item = _resolve_item(body.source_id)
    dst_item = _resolve_item(body.target_id)
    if not src_item or not dst_item:
        raise HTTPException(404, "Item not found")
    if src_item["type"] != dst_item["type"]:
        raise HTTPException(400, "Cannot copy NFO between a movie and a series")
    if body.fields:
        unknown = [f for f in body.fields if f not in _NFO_FIELD_TAGS]
        if unknown:
            raise HTTPException(400, f"Unknown NFO fields: {', '.join(unknown)}")
    src_nfo = _find_nfo_path(src_item)
    if not src_nfo:
        raise HTTPException(400, "Source item has no NFO file")
    if dst_item["type"] == "series":
        dst_nfo = Path(dst_item["path"]) / "tvshow.nfo"
    else:
        if not dst_item["files"]:
            raise HTTPException(400, "Target item has no video files")
        dst_nfo = Path(dst_item["files"][0]).with_suffix(".nfo")
    try:
        if body.fields:
            default_root = "tvshow" if dst_item["type"] == "series" else "movie"
            _merge_nfo_fields(src_nfo, dst_nfo, body.fields, default_root)
        else:
            shutil.copyfile(str(src_nfo), str(dst_nfo))
    except ET.ParseError as exc:
        raise HTTPException(500, f"Source NFO is not valid XML: {exc}")
    except OSError as exc:
        raise HTTPException(500, f"NFO copy failed: {exc}")
    # Update in-memory item so the UI reflects the change immediately
    nfo = parse_nfo(dst_nfo)
    with _lock:
        for i in _state["library"] + _state["downloads"]:
            if i["id"] == dst_item["id"]:
                i["nfo"] = nfo
                i["nfo_quality"] = _nfo_quality(nfo)
                break
    log.info("NFO copied (%s): %s → %s",
             ", ".join(body.fields) if body.fields else "whole file", src_nfo, dst_nfo)
    return {"ok": True, "from": str(src_nfo), "to": str(dst_nfo),
            "fields": body.fields or "all"}


# ── Not-duplicate API ──────────────────────────────────────────────────────────
class NotDuplicateBody(BaseModel):
    ids: List[str]


@app.post("/api/not-duplicate")
def api_not_duplicate(body: NotDuplicateBody):
    if len(body.ids) != 2:
        raise HTTPException(400, "Exactly two item ids required")
    pairs = _load_not_duplicates()
    key = sorted(body.ids)
    if key not in pairs:
        pairs.append(key)
        _save_not_duplicates(pairs)
        log.info("Marked as not-duplicate: %s ↔ %s", key[0], key[1])
    _recompute_duplicates()
    return {"ok": True}


@app.get("/api/not-duplicates")
def api_list_not_duplicates():
    return _load_not_duplicates()


class NotDupRemoveBody(BaseModel):
    pairs: List[List[str]]


@app.post("/api/not-duplicates/remove")
def api_remove_not_duplicates(body: NotDupRemoveBody):
    existing  = _load_not_duplicates()
    to_remove = {tuple(sorted(p)) for p in body.pairs if len(p) == 2}
    remaining = [p for p in existing if tuple(p) not in to_remove]
    removed   = len(existing) - len(remaining)
    if removed:
        _save_not_duplicates(remaining)
        _recompute_duplicates()
        log.info("Removed %d not-duplicate registration(s)", removed)
    return {"ok": True, "removed": removed, "remaining": len(remaining)}


# ── Move API ───────────────────────────────────────────────────────────────────
class MoveBody(BaseModel):
    item_ids:    List[str]
    target_base: str
    dry_run:     bool = True


@app.post("/api/move")
def api_move(body: MoveBody):
    if not body.target_base.strip():
        raise HTTPException(400, "target_base must not be empty")

    results = []
    for item_id in body.item_ids:
        item = _resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        src = Path(item["path"])
        dst = Path(body.target_base) / src.name
        if body.dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "from": str(src), "to": str(dst)})
        else:
            try:
                shutil.move(str(src), str(dst))
                _update_not_duplicates_paths(str(src), str(dst))
                results.append({"id": item_id, "ok": True,  "from": str(src), "to": str(dst)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
    return results


# ── Rename API ─────────────────────────────────────────────────────────────────
class RenameBody(BaseModel):
    item_id:         str
    new_name:        str
    new_folder_name: Optional[str] = None   # used when rename_target=="both" to allow different folder vs file names
    rename_target:   str = "folder"         # "folder" | "files" | "both"
    dry_run:         bool = True


@app.post("/api/rename")
def api_rename(body: RenameBody):
    if not body.new_name.strip():
        raise HTTPException(400, "new_name must not be empty")
    if body.rename_target not in ("folder", "files", "both"):
        raise HTTPException(400, "rename_target must be folder, files, or both")
    item = _resolve_item(body.item_id)
    if not item:
        raise HTTPException(404, "Not found")

    changes: List[Dict[str, str]] = []
    folder_dst: Optional[Path] = None

    # —— folder rename ——————————————————————————————————————
    if body.rename_target in ("folder", "both"):
        src = Path(item["path"])
        folder_name = (body.new_folder_name or body.new_name) if body.rename_target == "both" else body.new_name
        dst = src.parent / folder_name
        changes.append({"type": "folder", "from": str(src), "to": str(dst)})
        if not body.dry_run:
            try:
                src.rename(dst)
                folder_dst = dst
            except Exception as exc:
                raise HTTPException(500, f"Folder rename failed: {exc}")

    # —— file rename ——————————————————————————————————————
    if body.rename_target in ("files", "both"):
        # If folder was already renamed, files are now under the new folder path
        video_files = sorted(
            [Path(f) for f in item["files"] if Path(f).suffix.lower() in VIDEO_EXTS],
            key=lambda f: f.name.lower(),
        )
        if folder_dst is not None:
            video_files = [folder_dst / f.name for f in video_files]

        if len(video_files) == 1:
            vf = video_files[0]
            new_file = vf.parent / (body.new_name + vf.suffix)
            changes.append({"type": "file", "from": str(vf), "to": str(new_file)})
            if not body.dry_run:
                try:
                    vf.rename(new_file)
                except Exception as exc:
                    raise HTTPException(500, f"File rename failed: {exc}")
        else:
            for i, vf in enumerate(video_files, 1):
                new_file = vf.parent / f"{body.new_name} - Part {i}{vf.suffix}"
                changes.append({"type": "file", "from": str(vf), "to": str(new_file)})
                if not body.dry_run:
                    try:
                        vf.rename(new_file)
                    except Exception as exc:
                        raise HTTPException(500, f"File rename failed: {exc}")

    return {"ok": True, "dry_run": body.dry_run, "changes": changes}


# ── Delete API ─────────────────────────────────────────────────────────────────
class DeleteBody(BaseModel):
    item_ids: List[str]
    dry_run:  bool = True


@app.post("/api/delete")
def api_delete(body: DeleteBody):
    config = load_config()
    all_bases = config.get("locations", []) + config.get("downloads", [])

    results = []
    for item_id in body.item_ids:
        item = _resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        path = Path(item["path"])
        # Safety: only delete paths that originated from a configured location
        if not _path_under_base(path, all_bases):
            results.append({"id": item_id, "ok": False, "error": "Path is outside configured locations"})
            continue
        if body.dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "path": str(path)})
        else:
            try:
                shutil.rmtree(str(path))
                results.append({"id": item_id, "ok": True,  "path": str(path)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
    return results


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8998)
