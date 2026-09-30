# Implementation Plan — Transcoding Feature

Source spec: [TRANSCODING-FEATURE.md](TRANSCODING-FEATURE.md) · Reference: [sample-script.cmd](sample-script.cmd)

## Summary

Add GPU/CPU transcoding to the web UI: configure ffmpeg/ffprobe in Settings, select movies → probe → transcode to `<output-root>\Movies\<movie folder>\` as a single background job with live progress, console output, and kill support — then resolve the resulting duplicates via a "Use transcoded video" action in the Duplicates view.

## Status (2026-09-27)

| Phase | Status |
| :--- | :--- |
| 1 — Config & Settings | **Done** (incl. Detect/Verify, encoder test, audio preferences, field help) |
| 2 — Probe + transcode engine | **Done** (post-job refresh of the transcoded root moved to Phase 4) |
| 3 — Transcode UI | **Done** |
| 4 — Transcoded root as scanned location | **Done** |
| 5 — Duplicates integration | **Done** |
| 6 — Verification | **Done** — end-to-end run against real ffmpeg in a temp folder (see Phase 6) |

> **Operational note:** the UI server does not auto-reload. After any backend change the server must be restarted, otherwise new endpoints return 404 and new config fields are silently dropped by the old code.


## Decisions (clarified 2026-09-27)

| Topic | Decision |
| :--- | :--- |
| Original file after transcode | **Stays in place** (duplicate workflow; not the script's `-original` move) |
| Output structure | Single configured root with auto `Movies/` and `Series/` subfolders. A movie goes to `<output_root>\Movies\<movie folder name>\<file stem>.mkv` (no grouping/location folders mirrored). A loose file goes into its own folder named after the file stem |
| Default output root | **Empty** — user must set it in Settings; transcoding is blocked until it is set |
| Output container | Always `.mkv` regardless of source extension |
| Encoder settings | Preset dropdown (`hevc_amf` / `hevc_nvenc` / `libx265`) + quality field (QP for GPU encoders, CRF for libx265). Default **22** — a sensible default (same as the sample script), recommended range 20–24; explained in tooltips and a per-encoder hint line |
| Tool paths | Entered paths are **verified** (file or folder; quotes from "Copy as path" stripped) by running `-version` and checking the output identifies ffmpeg/ffprobe. Only non-working/empty paths trigger a search of PATH and common install folders |
| Encoder check | **Test encoder** / **Test all** buttons encode 10 generated frames to the null muxer with the form's values — proves encoder + GPU/driver work on the server machine; nothing is written to disk. Failures show a plain reason (no NVIDIA/AMD GPU/driver, encoder not in build) plus ffmpeg's first lines |
| Audio track | Auto-suggest the first clean track (not commentary/SDH/descriptive/visual) in the preferred language; user can override per file in the probe dialog; files with no clean track are flagged and excluded unless the user picks a track manually. **Configurable** in Settings → Transcoding: `audio_languages` (priority list, default `eng`; 2- and 3-letter codes are equivalent), `audio_reject_words` (title words to skip), `audio_prefer` (`first` / `default` / `channels` when several tracks match) |
| Series | **Movies only in v1** (sidebar still shows Movies/Series subfolders under the transcoded root) |
| "Use transcoded video" | Renames transcoded file to the original video's filename, replaces the original video only (NFO/images untouched), deletes the transcoded folder; dry-run checkbox, checked by default. Output is `.mkv`, so an original with another extension (e.g. `.mp4`) is **deleted** and `<stem>.mkv` takes its place. Afterwards the user is **asked** whether to run a full rescan; "no" keeps the in-memory update only |
| "Select transcoded" | **Toggle**: limits the Duplicates view to groups caused by transcoding **and** selects the transcoded copies; toggling off clears the selection |

## Hard requirements (from spec)

- Only **one** transcoding job can run at a time — enforced in the backend (409) and in the UI.
- Transcoding cannot start without probing first.
- Progress/status indicator at the top; clicking it opens a console-output dialog with a **Kill** button.
- Transcoded root appears in the sidebar below "Library" and "Downloads" **only when non-empty**.
- Kill/cancel deletes partial output files (per [ADR 0003](../adr/0003-cancel-no-rollback-delete-partial-files.md)).
- Destructive operations support dry-run (repo convention).
- Data-loss safeguards for transcoding (output-folder overlap, never deleting an existing output, verifying the transcoded file before "Use transcoded video") are tracked in [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) (R-07, R-08, R-10, R-11).

---

## Phase 1 — Config & Settings ✅

**Config** (`web/config.yaml`, defaults in `DEFAULT_TRANSCODE` in `py/ui/settings.py` — the single source of default values):
```yaml
transcode:
  ffmpeg_path: ""
  ffprobe_path: ""
  output_root: ""
  encoder: hevc_amf
  qp: 22
  audio_languages: [eng]
  audio_reject_words: [description, descriptive, described, commentary, sdh, visual]
  audio_prefer: first          # first | default | channels
```
- `load_config()` merges a partial `transcode:` section with `DEFAULT_TRANSCODE`.
- `POST /api/config` merges into the existing config (other keys are kept), logs what was saved, and returns the stored `transcode` section; the UI warns if it is missing (stale server).

**Models** (`py/ui/models.py`): `Encoder` and `AudioPrefer` are defined **once** as `Literal` types and reused; `TranscodeConfig` takes its defaults from `DEFAULT_TRANSCODE` (no repeated literals).

**Tool detection / verification** — `POST /api/transcode/tools {ffmpeg_path, ffprobe_path}` → per tool `source: verified | detected | missing` (+ path, version). Logged server-side.

**Encoder test** — `POST /api/transcode/test-encoder {ffmpeg_path, encoders[], qp}` → per encoder `ok`, `seconds`, `reason`, `error`. Logged server-side.

**Frontend** (Settings → Transcoding tab): ffmpeg/ffprobe paths + **Detect / Verify**; output folder; encoder + quality with ⓘ tooltips, per-encoder hint and "Use 22"; **Test encoder** / **Test all**; audio section (languages, tie-break rule, skip words) with tooltips. Save validates QP 0–51 and a non-empty language list. Error messages name the endpoint and HTTP status; a 404 explains that the server is older than the page.

## Phase 2 — Probe + transcode engine (`py/ui/transcode.py`) ✅

**State** (`py/ui/state.py`, mutate keys only — never rebind `_state`):
```python
_state["transcode"] = {            # None until the first job
    "running": bool, "killed": bool, "error": str | None,
    "started": iso, "finished": iso | None, "current": int | None,
    "encoder": str, "qp": int,
    "files": [{item_id, title, src, dst, audio_index, duration,
               status, progress, speed, error}],
}
```
File `status` ∈ `pending | running | done | failed | killed | cancelled | skipped`.
The console log is a module-level ring buffer (2000 lines) with a monotonically increasing sequence number so clients fetch only new lines; the ffmpeg process handle and probe cache are module-level too (not serialisable).

**Probe** — `POST /api/transcode/probe {item_ids}`:
- ffprobe (JSON) per movie video file: audio streams (index, language, title, codec, channels, dispositions), duration, size, first non-cover-art video stream, subtitle count.
- Suggested audio track = `suggest_audio(audio, audio_languages, audio_reject_words, audio_prefer)`: for each preferred language in order, candidates are tracks in that language (2/3-letter aliases, or untagged with the language name in the title) that are not rejected by title word or by commentary/hearing-impaired/visual-impaired disposition; tie-break by `audio_prefer`.
- Returns per-file results incl. `dst` / `dst_exists`, plus the audio config used.

**Start** — `POST /api/transcode/start {files: [{item_id, src, audio_index}]}`:
- 400 if ffmpeg/output folder not configured, series selected, file not part of the item, file **not probed this session**, or unknown audio stream.
- **409** if a job is running (check-and-set under `_lock`).
- One daemon worker thread per job; files run sequentially. Destination exists → file `skipped`.
- ffmpeg command:
  `-hide_banner -nostdin -n -v error -nostats -progress pipe:1 -i <src> -map 0:v:0 -map 0:<audio_index> -map 0:s? -c:v <encoder> <quality-args> -c:a copy -c:s copy <dst.mkv>`
  Quality args: `hevc_amf` → `-rc 1 -qp_i QP -qp_p QP`; `hevc_nvenc` → `-rc constqp -qp QP`; `libx265` → `-crf QP`.
- Progress from `out_time_us`/`out_time_ms` (both microseconds) vs probed duration; a progress line is written to the log every 10 s; stderr goes to the log.
- Failed or killed file → partial output deleted and its empty folder removed (ADR 0003).

**Control/status**
- `GET /api/transcode/status` — job state + `overall` (mean progress of non-skipped files) + `log_seq`.
- `GET /api/transcode/log?since=N` — lines after sequence N.
- `POST /api/transcode/kill` — 409 if nothing runs; kills ffmpeg, remaining files become `cancelled`.

All routes stay thin in `app.py`; logic lives in `transcode.py`; request models in `models.py`.

## Phase 3 — Transcode UI ✅

- **Transcode button** in the bottom-left `#selection-info` panel (alongside Move/Delete): disabled while a job runs ("Transcoding in progress…") or when the selection contains series ("Transcode (movies only)").
- **Transcode modal**:
  - Shows configured options read-only (encoder, quality, output folder, audio rule) and warns if ffmpeg/ffprobe/output folder are not configured (Probe disabled).
  - Mandatory **Probe files** step — Start is disabled until probing completes.
  - Probe table per file: name + destination, video codec/resolution, duration, size, subtitle count, audio-track dropdown (suggested track preselected; flagged files and existing destinations preselect "skip").
  - **Start** → `POST /api/transcode/start`; a 409 shows a toast.
- **Navbar badge** next to `#scan-badge`: hidden until a job has run; shows `Transcoding N% (x/y)` while running, then done/failed/killed. Own 1.5 s polling loop while running; toast on completion.
- **Console modal** (click the badge): per-file status + progress bars, live log (incremental via `since`), red **Kill transcoding** button with confirmation.

## Phase 4 — Transcoded root as a scanned location ✅

- `py/ui/settings.py`: `transcoded_root(config)` — the cleaned output folder ('' when unset).
- `py/ui/scanner.py`: `_location_entries(config)` is the single list of `(path, location_type)` incl. the output folder as `transcoded`; used by `run_scan`, `_load_all_caches` (the configured role decides the bucket) and `_all_locations_cached`. The output folder gets a cache file like any location. `refresh_transcoded()` rescans only the output folder; `recompute_status()` recomputes counts/duplicates from memory. Status has `transcoded_movies/series` and `total_transcoded_episodes`.
- `py/ui/state.py`: `_state["transcoded"]` + `all_items()` (library + downloads + transcoded, caller holds `_lock`) — used by `resolve_item`, `api_library`, duplicates and NFO copy.
- `py/ui/fileops.py`: Delete also accepts paths inside the output folder.
- `py/ui/transcode.py`: job start creates `Movies\` and `Series\`; after a job with ≥1 done file the worker calls `refresh_transcoded()` before reporting "not running"; transcoded items cannot be probed/transcoded again.
- `web/static/app.js`: **Transcoded** group in the location tree, shown only when it has items, children `Movies` / `Series` (short names, full path in tooltip); sidebar status rows for transcoded movies/series (only when > 0); location badges/labels centralised in `locBadgeHtml` / `_locLabel` (`primary` colour for Transcoded); scan popover icon; lists reload when a job finishes with done files; Transcode button disabled for transcoded items.

## Phase 5 — Duplicates integration ✅

- `py/ui/duplicates.py`: every duplicate group carries `transcoded: true|false` (any copy in the `transcoded` location).
- Duplicates toolbar: **Select transcoded** (next to Select all, visible only in the Duplicates view when such groups exist) — toggle filter + selection (see Decisions).
- Compare modal footer: **Use transcoded video** when the group has exactly one transcoded copy and ≥1 original movie. Opens a red confirmation dialog: transcoded file(s), "Replace the video of" dropdown (preselected by matching file stems), warning text, dry-run checkbox (checked). A real run asks once more, then offers a full rescan.
- `POST /api/use-transcoded {original_id, transcoded_id, dry_run}` (`use_transcoded` in `transcode.py`):
  1. Guards: both items exist; one original (library/download) + one transcoded; movies only; 409 while a job runs; original must be under configured locations; transcoded folder item must be at least `<root>\Movies\<movie>` deep.
  2. Pairing (`pair_videos`): transcoded file ↔ original file by path relative to the item folder without extension (loose originals: file stem). Unmatched or ambiguous → 400. A different existing `<stem>.mkv` in the original folder → 400.
  3. Per pair: move transcoded file to `<new>.partial` next to the original (copies across drives), `os.replace` to `<stem>.mkv`, delete the original if its extension differed. Failure → partial removed, 500.
  4. Delete the transcoded folder; update the original's `files` in memory, drop the transcoded item, `recompute_status()`.
  - Dry-run returns `changes`: `replace {from,to}`, `delete {path}`, `delete-folder {path}`.

## Phase 6 — Verification

- The pure-logic unit-test file was removed by the user and is **not** to be recreated.
- Verification is done against the real system: call the actual route handlers / module functions with the real ffmpeg (`d:\tools\ffmpeg\bin`) and the user's paths, using a temporary copy of `config.yaml` when saving is involved. Verified so far: config save/reload, Detect/Verify (quoted path, folder path, invalid path), probe of a real movie, encoder test (`hevc_amf` ✔, `hevc_nvenc` ✖ no NVIDIA GPU, `libx265` ✔), model validation.
- Phases 4–5 end-to-end (temp folder, config patched in memory, real ffmpeg, `libx265`): generated 2 s test movie + NFO → probe (English track suggested) → job `done` → `Movies\`+`Series\` created → transcoded folder refreshed (1 transcoded item, duplicate group with `transcoded: true`) → use-transcoded dry-run lists replace/delete/delete-folder and changes nothing → real run leaves `<stem>.mkv` + `movie.nfo` in the original folder, `.mp4` and transcoded folder gone, in-memory files updated, no duplicates left.
- Import check: `.\venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'py'); from ui.app import app"`.
- Manual UI verification is done by the user (agents do not start the UI) — always after restarting the server.

## Single source of truth

| What | Defined in |
| :--- | :--- |
| Default config values | `DEFAULT_TRANSCODE` (`py/ui/settings.py`) |
| Allowed encoders / audio tie-break values (API validation) | `Encoder` / `AudioPrefer` in `py/ui/models.py` |
| Per-encoder ffmpeg quality arguments | `ENCODER_QUALITY_ARGS` (`py/ui/transcode.py`) |
| Encoder choices shown in the UI (also used by "Test all") | `#tc-encoder` options in `web/templates/index.html` |

Adding an encoder = `Encoder` literal + `ENCODER_QUALITY_ARGS` entry + dropdown option (+ hint text in `app.js`).

## API overview

| Method | Route | Purpose |
| :--- | :--- | :--- |
| POST | `/api/transcode/tools` | Verify entered ffmpeg/ffprobe paths, search for missing ones |
| POST | `/api/transcode/test-encoder` | Test encoders on the server machine |
| POST | `/api/transcode/probe` | Probe selected movies, suggest audio tracks |
| POST | `/api/transcode/start` | Start the (single) transcode job |
| POST | `/api/transcode/kill` | Kill the running job |
| GET | `/api/transcode/status` | Job + per-file status |
| GET | `/api/transcode/log?since=N` | Console log lines after N |
| POST | `/api/use-transcoded` | Replace an original's video with its transcoded copy (dry-run by default) |

---

## Files touched

| File | Change | Status |
| :--- | :--- | :--- |
| `py/ui/transcode.py` | **New** — tool verify/detect, encoder test, probe, audio selection, job engine, kill, log, use-transcoded | Done |
| `py/ui/settings.py` | `DEFAULT_TRANSCODE` + merge in `load_config`; `transcoded_root()` | Done |
| `py/ui/models.py` | `Encoder`/`AudioPrefer` types, `TranscodeConfig`, tool/encoder-test/probe/start/use-transcoded bodies | Done |
| `py/ui/app.py` | Thin transcode routes; config save merges + logs; library includes transcoded items | Done |
| `py/ui/state.py` | `transcode` job state, `transcoded` items list, `all_items()` | Done |
| `py/ui/scanner.py` | `_location_entries`, `transcoded` location type, `refresh_transcoded`, `recompute_status` | Done |
| `py/ui/duplicates.py` | `transcoded` flag on groups; uses `all_items()` | Done |
| `py/ui/nfo.py`, `py/ui/fileops.py` | `all_items()`; Delete allowed inside the output folder | Done |
| `web/templates/index.html` | Settings tab, transcode/console/use-transcoded modals, navbar badge, Select transcoded, compare footer button | Done |
| `web/static/app.js`, `web/static/app.css` | Corresponding frontend logic; `dot-primary` | Done |

## Out of scope (v1)

- Series transcoding (episodes)
- Parallel/multiple simultaneous jobs
- Queue persistence across server restarts
- The script's scan-only "outlier report" mode
- Moving originals to an `-original` folder
