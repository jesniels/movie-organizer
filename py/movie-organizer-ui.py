#!/usr/bin/env python3
"""
Movie Organizer Web UI — FastAPI backend.
Run from project root:  python py/movie-organizer-ui.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
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
        for uid in root.findall("uniqueid"):
            t = uid.get("type", "")
            if uid.text and t:
                uniqueids[t] = uid.text
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


def _scan_recursive(folder: Path, loc: str, items: List[Dict[str, Any]]) -> None:
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
        if _is_leaf_folder(entry):
            _process_leaf(entry, loc, items)
        else:
            log.debug("Descending into grouping folder: %s", entry.name)
            _scan_recursive(entry, loc, items)


def scan_path(base: Path, loc: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not base.is_dir():
        log.warning("Skipping %s location — not a directory: %s", loc, base)
        return items
    log.info("Scanning %s: %s", loc, base)
    _scan_recursive(base, loc, items)
    log.info("Finished scanning %s: %d item(s) found", loc, len(items))
    return items


# ── Duplicate detection ────────────────────────────────────────────────────────
def _find_duplicates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
    return [{"title": k, "items": v} for k, v in seen.items() if len(v) > 1]


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


def _load_all_caches() -> bool:
    """Load all per-location cache files. Returns True if at least one was loaded."""
    if not CACHE_DIR.exists():
        return False
    files = sorted(CACHE_DIR.glob("*.json"))
    if not files:
        return False
    lib: List[Dict] = []
    dl:  List[Dict] = []
    scan_times: List[str] = []
    loaded = 0
    for cf in files:
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
    with _lock:
        _state["library"]   = lib
        _state["downloads"] = dl
        _state["last_scan"] = max(scan_times) if scan_times else None
    log.info("Loaded %d cache file(s): %d library + %d download item(s)",
             loaded, len(lib), len(dl))
    return True


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
}


def run_scan(_ignored_config: Any = None) -> None:
    """Always re-reads config from disk so path changes are picked up without restart."""
    with _lock:
        if _state["scanning"]:
            return
        _state["scanning"] = True
        _state["scan_error"] = None

    try:
        config = load_config()   # fresh read every time
        log.info("Scan starting with config: %d location(s), %d download(s)",
                 len(config.get("locations", [])), len(config.get("downloads", [])))

        scan_time = datetime.now().isoformat()

        lib: List[Dict[str, Any]] = []
        for p in config.get("locations", []):
            if p and Path(p).is_dir():
                with _lock:
                    _state["current_path"] = p
                location_items = scan_path(Path(p), "library")
                lib.extend(location_items)
                _save_location_cache(p, "library", location_items, scan_time)

        dl: List[Dict[str, Any]] = []
        for p in config.get("downloads", []):
            if p and Path(p).is_dir():
                with _lock:
                    _state["current_path"] = p
                location_items = scan_path(Path(p), "download")
                dl.extend(location_items)
                _save_location_cache(p, "download", location_items, scan_time)

        lib_movies = [i for i in lib if i["type"] == "movie"]
        lib_series = [i for i in lib if i["type"] == "series"]
        dl_movies  = [i for i in dl  if i["type"] == "movie"]
        dl_series  = [i for i in dl  if i["type"] == "series"]

        status: Dict[str, Any] = {
            "library_movies":           len(lib_movies),
            "library_series":           len(lib_series),
            "download_movies":          len(dl_movies),
            "download_series":          len(dl_series),
            "total_library_episodes":   sum(i.get("total_episodes", 0) for i in lib_series),
            "total_download_episodes":  sum(i.get("total_episodes", 0) for i in dl_series),
            "duplicate_movies":         _find_duplicates(lib_movies + dl_movies),
            "duplicate_series":         _find_duplicates(lib_series + dl_series),
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

        with _lock:
            _state["library"]   = lib
            _state["downloads"] = dl
            _state["last_scan"] = scan_time
            _state["status"]    = status

    except Exception as exc:
        log.error("Scan failed: %s", exc)
        with _lock:
            _state["scan_error"] = str(exc)
    finally:
        with _lock:
            _state["scanning"]     = False
            _state["current_path"] = None


# ── FastAPI ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()   # validate config on startup — exits with a clear message if broken
    cached = _load_all_caches()
    if cached:
        log.info("Serving cached data while background scan runs.")
    else:
        log.info("No cache found — UI will update once the initial scan completes.")
    t = threading.Thread(target=run_scan, daemon=True, name="scanner")
    t.start()
    log.info("Background scanner started. Open http://localhost:8080")
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
