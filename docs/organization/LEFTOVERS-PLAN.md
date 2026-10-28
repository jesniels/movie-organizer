# Implementation Plan — Leftover Folders

Source design: [README → Leftover Folders](README.md#leftover-folders-designed-2026-10-28-not-built-yet) · term: [CONTEXT.md](CONTEXT.md) (Leftover Folder) · API, models, settings: [solution-design.md](solution-design.md#leftover-folders-api-endpoints) · risk: [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) R-29

> **Ground rule (design):** the scan only lists leftover folders. Nothing is deleted unless the user ticks the folder, turns dry run off and confirms inside the dialog. Never `rmtree`: files are deleted one by one, folders with `rmdir` deepest first.
>
> **Operational note:** the UI server does not auto-reload — restart it after every backend phase, or new endpoints 404 and new config keys are silently dropped. Phase 2 adds a `leftovers` list to the cache: after it, **Rescan** once.
>
> **Before Phase 4** read [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) (R-29 is the requirement list). Tests use real files only in `py/test/_tmp*` and run with `venv\Scripts\python.exe`.

## Status

| Phase | Scope | Status |
| :--- | :--- | :--- |
| 1 | Config `leftover:` + Settings tab | Not started |
| 2 | Detection during the scan + cache + read API | Not started |
| 3 | Check after an action (Move, Delete, Reorganize, Use transcoded video) | Not started |
| 4 | Delete API with on-disk re-check (R-29) | Not started |
| 5 | UI: sidebar row, dialog, "Review…" links | Not started |

Each phase is independently shippable; nothing is visible to the user until Phase 5, except the Settings tab (Phase 1).

**Independent of the [Bundles plan](BUNDLES-PLAN.md).** Two hooks connect them, wired by whichever plan lands second: bundle moves call the after-action check (Phase 3 here), and the bundle dialogs show the "Review…" link (Phase 5 here). Organize is not built yet — its post-operation "Review…" link comes with Organize.

---

## Phase 1 — Config and Settings tab

**Backend** (`py/ui/settings.py`, `py/ui/models.py`, `py/ui/app.py`):
- `DEFAULT_LEFTOVER` with the six lists (`jellyfin_files`, `subtitle_files`, `ignored_files`, `jellyfin_folders`, `thumbnail_folders`, `system_folders`; defaults in the README) + `leftover` key in `DEFAULT_CONFIG`; `load_config()` merges a missing or partial section like `transcode:` and `naming:`.
- `LeftoverConfig` (each list `List[str]`); `ConfigBody.leftover: Optional[LeftoverConfig] = None` so an older frontend leaves it unchanged; `POST /api/config` stores it and refuses any entry that is or matches a video extension (`VIDEO_EXTS`).
- Matching helpers (documented, typed): file entry → whole name or, starting with a dot, the extension (case-insensitive); folder entry → folder name with `*` wildcard.

**Frontend** (`web/templates/index.html`, `web/static/js/settings.js`): Settings tab **"Leftover folders"** — one comma-separated field per list, short explanation per list, **Reset to defaults**; save refused client-side for video extensions (mirrors the server).

**Tests:** new `py/test/test_leftover_settings.py` — defaults merged into a config without `leftover:`, partial section completed, video extension refused, matching helpers (`Thumbs.db`, `.JPG`, `*.trickplay`).

**Verify:** restart; the tab shows the defaults, saving round-trips, `.mkv` in a list is refused. Run `py/test/test_static_js.py`.

---

## Phase 2 — Detection during the scan

**Backend** — new module `py/ui/leftovers.py` (documented, typed):
- `classify_folder(path, lists, configured_paths) -> Optional[dict]` — reads one folder tree from disk and returns `{path, files, label}` when it is a leftover folder by the README rules, else `None`: no video anywhere below; only listed files (any non-video file inside a `thumbnail_folders` folder); no `system_folders` inside; not a configured location and not containing one (library, download, transcode output; enabled or not); `jellyfin_folders` only set the label. `files` relative to the folder, `ignored_files` flagged hidden.
- `py/ui/scanner.py`: while walking, every grouping folder (and a folder `_is_leaf_folder` calls a series but `_process_leaf` yields no item for — the emptied series shell) is checked; only the **topmost** leftover of a tree is kept. Folders inside a folder that produced an item are never checked. Transcode output folder: not scanned for leftovers.
- Cache: a `leftovers` list per location next to the items, `path` relative to the location root, `files` relative to the folder; absolute on load. Old caches load as `[]`. Kept in `_state["leftovers"]` (`py/ui/state.py`).
- `GET /api/leftovers` (thin route).

**Tests:** new `py/test/test_leftovers.py` (detection part) — the *Leftover folders* detection cases in [solution-design.md → Testing](solution-design.md#modules-to-test): topmost only; video below, unknown file, `system_folders` entry, inside a movie or series folder, being/containing a configured location → not listed; emptied series shell → listed; `@eaDir` inside a leftover → part of it, inside a movie folder → not listed; cache round trip.

**Verify:** restart, **Rescan**, `GET /api/leftovers` lists a known emptied download folder.

---

## Phase 3 — Check after an action

**Backend:**
- `leftovers.check_after_action(changed_paths) -> List[dict]` — for each changed item's source parent, walks up to its location root with `classify_folder`, keeps the topmost leftover, adds new ones to `_state["leftovers"]` (no duplicates). A few folders, no full scan; not written to the cache (the next scan does that).
- Called after a real run of Move (`fileops.move_items`), Delete (`fileops.delete_items`), Reorganize (`reorganize.apply_changes`) and *Use transcoded video*; each result gains `leftovers: N` (the folders this action left behind) for the "Review…" link. Bundle moves call it once the Bundles plan has Phase 4.

**Tests:** in `py/test/test_leftovers.py` — moving the last movie out of a common folder with a `folder.jpg` → that folder reported; a parent that still holds a video → nothing; dry run → nothing.

---

## Phase 4 — Delete (R-29)

**Backend** (`py/ui/leftovers.py`, `py/ui/models.py`, `py/ui/app.py`):
- `POST /api/leftovers/delete` with `LeftoverDeleteBody` (`folders: [{path, files}]`, `dry_run = True`). 409 while a scan runs.
- Per folder, **re-check on disk** with the current settings — every R-29 rule: inside an enabled location, not the location itself, not (containing) a configured folder, no ancestor up to the location root produces an item (holds a video, or is a series with episodes — checked on disk, never from the cached items), no `system_folders`, no video below, only listed files, and **exactly the `files` the user saw**. Any difference → that folder refused with the reason.
- Then delete the listed files one by one and the folders with `rmdir` deepest first; anything unexpected stops that folder (reported). Dry run returns the same operations without touching anything. Remove deleted folders from `_state["leftovers"]`.

**Tests:** in `py/test/test_leftovers.py` (delete part) — content differing from the sent list → refused; a file added in the meantime → folder stays; a video copied in → refused; inside a movie folder (on disk) → refused; a configured location → refused; `@eaDir` with `SYNOINDEX_MEDIA_INFO` → deleted with its parent; dry run touches nothing.

**Done when:** tests pass; R-29 in APPLICATION-RISKS moved from *Planned* to **Guarded** with the test name.

---

## Phase 5 — UI

**Frontend** — new `web/static/js/leftovers.js`, added to `UI_SCRIPTS` in `py/ui/app.py` (ADR 0004; its own `DOMContentLoaded`, unique top-level names); markup in `web/templates/index.html`:
- Sidebar row **Leftover folders (N)** below the special filters (not a filter); refreshed with the library.
- Dialog as in the README: content per row with the label, expand to the full file list, **nothing ticked by default**, All/None, dry run by default with **Back to …**, in-dialog confirmation, per-folder result. Sends exactly the file list it showed. No browser popups.
- **"N leftover folders — Review…"** in the result dialogs of Move, Delete and Reorganize when the result reports `leftovers > 0` (bundle dialogs once the Bundles plan has Phase 5).

**Tests:** `py/test/test_static_js.py` passes.

**Verify (user, in the browser):** move the last movie out of a folder → "Review…" appears → dry run → real delete; a folder with an extra `.txt` is not listed; a list change in Settings applies at the next scan.

**Done when:** user has tested the flows; this plan's status table and the "not built yet" note in the [README](README.md) are updated.
