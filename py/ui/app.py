"""FastAPI app: lifespan and thin route handlers.

Business logic lives in fileops.py, nfo.py, duplicates.py, and scanner.py;
request models live in models.py.
"""
from __future__ import annotations

import copy
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .duplicates import _load_not_duplicates, mark_not_duplicate, remove_not_duplicate_pairs
from .fileops import delete_items, file_info, move_items, play_item, rename_item
from .models import (
    ConfigBody,
    DeleteBody,
    EncoderTestBody,
    MoveBody,
    NfoCopyBody,
    NotDuplicateBody,
    NotDupRemoveBody,
    PlayBody,
    RenameBody,
    ReorganizeProposalsBody,
    TranscodeProbeBody,
    TranscodeStartBody,
    TranscodeToolsBody,
    UseTranscodedBody,
)
from .nfo import copy_nfo, read_nfo
from .reorganize import analyse as reorganize_analyse
from .scanner import _all_locations_cached, _load_all_caches, run_scan
from .settings import (
    STATIC_DIR,
    TEMPLATES_DIR,
    _write_config,
    load_config,
    log,
    naming_error,
    transcoded_root_conflict,
)
from .state import _lock, _state, all_items
from .transcode import (
    detect_tools,
    job_log,
    job_status,
    kill_job,
    probe_items,
    start_job,
    test_encoders,
    use_transcoded,
)


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


@app.post("/api/config")
def api_save_config(body: ConfigBody):
    try:
        cfg = load_config()
    except SystemExit:
        raise HTTPException(500, "Config file is invalid. Check server logs for details.")
    cfg["locations"] = body.locations
    cfg["downloads"] = body.downloads
    if body.transcode is not None:
        cfg["transcode"] = body.transcode.model_dump()
    if body.naming is not None:
        naming = body.naming.model_dump()
        err = naming_error(naming)
        if err:
            raise HTTPException(400, err)
        cfg["naming"] = naming
    if body.server_mode is not None:
        cfg["server_mode"] = body.server_mode
    if body.local_shares is not None:
        cfg["local_shares"] = {k.strip(): v.strip() for k, v in body.local_shares.items()
                               if k.strip() and v.strip()}
    conflict = transcoded_root_conflict(cfg)
    if conflict:
        raise HTTPException(400, conflict)
    _write_config(cfg)
    tc = cfg["transcode"]
    log.info("Config saved: %d location(s), %d download(s), server_mode=%s, %d local share(s); "
             "transcode ffmpeg=%r ffprobe=%r output=%r",
             len(cfg["locations"]), len(cfg["downloads"]),
             cfg.get("server_mode"), len(cfg.get("local_shares") or {}),
             tc.get("ffmpeg_path"), tc.get("ffprobe_path"), tc.get("output_root"))
    return {"ok": True, "transcode": tc, "naming": cfg["naming"],
            "server_mode": cfg["server_mode"], "local_shares": cfg["local_shares"]}


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
        items: List[Dict[str, Any]] = list(all_items())
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


# ── File APIs ──────────────────────────────────────────────────────────────────
@app.get("/api/fileinfo")
def api_fileinfo(item_id: str):
    return file_info(item_id)


@app.post("/api/play")
def api_play(body: PlayBody):
    return play_item(body.item_id, body.file)


@app.post("/api/move")
def api_move(body: MoveBody):
    return move_items(body.item_ids, body.target_base, body.dry_run)


@app.post("/api/rename")
def api_rename(body: RenameBody):
    return rename_item(body.item_id, body.new_name, body.new_folder_name,
                       body.rename_target, body.dry_run)


@app.post("/api/delete")
def api_delete(body: DeleteBody):
    return delete_items(body.item_ids, body.dry_run)


# ── Reorganize API ─────────────────────────────────────────────────────────────
@app.post("/api/reorganize/proposals")
def api_reorganize_proposals(body: ReorganizeProposalsBody):
    return reorganize_analyse(body.item_ids)


# ── NFO API ────────────────────────────────────────────────────────────────────
@app.get("/api/nfo")
def api_nfo(item_id: str):
    return read_nfo(item_id)


@app.post("/api/nfo/copy")
def api_nfo_copy(body: NfoCopyBody):
    return copy_nfo(body.source_id, body.target_id, body.fields)


# ── Not-duplicate API ──────────────────────────────────────────────────────────
@app.post("/api/not-duplicate")
def api_not_duplicate(body: NotDuplicateBody):
    mark_not_duplicate(body.ids)
    return {"ok": True}


@app.get("/api/not-duplicates")
def api_list_not_duplicates():
    return _load_not_duplicates()


@app.post("/api/not-duplicates/remove")
def api_remove_not_duplicates(body: NotDupRemoveBody):
    return remove_not_duplicate_pairs(body.pairs)


# ── Transcode API ────────────────────────────────────────────────────────────────
@app.post("/api/transcode/tools")
def api_transcode_tools(body: TranscodeToolsBody):
    return detect_tools(body.model_dump())


@app.post("/api/transcode/test-encoder")
def api_transcode_test_encoder(body: EncoderTestBody):
    return test_encoders(body.ffmpeg_path, body.encoders, body.qp)


@app.post("/api/transcode/probe")
def api_transcode_probe(body: TranscodeProbeBody):
    return probe_items(body.item_ids)


@app.post("/api/transcode/start")
def api_transcode_start(body: TranscodeStartBody):
    return start_job([f.model_dump() for f in body.files])


@app.post("/api/transcode/kill")
def api_transcode_kill():
    return kill_job()


@app.get("/api/transcode/status")
def api_transcode_status():
    return job_status()


@app.get("/api/transcode/log")
def api_transcode_log(since: int = 0):
    return job_log(since)


@app.post("/api/use-transcoded")
def api_use_transcoded(body: UseTranscodedBody):
    return use_transcoded(body.original_id, body.transcoded_id, body.dry_run)
