# Implementation Plan — Reorganize

Source design: [REORGANIZE-DESIGN.md](REORGANIZE-DESIGN.md)

> **Phases 1 and 2 done (2026-09-29).** Phases 3–6 not started. Ground rule (design): nothing is ever changed without the user selecting it and executing with dry run off.
>
> **Operational note:** the UI server does not auto-reload — restart it after every backend phase, or new endpoints 404 and new config keys are silently dropped.

## Status

| Phase | Scope | Status |
| :--- | :--- | :--- |
| 1 | Config & Settings UI (Naming Format) | Done 2026-09-29 |
| 2 | Analysis engine (`py/ui/reorganize.py`) | Done 2026-09-29 |
| 2b | Scanner: see folders whose name ends with a space/dot (Windows) | Not started — found in Phase 2, needs approval |
| 3 | Execution engine + API | Not started |
| 4 | Reorganize Dialog (frontend) | Not started |
| 5 | Toolbar button + badge | Not started |
| 6 | Risk register + verification | Not started |

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

**Frontend** (`web/templates/index.html`, `web/static/app.js`):
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

## Phase 2b — Scanner and trailing-space folders (found in Phase 2, not started)

On Windows `Path.is_dir()` strips a trailing space/dot and returns False, so `_scan_recursive` skips such folders, and `SEASON_DIR_RE.fullmatch("Season 1 ")` fails. Items below such folders are missing from the scan and therefore from the analysis. Fix idea: use `os.scandir` `DirEntry.is_dir()` (no re-stat) in the scanner and match season folders on the stripped name. Needs the user's go-ahead (touches scanning and cached data).

## Phase 3 — Execution engine + API

**`py/ui/reorganize.py`**:
- `apply_changes(changes, dry_run) -> per-item results`. For each change, in order: re-validate name (`valid_component`), re-check stale cache, re-check conflicts, then plan the full operation list (mkdir / moves / renames / deletions) before touching anything — same plan-then-execute style as `rename_item`.
- Every destination is checked with `dst.exists()` before any `shutil.move`/`Path.rename` (R-03 rule). Never overwrite; a mid-item failure stops that item and reports it, already-finished items stay (no rollback).
- **Sanitize execution:** the server recomputes each target from the current on-disk name with `sanitize_component` (never a client-supplied name), re-checks conflict/empty/`SxxEyy`, renames deepest paths first, then the item's format renames. On Windows the source path uses the `\\?\` extended prefix (names ending in a dot/space are unreachable otherwise).
- In-memory state update after a successful item (id/path/files/folder/raw_name), mirroring what move/rename already do, so the UI is consistent before the next scan.
- **Models** (`models.py`): `ReorganizeChange` (`item_id: Optional` — null for “Parent folders”, `folder_name`, `file_name`, `apply_folder`, `apply_file`, `apply_nfo` (4a rename), `nfo_action: use_best|delete_all|leave = leave`, `delete_images`, `sanitize: List[str]` paths) and `ReorganizeBody` (`changes`, `dry_run: bool = True`). NFO actions re-check that the folder still holds exactly the analysed NFOs, else refuse.
- **Route**: `POST /api/reorganize`.

**Done when:** dry run reports exactly what execute then does; every guard in the design's safety table is enforced server-side and covered in Phase 6 verification.

## Phase 4 — Reorganize Dialog

**Frontend** (`index.html` modal + `app.js`):
- Modal per the design mock: proposal rows (checkbox, current → **two editable name fields — folder and file** — with a per-row “= folder” sync toggle defaulting to `file_equals_folder` and per-row file-rename disable, operation summary, expander with the exact file operations and the item's sanitize entries, each with its own checkbox, image deletion as its own checkbox, several-NFO choice preset to *leave as is*), a “Parent folders” group for `parent_folders`, conflict rows red + unchecked, collapsed Blocked section with reasons, All/None, dry-run checkbox (default on), Preview/Do it, Close. The dialog sends the ids of `filteredItems` when it opens (and on Refresh).
- Client-side live validation of edited names (single component) and conflict re-check via the proposals data; server re-validates regardless.
- Execution renders per-row results (done/failed + reason); a completion footer offers "Rescan now" (reuse `triggerScan()`).
- Follow existing modal conventions (event delegation, `esc()` everywhere user data is interpolated, `_apiErr` for errors).

**Done when:** full flow works against a temp-tree scan: open → edit → select subset → preview → execute → per-row results → rescan prompt.

## Phase 5 — Toolbar button + badge

- **Reorganize** button in the header toolbar. The analysis runs **only** when the dialog opens or its Refresh is clicked (decision #1) — no post-scan hook, no background analysis.
- The badge shows the count from the most recent analysis of the session (updated on dialog open/refresh and after an execution); absent before the first analysis. Disabled with a tooltip while a scan is running (analysis would race the cache rebuild).

**Done when:** the button/badge behave as above and no analysis ever fires automatically (verified by watching the server log across a scan).

## Phase 6 — Risk register + verification

**[APPLICATION-RISKS.md](../APPLICATION-RISKS.md):** add the new operations with their guards (new R-numbers): bulk rename/foldering never overwrites, exact-stem collection, single-component validation, stale-cache refusal, series folder-only (format), sanitize (server-recomputed target, `SxxEyy` unchanged, followers, deepest-first, `\\?\` access), image deletion by design. Any accepted gap goes in as an Open risk.

**Verification** (repo pattern — no test files; real files in a temp dir, config patched in memory):
- Temp dir + `load_config` patched on `reorganize`/`scanner`/`fileops` modules + `scanner.CACHE_DIR` redirected; call module functions and route handlers directly (no httpx/TestClient).
- Cases: the Phase 2 matrix end-to-end (analyse → execute → re-analyse shows compliant), plus: dry run touches nothing (tree snapshot identical), conflict refusal, `Alien`/`Aliens` sidecar separation, stale-cache refusal, series folder rename updates episode item paths in state, name edited in the dialog to something illegal is refused server-side, sanitize of an episode + its `.en.srt` inside a sanitized season folder (deepest first, both end up legal), sanitize with a tampered client target is ignored (server recomputes).
- Update the memory/docs notes if any convention emerges (e.g. shared name-validation helper location).

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
