# Implementation Plan — Reorganize

Source design: [REORGANIZE-DESIGN.md](REORGANIZE-DESIGN.md)

> **Phases 1 and 2 done (2026-09-29), Phase 3 done (2026-10-01), Phases 4 and 5 done (2026-10-04; the user tests the full flow in the browser). Phase 6: risk register and backend test done, dialog verification by the user.** Ground rule (design): nothing is ever changed without the user selecting it and executing with dry run off.
>
> **Operational note:** the UI server does not auto-reload — restart it after every backend phase, or new endpoints 404 and new config keys are silently dropped.

## Status

| Phase | Scope | Status |
| :--- | :--- | :--- |
| 1 | Config & Settings UI (Naming Format) | Done 2026-09-29 |
| 2 | Analysis engine (`py/ui/reorganize.py`) | Done 2026-09-29 |
| 2b | Scanner: see folders whose name ends with a space/dot (Windows) | Done 2026-10-01 |
| 3 | Execution engine + API | Done 2026-10-01 |
| 4 | Reorganize Dialog (frontend) | Done 2026-10-04 |
| 5 | Toolbar button + badge | Done 2026-10-04 |
| 6 | Risk register + verification | Risk register + backend test done 2026-10-04; dialog flow tested by the user |

Each phase is independently shippable; the feature is inert until Phase 5 exposes it.

---

## Phase 1 — Config & Settings UI

**Backend** (`py/ui/settings.py`, `py/ui/models.py`, `py/ui/app.py`):
- `DEFAULT_NAMING` dict (`movie_folder`, `movie_file`, `series_folder`, `file_equals_folder`, `rename_files`, `resolve_nfos` (was `rename_nfo`, decision #13), `delete_images`, `sanitize_names` — values per design) + `naming` key in `DEFAULT_CONFIG`; merge partial `naming:` sections in `load_config()` exactly like `transcode:`. When `file_equals_folder` is true, `movie_file` is forced to `movie_folder` (in `load_config()` and in the `NamingConfig` validator).
- `NamingConfig` pydantic model taking defaults from `DEFAULT_NAMING` (same pattern as `TranscodeConfig`); `ConfigBody.naming: Optional[NamingConfig] = None` so older frontends don't wipe it; `POST /api/config` stores it when present and returns it.
- Helpers in `settings.py`:
  - `expand_naming(template, title, year) -> str | None` — `None` when `{year}` is required but unknown (or the title is empty). The title is trimmed and passed through the Sanitize rule first (decision #5).
  - `sanitize_component(value) -> str` — **the one Sanitize rule** (decision #9): `:` → ` - `, other illegal chars dropped, trailing dots/spaces stripped; nothing else. Used for titles now and for existing names in Phase 2 (applied to the stem for files).
  - `valid_component(name) -> str | None` — returns the error text for separators, `.`/`..`, illegal Windows chars, control chars, trailing dot/space, reserved device names; `fileops._check_name` (Rename) now calls it, so the rules cannot drift apart (decision #6).
  - `naming_error(naming) -> str | None` — template validation used by the save endpoint: must contain `{title}`, only known placeholders, no unmatched braces, sample expansion must pass `valid_component`.

**Frontend** (`web/templates/index.html`, `web/static/js/settings.js`):
- Dedicated **Naming** tab in the Settings modal (decision #2): the three format fields, each with an explanation of the supported placeholders and naming rules directly below it; `rename_files` / `resolve_nfos` / `delete_images` / `sanitize_names` checkboxes, all labelled “Suggest …”, and a highlighted top statement that the settings never change files (decision #11); a “File name = folder name” sync checkbox stored as `file_equals_folder` (disables the file field while checked; decision #7); and a live preview per field (`Alien (1979)`, `Alien (1979).mkv`, `Breaking Bad (2008)`) updated on input.
- The dialog states **exactly what will happen**: the rules box defines the Sanitize rule; the series-folder help says episodes/season folders are never renamed to a format but are offered for sanitizing; the “Sanitize names” help lists the scope (every file and folder in locations + downloads), the stem rule for files, that `SxxEyy` stays intact, that same-stem subtitles/NFOs follow, and that nothing changes until executed in the dialog.
- `loadSettings`/`saveSettings` round-trip the `naming` section; save is refused client-side on template validation errors (mirroring server checks).

**Done when:** config round-trips with old and new frontends, invalid templates are refused with a clear message, preview updates live.

## Phase 2 — Analysis engine (done 2026-09-29)

**New module `py/ui/reorganize.py`** — analysis only, no writes:
- `analyse(item_ids)` — refuses with 409 while a scan runs; copies the requested items out of `state.all_items()` under `_lock`, then `build_proposals(config, items) -> {generated, analysed_count, compliant_count, proposals, parent_folders, blocked}`. Transcoded items are skipped.
- Scope = the item ids sent by the dialog = the items shown by the left-bar filters (decision #12).
- Checks 1–5 per design; title/year per field NFO → `parse_name` (folder name, then video stem; decision #15); case-insensitive comparison against the expanded format.
- `related_files(video, siblings)` — the one **exact-stem** helper (stem, `stem.`, `stem-`; other videos excluded) for Organize to reuse.
- Check 4 — a lone NFO named neither `movie.nfo` nor `<video>.nfo` → `nfo` rename suggestion to `<video>.nfo` (decision #17); several top-level NFOs → `nfos` choice (`use_best` only when a unique best exists, default `leave`); a lone `movie.nfo` / `<video>.nfo` is never touched (decision #13).
- Conflict detection: fresh disk peek plus a shared `planned` map so two suggestions never target the same path; folder conflicts get a `suggested` “(1)” name.
- Blocked reasons: `no_year`, `multi_video`, `stale_cache` (blocks checks 1–5 only), `sanitize_empty`, `episode_changed`.
- Loose Files: operate on the file (`id`), never `item["path"]`.
- **Check 6 — Sanitize** (when `sanitize_names`): walks the item folder recursively (or the Loose File + its sidecars) and collects the folders above the item up to its location/download folder (`parent_folders`, deduplicated). Files by stem, folders by whole name; target = `sanitize_component(stem) + suffix` / `sanitize_component(name)`. Sanitized videos carry their sidecars as `follows`. Entries covered by a format rename are dropped. All disk access goes through `_fs()` (`\\?\` prefix on Windows).

**Route** (`app.py`): `POST /api/reorganize/proposals` `{item_ids}` (model `ReorganizeProposalsBody`) → `reorganize.analyse`.

**Verified** against a temp tree (module functions called directly): compliant items (incl. case-only difference, lone `movie.nfo`), scene-named folder → folder + file rename with `.en.srt` follower, several NFOs → choice with best = full-quality NFO, `Alien`/`Aliens` loose files kept apart, image suggested for deletion, no-year, multi-video, existing-target conflict with `(1)` suggestion, stale cache, series folder rename, episode + `.en.srt` sanitize, `Season 2 ` folder sanitize, sanitize conflict `S01E02 .mkv`/`S01E02.mkv`, grouping folder `Marvel ` in `parent_folders`.

## Phase 2b — Scanner and trailing-space folders (done 2026-10-01)

On Windows `Path.is_dir()` strips a trailing space/dot and returns False, `rglob` does not enter such folders, and `SEASON_DIR_RE.fullmatch("Season 1 ")` failed — items below/inside such folders were missing from the scan. Fixed in `scanner.py`: `_is_dir()` (falls back to `settings.fs_path`, the `\\?\` prefix helper now shared with `reorganize.py`), `_walk_entries()` replaces `rglob` (same order: a folder's own files before sub-folders), season folders match on the stripped name, the movie NFO lookup uses `iterdir`. Verified: `Marvel \Iron Man (2008)`, `Breaking Bad\Season 1 \…`, `Heat (1995) \…` are found and get sanitize/rename suggestions. Follow-up risk R-16 (Move/Rename/Delete on such items may fail safely) registered in APPLICATION-RISKS.md.

## Phase 3 — Execution engine + API

Readiness review 2026-10-01 (decisions #19–#25 below) refined this phase.

**`py/ui/reorganize.py`**:
- `apply_changes(changes, dry_run) -> per-item results`. Refused with 409 while a scan **or a transcode** runs (decision #23).
- **Server re-analysis (decision #20):** for every selected item the server re-runs the per-item analysis (`_analyse_*`) against the current disk. From the client it takes **only the user's choices** — the checkboxes (`apply_folder`, `apply_file`, `apply_nfo`, `delete_images`, selected sanitize paths), the edited `folder_name` / `file_name`, and `nfo_action`. Everything else — move list, sidecars/followers, images, NFO target, sanitize targets — comes from the server's own re-analysis, never from the client. If the re-analysis differs from what the user saw (item now stale/blocked, a selected operation no longer proposed, NFO set changed), that item is refused with the reason.
- Then plan the full operation list (mkdir / moves / renames / deletions) before touching anything — the plan-then-execute style of `fileops.rename_item`, but **not** by calling it (it raises HTTP errors, stops at the first problem and has no `\\?\` support; decision #24).
- Edited names are re-validated with `valid_component`; conflicts are re-checked on disk and across all planned targets of the request.
- Every destination is checked with exists (via `fs_path`) before any `shutil.move`/rename (R-03 rule) — also needed because `rename` overwrites silently on Linux. **Exception:** a rename that only changes letter case of the same path is allowed (same rule as `rename_item`; decision #25). Never overwrite; a mid-item failure stops that item and reports it, already-finished items stay (no rollback).
- **Sanitize execution (decision #21):** a sanitize path is accepted only when the server's re-analysis produced the same sanitize entry for it — for that item, or for the “Parent folders” group (`item_id` null). Any other path is refused. The target is recomputed from the current on-disk name with `sanitize_component` (never a client-supplied name), re-checking conflict/empty/`SxxEyy`; followers are recomputed too. All disk access uses the `\\?\` prefix on Windows (`fs_path`).
- **Order (decision #22):** per item — sanitize renames deepest path first, then the item's format operations (folder create/rename, file rename + followers, NFO, image deletion). **Parent folders run last**, after all items, deepest first, so no item path changes under a pending operation.
- **No in-memory state update (decision #19):** move/rename never updated `_state` either, and item ids are paths. After execution the user rescans (Phase 4 “Rescan now” prompt). Exception: `_update_not_duplicates_paths(old, new)` is called for every item whose id path changes (folder rename/create, loose-file move), so “not a duplicate” marks survive.
- **Result shape (decision #25):** `[{item_id, ok, dry_run, operations: [{op, from, to}], error}]` with `op` ∈ `mkdir | move | rename | delete`; a dry run returns exactly the operations execute would perform, in execution order. `item_id` null = Parent folders.
- **Models** (`models.py`): `ReorganizeChange` (`item_id: Optional` — null for “Parent folders”, `folder_name`, `file_name`, `apply_folder`, `apply_file`, `apply_nfo` (4a rename), `nfo_action: use_best|delete_all|leave = leave`, `nfo_files: List[str]` — the NFO names the user saw, `delete_images`, `sanitize: List[str]` paths) and `ReorganizeBody` (`changes`, `dry_run: bool = True`). NFO actions refuse unless the folder still holds exactly `nfo_files`.
- **Route**: `POST /api/reorganize`.

**Done when:** dry run reports exactly what execute then does; every guard in the design's safety table is enforced server-side and covered in Phase 6 verification.

**As implemented (2026-10-01):**
- `_Planner` checks every operation against a `_View` of the disk (planned adds/removes on top of the real disk), so a dry run over several items also catches two selected changes with the same target. A real run re-reads the disk per item and re-checks each target right before `os.rename` (no `shutil.move` — all moves stay on the same volume).
- Per-item order for movie folders: sanitize (deepest first) → NFO deletions → video rename + followers → NFO rename (4a / *use the best*) → folder rename. Loose files: mkdir → moves → image deletion.
- Edited names are validated as a whole (so `a\b` is refused, not checked as `b`), and a file stem is also validated on its own (`"Name "` + `.mkv` would otherwise pass).
- Strict “no longer proposed” refusals: a selected option with nothing behind it (`delete_images` without images, `apply_nfo` without a 4a proposal, a sanitize path the re-analysis didn't produce) refuses the item. The dialog (Phase 4) must only send what the row shows.
- Result items also carry `kind` per operation (`folder`/`file`) and, after a real run, `done` (operations completed — tells the user how far a failed item got).
- Verified against a temp tree (module functions called directly): edited-name refusals (separator, illegal char, trailing space in stem), foreign sanitize path, parent path not above an item, `nfo_files` mismatch, series file rename, 409 during scan/transcode, case-only rename, dry run leaves the tree identical and lists exactly the operations execute performs, full execute (scene folder + file + NFO + `.en.srt`, lone NFO 4a, *use the best* keeping `movie.nfo`, `Marvel ` parent, episode + sidecar + `Season 1 ` sanitize, loose files with sidecar/poster, `Alien`/`Aliens` separation), not-duplicate marks follow, rescan → re-analysis leaves only the unticked conflict, stale cache refuses format but allows sanitize, sanitize onto an existing file refused, two loose files into one new folder.

## Phase 4 — Reorganize Dialog

**Frontend** (`index.html` modal + `web/static/js/reorganize.js`):
- Modal per the design mock: proposal rows (checkbox, current → **two editable name fields — folder and file** — with a per-row “= folder” sync toggle defaulting to `file_equals_folder` and per-row file-rename disable, operation summary, expander with the exact file operations and the item's sanitize entries, each with its own checkbox, image deletion as its own checkbox, several-NFO choice preset to *leave as is*), a “Parent folders” group for `parent_folders`, conflict rows red + unchecked, collapsed Blocked section with reasons, All/None, dry-run checkbox (default on), Preview/Do it, Close. The dialog sends the ids of `filteredItems` when it opens (and on Refresh).
- Client-side live validation of edited names (single component) and conflict re-check via the proposals data; server re-validates regardless.
- Execution renders per-row results (done/failed + reason); a completion footer offers "Rescan now" (reuse `triggerScan()`). The rescan is required — the server does not update in-memory state (decision #19), so until then the library shows the old names.
- Each change also sends `nfo_files` (the NFO names shown in the row) for the server's NFO re-check (decision #20).
- Follow existing modal conventions (event delegation, `esc()` everywhere user data is interpolated, `_apiErr` for errors).

**Done when:** full flow works against a temp-tree scan: open → edit → select subset → preview → execute → per-row results → rescan prompt.

**As implemented (2026-10-04)** — `reorganizeModal` in `index.html`, `web/static/js/reorganize.js` (was a section of `app.js` before ADR 0004), `.ro-*` styles in `app.css`. Decisions #26–#35 below came out of the user's testing.
- **Rows:** row checkbox + current name (quoted, so trailing spaces show) + kind badge + title/year/source; a line per suggestion with its own checkbox — folder (editable), file stem (editable, extension shown, “= folder” sync), NFO rename (4a, target follows the file name), several NFOs (select, files with quality, ★ best), image deletion; sanitize entries under *Details* (shown directly when they are the only suggestion), plus what follows the video. Conflicts are red and unticked; blocked reasons for the item are shown in the row and in the collapsed *Cannot propose* list.
- **Ticks:** checkboxes carry `data-default` (ticked by default and by All) or `data-conflict` (ticked only as an approval). Image deletion and a file rename that was not suggested are never ticked automatically. A file line for an already correct video is only shown next to a folder field (for the sync). Editing a name ticks it; for a loose file the file rename needs the new folder.
- **Row checkbox (#30):** empty → ticks every suggestion of the row (conflicts approved); partly ticked → completes the row; fully ticked → clears it (and sets the NFO choice back to *leave as is*). The several-NFOs choice is never picked automatically — not by the row checkbox, not by All.
- **Live checks:** edited names mirror `valid_component` (no trimming — a trailing space is an error); ticked folder/file targets are checked against the other ticked rows and the folders/videos of the scanned library (#34). Advisory only.
- **Request:** one change per ticked row (`apply_*` from the ticks, `folder_name`, `file_name` only when the stem differs from the current one, `nfo_action` + `nfo_files`, `sanitize` paths); Parent folders last as `item_id: null`.
- **Preview** (dry run on, default): one request, exact operations listed under each row (paths relative to the row's folder). **Do it** (dry run off): in-dialog confirmation (#26), then one request per row with progress; closing the dialog/page is blocked while it runs; an HTTP error stops the run (the remaining rows show “Not attempted”).
- **Working in steps (#29):** done rows stay ticked, keep their result and are locked (green, “already reorganized”); failed and not-attempted rows stay selectable; rows below a renamed parent folder are unticked and locked (“rescan and refresh first”). All/None, Preview and Do it only touch unlocked rows. The header adds “N done”; **Rescan now** stays in the footer until a scan finishes.
- **Duplicates (#31):** rows whose item is in a duplicate group (`duplicateMap`, not-duplicate marks excluded) get a badge and a hint; a panel below the rows lists them and asks (after every analysis) whether to leave them out of the default selection and All (default) or include them; **Select N duplicate(s)** appears only while they are left out; **Show duplicates** closes the dialog and switches to the Duplicates filter.
- The design mock's location dropdown is not built — the left-bar filters are the scope (#28).

## Phase 5 — Toolbar button + badge

- **Reorganize** button in the header toolbar. The analysis runs **only** when the dialog opens or its Refresh is clicked (decision #1) — no post-scan hook, no background analysis.
- The badge shows the count from the most recent analysis of the session (updated on dialog open/refresh and after an execution); absent before the first analysis. Disabled with a tooltip while a scan is running (analysis would race the cache rebuild).

**Done when:** the button/badge behave as above and no analysis ever fires automatically (verified by watching the server log across a scan).

**As implemented (2026-10-04):** the button sits in the navbar before Rescan (added during Phase 4 so the dialog could be tested, #35). Badge = rows of the last analysis that still have suggestions (done/locked rows not counted), updated only by an analysis or an execution, hidden at 0 and before the first analysis. `setScanBadge` disables the button while a scan runs; the tooltip sits on a wrapper because a disabled button shows none.

## Phase 6 — Risk register + verification

**[APPLICATION-RISKS.md](../APPLICATION-RISKS.md):** add the new operations with their guards (new R-numbers): bulk rename/foldering never overwrites, exact-stem collection, single-component validation, stale-cache refusal, series folder-only (format), sanitize (server-recomputed target, `SxxEyy` unchanged, followers, deepest-first, `\\?\` access), image deletion by design. Any accepted gap goes in as an Open risk.

**Verification** (repo pattern — no test files; real files in a temp dir, config patched in memory):
- Temp dir + `load_config` patched on `reorganize`/`scanner`/`fileops` modules + `scanner.CACHE_DIR` redirected; call module functions and route handlers directly (no httpx/TestClient).
- Cases: the Phase 2 matrix end-to-end (analyse → execute → **rescan** → re-analyse shows compliant; decision #19), plus: dry run touches nothing (tree snapshot identical) and lists the same operations execute performs, conflict refusal, `Alien`/`Aliens` sidecar separation, stale-cache refusal, refusal while a scan/transcode runs, series folder rename (episodes found under the new path after rescan), not-duplicate marks follow a renamed item, case-only rename allowed, name edited in the dialog to something illegal is refused server-side, sanitize of an episode + its `.en.srt` inside a sanitized season folder (deepest first, both end up legal), a sanitize path not produced by the re-analysis is refused, parent folder sanitized after the items below it, NFO action refused when `nfo_files` no longer matches the folder.
- Update the memory/docs notes if any convention emerges (e.g. shared name-validation helper location).

**Done so far (2026-10-04):** risk register R-17 – R-24 added. `py/test/test_reorganize_execution.py` (temp tree in `py/test/_tmp*`, removed afterwards; run with the venv's Python, exit code 0 = pass) covers the backend cases: guards, 409, case-only rename, dry run touches nothing and equals execute, full execute, `Alien`/`Aliens`, not-duplicate marks, rescan → re-analysis, stale cache, sanitize onto an existing file, two changes into one new folder, underscore titles. Still open: the dialog flow (user, in the browser) and the series-after-rescan / parent-folder-after-items cases end-to-end.

## Potential future fixes — REQUIRE A DIALOG WITH THE USER BEFORE ANY CHANGE

Ideas raised during testing that were **deliberately not implemented**. Do not implement any of these without discussing them with the user first.

| # | Raised | Problem | Potential fix (not agreed) |
| :--- | :--- | :--- | :--- |
| F1 | 2026-10-01, Phase 4 testing (row “Doctor Strange (2016)”) | A row shows only the **new** name in its folder/file fields, never the **current** one — the header shows the folder name, the “Rename file” line only the suggested video name. When the suggestion equals the title, the user cannot see what would be renamed (or whether anything would). | Every name line shows *current → new*: `Rename file “Doctor.Strange.2016.mp4” → [Doctor Strange (2016)] .mp4` (same for folders). When the new name equals the current one, show “already correct” instead of a checkbox; a line that only exists for the “= folder” sync and is already correct is hidden; editing a name back to the current one shows “already correct” and sends nothing. |

---

## Decisions (confirmed 2026-09-28)

| # | Question | Decision |
| :--- | :--- | :--- |
| 1 | Analysis trigger | **On demand only** — dialog open/refresh; never automatic (no post-scan hook) |
| 2 | Settings placement | **Dedicated “Naming” tab**, formatting rules explained below each field |
| 3 | Folder vs. file name | **Two fields** in the dialog; “file = folder” sync option; file renaming can be disabled per row and globally (`rename_files`) |
| 4 | Server mode | **No special handling** — Reorganize runs where the files are (backend machine); local shares only affect playing |

## Decisions (confirmed 2026-09-29, Phase 1)

| # | Question | Decision |
| :--- | :--- | :--- |
| 5 | Illegal characters in titles (`Alien: Covenant`, `What If...?`) | **Sanitize the title** before expanding: `:` → ` - `, other illegal chars dropped, trailing dots/spaces stripped (`Alien - Covenant (2017)`). Template literal text is not sanitized — it is validated on save. |
| 6 | Share `valid_component` with Rename | **Yes** — the Rename dialog becomes stricter (also refuses `* ? " < > \|`, control chars, trailing dot/space, reserved device names). |
| 7 | “File name = folder name” persistence | **Config key `naming.file_equals_folder`** (default `true`). It is also the default for the per-row “= folder” toggle in the Reorganize Dialog, which the user can override per row. |
| 8 | Sanitize scope | **Every file and folder of the analysed items** — episodes + their sidecars, season folders, movie files, extras, sidecars, folders — plus the folders above them (amended by #12: scope follows the filters, no “Other files” group). Switch `naming.sanitize_names` (default `true`), explained in the Settings Naming tab (Phase 1). |
| 9 | Sanitize rule | **Exactly decision #5** — one rule for titles and existing names, no extra normalisation (whitespace collapsing removed from `sanitize_component`). Files: applied to the stem, extension kept. |
| 10 | Sidecars of a sanitized video | **Follow the video** (exact-stem rule), keeping their suffix chain. |

## Decisions (confirmed 2026-09-29, Phase 2)

| # | Question | Decision |
| :--- | :--- | :--- |
| 11 | Automatic actions | **None, ever.** Top statement in the Naming tab, “Suggest …” labels, ground rule in the design, first Safety guard. |
| 12 | Analysis start and scope | **Right away when the dialog opens**, on **the items shown by the left-bar filters** only. Sanitize: those items + the folders above them. |
| 13 | NFO naming | **Named after the video, never `movie.nfo`.** A single NFO (incl. Jellyfin's `movie.nfo`) is left alone. Several → use the best (rename to `<video>.nfo`, delete the others) / delete all / leave as is (default). `rename_nfo` → `resolve_nfos`. |
| 14 | Series default | **`{title} ({year})`** |
| 15 | Titles without NFO | **Own parser** (`parse_name`) understanding scene names. |
| 16 | Parts in one folder | **A sub-folder suggestion per file anyway**; the second one becomes a conflict. |
| 17 | Lone NFO with another name (2026-10-01) | **Suggest renaming it to `<video>.nfo`** — Jellyfin ignores it otherwise. |
| 18 | NFO already named `movie.nfo` / `<video>.nfo` (2026-10-01) | **Never renamed** — also not by *use the best* (`nfos.target` = its own name, `rename_best: false`). |

## Decisions (confirmed 2026-10-01, Phase 3 readiness review)

| # | Question | Decision |
| :--- | :--- | :--- |
| 19 | In-memory state after execution | **Not updated** (move/rename don't either; ids are paths). The user rescans via the “Rescan now” prompt. `_update_not_duplicates_paths` is called for every renamed/moved item. |
| 20 | Trusting the client | **Server re-runs the per-item analysis** at execute time and takes only the user's choices (checkboxes, edited folder/file name, NFO action) from the client; everything else is recomputed. Mismatch → that item is refused. `nfo_files` added to the request for the NFO re-check. |
| 21 | Accepted sanitize paths | **Only paths the server's re-analysis produced** for that item / the Parent folders group; anything else is refused. |
| 22 | Execution order | Per item: sanitize deepest first, then format operations. **Parent folders last**, deepest first. |
| 23 | Concurrency | Execution **refused (409) while a scan or a transcode runs**. |
| 24 | Reuse of `rename_item` | **Same plan-then-execute pattern, not a call** — it raises HTTP errors, stops at the first problem and lacks `\\?\` support. |
| 25 | Case-only renames; result shape | Case-only renames of the same path are **allowed** (like `rename_item`). Result: `[{item_id, ok, dry_run, operations: [{op, from, to}], error}]`. |

## Decisions (2026-10-01 – 2026-10-04, Phase 4/5 testing with the user)

| # | Question | Decision |
| :--- | :--- | :--- |
| 26 | Confirming Do it | **Inside the dialog**, never a browser popup: the first click shows the warning in the footer and turns the button into *Confirm (N)*; any change cancels it. |
| 27 | Image deletion default | **Unticked** (design: defaults never pick a destructive option); unticked images move into the new folder. |
| 28 | Location dropdown in the mock | **Not built** — the left-bar filters decide the scope (decision #12). |
| 29 | Working in steps | **Done rows locked for good** (ticks and result kept, nothing selectable until rescan + Refresh); **failed rows selectable again**; rows below a renamed parent folder locked. All/None leave locked rows alone. |
| 30 | Row checkbox | Completes a partly ticked row. **Several NFOs always start at *leave as is* and only the user changes that choice** — neither the row checkbox nor All picks *use the best* / *delete all* (2026-10-04; an earlier version let the row checkbox pick *use the best*). A row whose only suggestion is the NFO choice gets a disabled row checkbox with a tooltip. |
| 36 | Browser popups elsewhere | **Replaced app-wide** by in-dialog confirmations (`confirmInDialog`: first click warns in the dialog and relabels the button, second click within 10 s confirms; closing the dialog or changing its options cancels): *Use transcoded video*, *Remove all* registered duplicates, *Kill transcoding*. The rescan question after *Use transcoded video* is a **Rescan now** button in the result dialog. |
| 31 | Potential duplicates | Rows of items in a duplicate group are marked; a panel asks after every analysis to **leave them out** of the default selection and All (default) or include them; *Select N duplicate(s)* only while left out; *Show duplicates* opens the Duplicates filter. |
| 32 | Underscores in names | `parse_name` turns **every underscore into a space** (it usually replaced a `:`). |
| 33 | “= folder” on conflict rows | **Off by default**, so approving `Name (1)` does not put “(1)” in the video name (to be tried by the user). |
| 37 | Jellyfin NFO pair | **`movie.nfo` + `<video>.nfo` of equal quality is compliant** — Jellyfin's NFO saver writes both (it re-saves after a rename), so offering only *delete all* / *leave as is* was noise (2026-10-20). A different quality, or any other pair, still gets the several-NFOs choice. |
| 34 | Live conflict check | Ticked folder **and file** names are checked against the other ticked rows **and the scanned library**; the server still decides. |
| 35 | Toolbar button | Added in Phase 4 (needed for testing); badge and scan-disable in Phase 5. |
