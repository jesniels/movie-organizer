# Implementation Plan — Transcoding Feature

Source spec: [TRANSCODING-FEATURE.md](TRANSCODING-FEATURE.md) · Reference: [sample-script.cmd](sample-script.cmd)

## Summary

Add GPU/CPU transcoding to the web UI: configure ffmpeg/ffprobe in Settings, select movies → probe → transcode to a mirrored `<output-root>/Movies|Series` structure as a single background job with live progress, console output, and kill support — then resolve the resulting duplicates via a "Use transcoded video" action in the Duplicates view.


## Decisions (clarified 2026-09-27)

| Topic | Decision |
| :--- | :--- |
| Original file after transcode | **Stays in place** (duplicate workflow; not the script's `-original` move) |
| Output structure | Single configured root with auto `Movies/` and `Series/` subfolders, mirroring source folder structure relative to the source location root |
| Encoder settings | Preset dropdown (`hevc_amf` / `hevc_nvenc` / `libx265`) + QP quality field |
| Audio track | Auto-suggest first clean English track (not commentary/SDH/descriptive/visual); user can override per file in the probe dialog; files with no clean track are flagged and excluded unless the user picks a track manually |
| Series | **Movies only in v1** (sidebar still shows Movies/Series subfolders under the transcoded root) |
| "Use transcoded video" | Renames transcoded file to the original video's filename, replaces the original video only (NFO/images untouched), deletes the transcoded folder; dry-run checkbox, checked by default |

## Hard requirements (from spec)

- Only **one** transcoding job can run at a time — enforced in the backend (409) and in the UI.
- Transcoding cannot start without probing first.
- Progress/status indicator at the top; clicking it opens a console-output dialog with a **Kill** button.
- Transcoded root appears in the sidebar below "Library" and "Downloads" **only when non-empty**.
- Kill/cancel deletes partial output files (per [ADR 0003](../adr/0003-cancel-no-rollback-delete-partial-files.md)).
- Destructive operations support dry-run (repo convention).

---

## Phase 1 — Config & Settings

**Backend**
- `py/ui/settings.py`: extend `DEFAULT_CONFIG` with:
  ```yaml
  transcode:
    ffmpeg_path: ""
    ffprobe_path: ""
    output_root: ""
    encoder: hevc_amf
    qp: 22
  ```
- `py/ui/models.py`: extend `ConfigBody` with an optional `transcode` section.
- `py/ui/app.py`: `POST /api/config` persists the transcode section (merge — must not drop it).
- New `py/ui/transcode.py`: `detect_tools()` — search `PATH` (`shutil.which`) plus common install dirs (e.g. `d:\tools\ffmpeg\bin`); exposed as `GET /api/transcode/tools`.

**Frontend**
- `web/templates/index.html`: new **Transcoding** tab in the settings modal: ffmpeg/ffprobe path inputs + "Detect" button, output root input, encoder dropdown, QP number input.
- `web/static/app.js`: `loadSettings` / `saveSettings` handle the new fields.

## Phase 2 — Probe + transcode engine (`py/ui/transcode.py`)

**State** (`py/ui/state.py`, mutate keys only — never rebind `_state`):
```python
_state["transcode"] = {
    "running": False, "killed": False, "error": None,
    "started": None, "finished": None,
    "files": [],      # [{item_id, src, dst, audio_index, status, progress}]
    "current": None,  # index into files
    "log": [],        # ring buffer (~2000 lines)
}
```
File `status` ∈ `pending | running | done | failed | skipped`.

**Probe** — `POST /api/transcode/probe {item_ids}`:
- Runs ffprobe per selected movie video file: audio streams (index, language, title, dispositions), format duration/size, video codec.
- Auto-suggests the clean-English track (port the script's logic: language `eng`, reject `description/descriptive/commentary/sdh/visual` in title/disposition).
- Returns a per-file report for the UI.

**Start** — `POST /api/transcode/start {files: [{item_id, audio_index}]}`:
- Returns **409** if a job is already running (single-job guard under `_lock`).
- Spawns one worker thread for the whole job (same pattern as the scanner thread).
- Destination: `output_root/Movies/<relpath of item folder vs its source location root>/<filename>`; creates dirs; refuses if destination exists.
- ffmpeg command per file (from the sample script):
  `-v error -i <src> -c:v <encoder> <quality-args> -map 0:v:0 -map 0:<audio_index> -map 0:s:? -c:a copy -c:s copy <dst>` plus `-progress pipe:1 -nostats`.
  Quality args per encoder: `hevc_amf` → `-rc 1 -qp_i QP -qp_p QP`; `hevc_nvenc` → `-rc constqp -qp QP`; `libx265` → `-crf QP`.
- Progress: parse `out_time_ms` from the progress pipe against the ffprobe duration → per-file %; overall = `(done + current fraction) / total`.
- stderr is captured into the log ring buffer.
- On job end: rescan/refresh the transcoded root and recompute duplicates so new items appear.

**Control/status endpoints**
- `GET /api/transcode/status` — running flag, per-file statuses, overall %, current file.
- `GET /api/transcode/log` — full log buffer.
- `POST /api/transcode/kill` — kill the ffmpeg process, mark job killed, **delete the partial output file** (ADR 0003).

All routes stay thin in `app.py`; logic lives in `transcode.py`; request models in `models.py`.

## Phase 3 — Transcode UI

- **Transcode button** in the bottom-left `#selection-info` panel (alongside Move/Delete): visible for movie-only selections; disabled while a job is running or when the selection contains series/transcoded items.
- **Transcode modal**:
  - Shows configured options read-only (encoder, QP, output root) with a hint to change them in Settings.
  - Mandatory **Probe files** step — Start is disabled until probing completes.
  - Probe results table per file: filename, video codec, duration, audio-track dropdown (suggested track preselected; flagged files preselect nothing and are excluded unless the user picks a track).
  - **Start** → `POST /api/transcode/start`; a 409 shows a toast.
- **Navbar badge** next to `#scan-badge`: hidden when idle, shows status + overall % while running (piggybacks on the existing polling loop).
- **Console modal** (click the badge): live log (polls `/api/transcode/log` while open), per-file progress list, red **Kill transcoding** button with confirmation.

## Phase 4 — Transcoded root as a scanned location

- `py/ui/scanner.py`: when `transcode.output_root` is configured and non-empty on disk, scan it as a new location type `transcoded` (its `Movies/` and `Series/` subtrees), with a cache file like other locations.
- `py/ui/state.py` / `app.py`: third state list `_state["transcoded"]`; adjust `api_library`, `resolve_item`, `_compute_status` accordingly.
- `web/static/app.js`: add a **Transcoded** group to `_LOC_GROUPS`, rendered below Library and Downloads **only when it contains items**, with Movies and Series sub-entries (automatic).

## Phase 5 — Duplicates integration

- Transcoded copies group naturally with originals via `_clean_title` in `py/ui/duplicates.py`; flag groups (or items) that involve a transcoded item (`transcoded: true`).
- Duplicates view toolbar: **"Select transcoded"** button next to "Select all" — selects only duplicates caused by transcoding.
- Compare modal: when the pair is original + transcoded, show **"Use transcoded video"** at the bottom with a warning confirmation and dry-run checkbox (default checked).
- New `POST /api/use-transcoded {original_id, transcoded_id, dry_run}`:
  1. Locate the original's video file and the transcoded video file.
  2. Replace the original video (transcoded file renamed to the original's basename); NFO and images untouched.
  3. Delete the transcoded folder (or file, if loose).
  4. Update in-memory state and `recompute_duplicates()`.
  - Dry-run returns the planned actions (same result-list pattern as `delete_items` / `move_items`).

## Phase 6 — Tests & verification

- New test file at repo root (style of `test_normalize.py`) covering pure logic — no ffmpeg needed:
  - Audio-track selection heuristic (clean-English detection, rejection keywords).
  - Destination-path mapping (source root → `output_root/Movies/...`).
- Import check: `.\venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'py'); from ui.app import app"`.
- Run tests with the venv interpreter.
- Manual UI verification is done by the user (agents do not start the UI).

---

## Files touched

| File | Change |
| :--- | :--- |
| `py/ui/transcode.py` | **New** — tool detection, probe, job engine, kill, use-transcoded operation |
| `py/ui/settings.py` | `transcode` config section defaults |
| `py/ui/models.py` | New request models + `ConfigBody` extension |
| `py/ui/app.py` | Thin routes for the new endpoints |
| `py/ui/state.py` | `transcode` job state + `transcoded` items list |
| `py/ui/scanner.py` | Scan transcoded root as `transcoded` location type |
| `py/ui/duplicates.py` | Flag transcode-caused duplicate groups |
| `web/templates/index.html` | Settings tab, transcode modal, console modal, navbar badge, sidebar group, duplicates buttons |
| `web/static/app.js` | All corresponding frontend logic |
| `test_transcode_logic.py` | **New** — pure-logic tests |

## Out of scope (v1)

- Series transcoding (episodes)
- Parallel/multiple simultaneous jobs
- Queue persistence across server restarts
- The script's scan-only "outlier report" mode
- Moving originals to an `-original` folder
