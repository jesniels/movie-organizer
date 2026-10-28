# Solution Design: Movie Library Organisation

**Phase:** Organisation features (movie library and download folder management)
**Status:** Approved for implementation (Smart Scan replaced by bundle detection during every scan, 2026-10-28 — see [bundles.md](bundles.md))

---

## Problem Statement

The existing web UI (`movie-organizer-ui.py`) can scan library and download locations and display their contents, but it lacks the ability to intelligently organise movies from download folders into a proper Jellyfin-compatible library structure. Users must currently use the CLI tool (`movie-organizer.py`) for this, which is a one-shot, command-line-only operation with no preview, no conflict handling, and no visibility into what is happening.

Beyond the core organise operation, users also have no in-app way to surface library-level intelligence — identifying movies that belong together in a Bundle, finding duplicates, or seeing which items have incomplete NFO metadata. These all require manual inspection.

The result is a fragmented workflow: the web UI provides visibility, but organising and analysing the library requires switching to separate CLI tools or manual filesystem operations.

---

## Solution

Extend the web UI with two major capabilities:

1. **Organize action** — a bulk operation that moves selected movies or series from any configured location into a chosen Library Location, creating per-movie subfolders for Loose Files, handling image deletion (NFO files keep their name), showing a full per-item preview with conflict detection, and executing with a visible progress modal that can be cancelled safely.

2. **Bundles** — every scan proposes movies that belong together (same NFO collection, or similar titles), as a new bundle or as an addition to an existing one. The user moves them into a bundle folder with dry run and confirmation, or marks them *not related*; those choices are remembered and editable in Settings. Duplicates and incomplete NFOs are already shown as special filters, so there is no separate Smart Scan.

Supporting these, the scanner records each movie's Bundle, the destination picker offers a Library Location's root and its bundles, and the settings system gains new user-configurable defaults.

---

## User Stories

### Organize Action

1. As a user, I want to select multiple movies from the library list and click Organize, so that I can move them to a Library Location in one operation.
2. As a user, I want to select multiple series from the library list and click Organize, so that entire series folder trees are moved to a Library Location.
3. As a user, I want a destination picker that lists my configured Library Locations, so that I do not have to type a path manually.
4. As a user, I want to browse subdirectories within a Library Location in the destination picker, so that I can place items inside Bundle folders. (Changed 2026-10-28 — the root and its bundles.)
5. _(Removed 2026-10-28 — no picker depth to configure.)_
6. As a user, I want to create a new bundle in the destination picker, so that I can organise into a bundle that does not exist yet. (Changed 2026-10-28 — a single folder name directly in the Library Location.)
7. As a user, I want to add a new Library Location directly from the Organize destination picker, so that I do not have to navigate to Settings mid-workflow.
8. As a user, I want a new Library Location added via the picker to be immediately saved to `config.yaml`, so that it is available in future sessions.
9. As a user, I want the destination shown once at the top of the preview dialog, so that I can confirm I have chosen the right location before reviewing individual items.
10. As a user, I want to see each planned move as a single `FileName → FolderName` row in the preview, so that I can quickly verify what will happen to each item.
11. As a user, I want each row in the preview to have a checkbox, so that I can exclude specific items from the operation without starting over.
12. As a user, I want Conflict rows highlighted in red when the destination folder already exists, so that I am warned before overwriting anything.
13. As a user, I want a conflict row to show an auto-suggested alternative name (e.g. `GoldenEye (1995) (1)`), so that I have a safe option to approve instead of figuring out a name myself.
14. As a user, I want conflict rows to be unchecked by default, so that I must actively approve each conflict rather than accidentally overwriting.
15. As a user, I want a "Delete images" checkbox in the Organize dialog, so that I can choose whether to remove jpg/png files that Jellyfin will regenerate.
16. As a user, I want NFO files to keep their name (`movie.nfo` or named after the video) when organizing, so that Jellyfin keeps reading them and nothing is renamed unnecessarily. (Changed 2026-10-01 — previously a “Rename NFO to movie.nfo” checkbox.)
17. As a user, I want both checkboxes to be inactive when only series are selected, so that I am not confused by options that do not apply to series.
18. As a user, I want the defaults for both checkboxes to be configurable in Settings, so that I do not have to adjust them on every organize operation.
19. As a user, I want to be warned when my selection mixes movies and series going to the same destination, so that I do not accidentally mix content types in a Library Location.
20. As a user, I want the mixed-types warning to be a configurable setting (default: on), so that power users who intentionally mix types can disable it.
21. As a user, I want the UI to be locked while an organize operation is executing, so that I cannot make changes that would conflict with the ongoing file moves.
22. As a user, I want a progress bar showing how many items have been moved out of the total, so that I know how long the operation will take.
23. As a user, I want to see the name of the item currently being moved, so that I have a sense of progress on large batches.
24. As a user, I want a Cancel button in the progress modal, so that I can stop a long-running operation if needed.
25. As a user, I want a cancelled operation to stop cleanly at the current item boundary, so that already-moved items are not left in an inconsistent state.
26. As a user, I want any partially-copied destination file to be deleted on cancel, so that I do not end up with corrupt half-written files in my library.
27. As a user, I want a post-operation prompt telling me how many items were moved, so that I can confirm the outcome.
28. As a user, I want a "Rescan now" option in the post-operation prompt, so that the library list reflects the new state immediately.
29. As a user, I want a "Review leftover folders" link in the post-operation prompt when the operation left folders behind, so that I can clean them up in one step. (Changed 2026-10-28 — one Leftover Folders dialog for all actions.)
30. As a user, I want the leftover folders dialog to list each folder with its content, so that I know exactly what will be removed.
31. As a user, I want the link to appear only when leftover folders exist, so that the prompt is not cluttered when there is nothing to clean.
32. _(Removed 2026-10-28 — there is no Smart Scan that could block the rescan.)_
33. _(Removed 2026-10-28 — see 32.)_

### Bundle Display

34. As a user, I want movies that live inside a Bundle folder to show a subtle breadcrumb below their title in the library list, so that I can see at a glance which bundle they belong to.
35. As a user, I want the breadcrumb to show the bundle the movie is in (the folder directly above its own folder), so that I can see where it belongs.

### Move Action (Enhancement)

36. As a user, I want the Move action's destination picker to list all configured paths — both Library Locations and Download Locations — so that I can move an item back to downloads or to a staging folder for transcoding.

### Bundles (replaces Smart Scan, 2026-10-28)

37. As a user, I want bundle proposals computed as part of every scan, so that I do not have to start a separate analysis or watch a second indicator.
38. As a user, I want movies with the same collection in their NFO grouped together, so that franchises without a shared title (e.g. James Bond) are found.
39. As a user, I want movies with similar titles grouped when their NFOs give no collection, so that movies without good NFOs are found too.
40. As a user, I want each proposal to say why it was made (same collection or similar titles), so that I know how much to trust it.
41. As a user, I want a single movie never proposed as a bundle, and copies of one movie counted once, so that proposals are real groups.
42. As a user, I want movies from different collections never grouped by title, so that unrelated movies with similar names stay apart.
43. As a user, I want a movie outside a bundle suggested for an existing related bundle, with the bundle's current movies shown, so that new arrivals join their bundle.
44. As a user, I want a "Possible bundles" special filter with a count, so that I see the proposals next to Duplicates and Incomplete NFO.
45. As a user, I want to edit the proposed bundle name and choose the Library Location, so that the folder matches my convention.
46. As a user, I want to create a bundle from a proposal, moving the movies into a new folder with a dry run first, so that I can check before anything moves.
47. As a user, I want to add a movie to an existing bundle from a proposal, so that the bundle stays complete.
48. As a user, I want a loose file moved into a bundle to get its own `Title (Year)` folder, so that the bundle keeps the Jellyfin structure.
49. As a user, I want to mark a whole proposal as not related, so that unrelated movies are not proposed again.
50. As a user, I want to mark one movie of a proposal as outside the bundle, so that the rest can still be bundled.
51. As a user, I want to mark a movie suggested for an existing bundle as not related, so that it is not suggested again.
52. As a user, I want these choices remembered and listed in Settings where I can remove them, so that I can change my mind.
53. As a user, I want to move selected movies into an existing or new bundle by hand, so that I can group movies the detection did not find.

### Settings

54. _(Removed 2026-10-28 — no picker depth setting.)_
55. As a user, I want to configure the default value of the "Delete images" checkbox in Settings, so that it is already set correctly when I open the Organize dialog.
56. _(Removed 2026-10-01 — there is no NFO-rename option, so no default to configure.)_
57. As a user, I want to configure whether the mixed-types warning is shown in Settings, so that power users who intentionally mix movies and series can suppress it.

### Bundles — additions (2026-10-28 review)

58. As a user, I want a Bundles overview listing every bundle with its movie count, where I can show a bundle's movies and rename the bundle folder, so that I can manage bundles in one place.
59. As a user, I want to move selected movies out of their bundle to the root of its Library Location, so that I can undo a bundling.
60. As a user, I want to be warned that moving a movie may reset its Jellyfin played status, so that I am not surprised.
61. As a user, I want bundles with the same name in two Library Locations proposed for merging, so that a bundle does not end up split across drives.

---

## Implementation Decisions

### New `config.yaml` Schema

A new top-level `settings` section is added. Existing keys (`locations`, `downloads`, `ffmpeg`) are unchanged.

```yaml
settings:
  organize:
    delete_images_default: true
    warn_mixed_types: true
```

The config loader merges defaults so existing config files without a `settings` key continue to work.

### Scanner: Bundle Tracking

The scanner (`_process_leaf`, `_scan_recursive`) passes the folder it descends from to the items it creates. Each movie in a Library Location gains a `bundle` field — the **full path** of the folder directly above its own folder (or above the loose file) when that folder is a Bundle ([bundles.md](bundles.md#what-is-a-bundle): directly in the location root, holding at least one movie folder or loose files of two different movies), otherwise empty. The library list shows the path's last component.

Examples:
- `M:\Movies\GoldenEye (1995)\` → `bundle: ""`
- `M:\Movies\James Bond\GoldenEye (1995)\` → `bundle: "M:\Movies\James Bond"`
- `M:\Movies\Heat (1995)\Heat - CD1.mkv` (parts of one movie, split into loose files) → `bundle: ""` — the item's own folder is never its bundle.
- `M:\Movies\A\B\Movie (2000)\` → `bundle: ""` — `B` is not directly in the root.

Items with a `bundle` show it in the library list as a secondary line.

### Organize Engine (new deep module)

A self-contained organize module with two pure-function stages and one I/O execution stage:

**Plan stage** — takes a list of items, a destination path, and options (delete_images). Returns a plan: a list of `OrganizeMove` objects each containing source, destination folder name, resolved destination path, and a conflict flag. No I/O except checking whether destination folders exist.

**Conflict resolution** — the plan stage auto-generates a non-conflicting alternative name when the destination exists. The UI presents this for user approval; the approved plan is sent to the execute stage.

**Execute stage** — iterates moves in order, performing filesystem operations. Accepts a progress callback (item index, total, current path). On cancel signal: stops at the next item boundary. If interrupted mid-copy (detected by checking destination file size against source before marking done), deletes the partial destination file.

The execute stage is run as a background task under a named lock so only one Organize operation runs at a time.

### Organize API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/organize/plan` | Returns a plan for the given items and destination. No file I/O except conflict checks. |
| `POST` | `/api/organize/execute` | Starts execution of a confirmed plan as a background task. |
| `GET` | `/api/organize/status` | Returns current progress (item index, total, current path, running flag, error). |
| `POST` | `/api/organize/cancel` | Signals the running organize to stop at the next item boundary. |

The plan endpoint is called when the user opens the preview dialog. The execute endpoint is called when the user clicks "Do it".

### Location Subdirectory API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/locations/bundles?path=…` | Returns the bundles directly in a configured Library Location (one level, folders only). Other paths are refused. |
| `POST` | `/api/config/locations` | Adds a new Library Location path to `config.yaml`. Validates that the path exists. |

### Bundle Detection and Bundle Moves (replaces the Smart Scan engine, 2026-10-28)

Full design: [bundles.md](bundles.md). In short:

- **Detection** — new module `py/ui/bundles.py` with a pure `find_bundle_proposals(items, not_related)`: groups movies by NFO collection (`<collectionnumber>` ID, else `<set>` name — both newly parsed in `nfo.py`), then by normalised title (NFO title, else the parsed name; title-only groups need the original film plus a sequel marker); never groups different collections; needs two different movies (a duplicate group counts once); never proposes a copy of a movie the bundle already has; proposes merging same-name bundles in two Library Locations. Exact rules: [bundles.md](bundles.md#rules). Runs at the end of every scan and wherever duplicates are recomputed; results live in `_state` only (recomputed from the cache at startup, nothing persisted).
- **Not related** — records `{path, key}` in `web/not-related.json` (format in [bundles.md](bundles.md#not-related--saying-no)), following path changes like `not-duplicates.json`; a Settings tab lists and removes them.
- **Moving into a bundle** — an extension of Move: may create one new bundle folder directly in a Library Location, and a loose file's own `Title (Year)` folder inside the bundle. All Move guards apply (APPLICATION-RISKS R-03, R-26, R-27; new R-28).

### Bundle API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/bundles` | Current proposals and the existing bundles (for the Bundle… picker). |
| `POST` | `/api/bundles/move` | Moves movies into an existing bundle or a new one (`location` + `name`); dry run by default. |
| `GET` | `/api/bundles/not-related` | Lists the not-related records. |
| `POST` | `/api/bundles/not-related` | Adds records (whole proposal, one movie, or a candidate for an existing bundle). |
| `POST` | `/api/bundles/not-related/remove` | Removes the selected records. |
| `POST` | `/api/bundles/out` | *Out of bundle*: moves the selected movies from their bundle to the root of its Library Location (loose files get their own `{title} ({year})` folder); no keep-together. `{ item_ids, dry_run }`, dry run by default. |
| `POST` | `/api/bundles/rename` | Renames a bundle folder (Bundles overview). `{ path, new_name, dry_run }`; Rename rules, case-only change allowed, not-related and not-a-duplicate records follow. Dry run by default. |
| `POST` | `/api/bundles/merge` | *Merge bundles*: moves every movie of `source` into `target` (both existing bundle folders in different Library Locations), as *Add to bundle* does. `{ source, target, dry_run }`, dry run by default. |

`GET /api/bundles` returns `{ proposals, bundles }`: `proposals` = every proposal (`type`: `new` / `add` / `merge`, `key`, `reason`, suggested `name`, member item ids, context members, pre-selected location or target); `bundles` = every existing bundle with its path, Library Location, movie count and the *nested movie folder?* hint — the data for the Bundle… picker and the Bundles overview. The browser fetches it together with `/api/status` whenever it reloads the library.

Request models (`models.py`): `BundleMoveBody` (`item_ids`, `bundle` path **or** `location` + `name`, `dry_run = True`), `BundleOutBody`, `BundleRenameBody`, `BundleMergeBody`, `NotRelatedBody` (records to add or remove).

### Leftover Folders API Endpoints

Design: [README](README.md#leftover-folders-designed-2026-10-28-not-built-yet), risk R-29.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/leftovers` | The current leftover folders (from the scan cache plus the after-action checks): per folder its path, location, label (*empty* / *Jellyfin data* / *subtitle, no video* / *NAS thumbnails*) and the full file list, relative to the folder (sub-folders included, `ignored_files` included but flagged as hidden). |
| `POST` | `/api/leftovers/delete` | `{ folders: [{ path, files: [relative paths] }], dry_run }`, dry run by default. `files` is **the list the user saw** — the server re-checks every R-29 rule on disk and refuses a folder whose content differs from it. Deletes the listed files one by one, then the folders with `rmdir` deepest first. Returns per folder `ok`, the operations (or what a real run would do) and any error. 409 while a scan runs. |

Request model (`models.py`): `LeftoverDeleteBody` (`folders: List[LeftoverFolder]` with `path` + `files`, `dry_run: bool = True`).

### Settings: Leftover Folder Lists

The `leftover:` section of `config.yaml` is saved through the existing `POST /api/config` like `transcode` and `naming`: `ConfigBody` gains `leftover: Optional[LeftoverConfig] = None` (None → leave unchanged, for an older frontend). `LeftoverConfig` holds the six lists — `jellyfin_files`, `subtitle_files`, `ignored_files`, `jellyfin_folders`, `thumbnail_folders`, `system_folders` — each `List[str]`. Saving refuses any entry that is or matches a video extension (`VIDEO_EXTS`); missing lists are merged with `DEFAULT_LEFTOVER` in `settings.py`.

### Frontend Modules

Front-end code goes in the feature's script in `web/static/js/`. Shared helpers and state are in `core.js`. A new script must be added to `UI_SCRIPTS` in `py/ui/app.py`. See ADR 0004.

**Organize dialog** — opens from the selection action bar. Steps: destination picker (location + subdirectory) → preview table (per-row checkboxes, conflict highlighting, option checkboxes) → confirmation. Calls plan endpoint when destination is chosen; calls execute endpoint on "Do it".

**Progress modal** — shared component used by Organize execution. Polls `/api/organize/status` every 500 ms. Shows item count progress and current file name. Exposes a cancel callback. Used only for Organize in this phase.

**Leftover folders** (`web/static/js/leftovers.js`) — the sidebar row, the Leftover folders dialog (list, expand, All/None, dry run, in-dialog confirmation), the “N leftover folders — Review…” link in result dialogs, and the Settings “Leftover folders” tab. Added to `UI_SCRIPTS` (ADR 0004).

**Bundles** (`web/static/js/bundles.js`) — the "Possible bundles" special filter, the Bundle dialog (new bundle / add to bundle, Outside / Not related buttons, dry run), the **Bundle…** selection-bar action (incl. *Out of bundle*), the Bundles overview (show movies, rename), and the Settings "Not related" tab.

**Destination picker component** — reusable two-level picker (location dropdown + subdirectory list/input). Calls `/api/locations/subdirs` when a location is selected. Supports "Add new location" and "Create new subfolder" inline.

---

## Testing Decisions

A good test verifies the external behaviour of a module given a specific input, without asserting on internal implementation details (function calls, intermediate state, data structure shapes beyond what the caller sees).

### Modules to test

**Organize plan generator** — highest priority. Pure function with well-defined inputs and outputs. Test cases: loose file → subfolder name derived from stem; NFO present → NFO title used; quality tags stripped; conflict detected when destination exists; auto-suggested conflict name increments correctly (`(1)`, `(2)`, …); series item always produces a folder-move plan (no subfolder creation); image files excluded from move list when delete_images is true; NFO files keep their name in the plan (never renamed to `movie.nfo`).

**Bundle of an item** — pure function. Test cases: item directly under library root → empty; item in a bundle folder → that folder's name; loose parts of one movie (`CD1`/`CD2`) in their folder → empty; loose files of two different movies in one folder → that folder; item in a Download Location → empty; path not under a library root → empty.

**Bundle proposer** — pure function. Test cases: two movies with the same NFO collection but unrelated titles → one proposal named after the collection without " Collection"; two movies with sequel indicators stripped to the same base → proposal only when one of them is the original (title = base, e.g. *Rocky* + *Rocky II*); *Toy Story 2* + *Toy Story 3* alone or *Apollo 13* + *Apollo 18* → nothing; `1917` → never stripped to an empty base; single movie (also a single-movie collection) → nothing; two copies of one movie → nothing; different collections with similar titles → not grouped; movies from different locations → still grouped; colon subtitle and Roman numeral suffix → base extracted, and a colon subtitle counts as a marker (*Alien* + *Alien: Covenant* → proposal); remakes without a marker (*The Thing* 1982/2011) → nothing; same `<collectionnumber>` with different collection names → grouped; movie related to an existing bundle → add-to-bundle proposal (base title = folder name or a member's whole title; a sequel-only member is not enough); movie already in a bundle → not proposed elsewhere; a copy (same duplicate group) of a bundled movie in Downloads → not proposed; not-related records → respected (proposal dropped below two unmarked movies).

**Bundle move** — real files in `py/test/_tmp*`: new bundle folder created only in a Library Location; invalid or existing name refused; movie folder moved as a whole; loose file foldered with its exact-stem sidecars (`Alien` / `Aliens` kept apart); existing destination refused; emptied source bundle removed only when empty; a loose-file copy of a movie already in the target bundle → skipped; *Out of bundle* → root of the Library Location, loose file foldered; bundle rename → existing name refused, case-only allowed; dry run touches nothing and lists what the real run does.

**Leftover folders** — real files in `py/test/_tmp*`: detection lists only the topmost leftover; a folder with a video below, an unknown file, a `system_folders` entry, inside a movie or series folder, or being/containing a configured location → not listed; an emptied series shell → listed; `@eaDir` inside a leftover → deleted with it, inside a movie folder → untouched. Delete: content differing from the file list sent → refused; a file added in the meantime → that folder stays; dry run touches nothing.

**Config loader with new settings section** — test that a config without `settings:` merges defaults correctly; test that a config with partial `settings.organize` fills in missing keys.

### Prior art in the codebase

The existing codebase has no test files for the web backend. The closest prior art is `test_filename_parsing.py` and `test_normalize.py` at the repo root, which test pure string-manipulation functions directly. The new deep modules (plan generator, bundle proposer, etc.) follow the same pattern: import the function, call with a constructed input, assert on the output.

---

## Out of Scope

- **Series organize** — series are moved as a whole folder unit in this phase; detailed season/episode reorganisation (e.g. renaming individual episode files, restructuring season folders) is a separate future feature.
- **ffprobe / ffmpeg integration** — codec inspection and transcoding are acknowledged future features but are not part of this design.
- **Jellyfin API integration** — triggering Jellyfin library refreshes via API after organizing is a future enhancement; for now, the user manually triggers a Jellyfin scan.
- **NFO creation / editing** — the Incomplete NFO special filter surfaces items that need attention, but in-app NFO editing is out of scope.
- **Undo / rollback** — by ADR 0003, cancelled Organize operations do not roll back completed moves.
- **Concurrent organize operations** — only one Organize execution runs at a time.
- **`fix-renamed-movies.py` integration** — the iTunes m4v/mp4 matching workflow is not integrated into the web UI in this phase.
- **`movie-convert-checker.py` integration** — iTunes library cross-checking is not integrated in this phase.

---

## Further Notes

- The `_clean_title` function and `QUALITY_STRIP` regex are directly reusable by the bundle proposer for title normalisation.
- When a "Add new library location" path is added via the Organize picker, it must be validated on the backend (path must exist as a directory) before being written to `config.yaml`.
- Leftover folders (no video below, only files from the configurable `leftover:` lists — Jellyfin data, system files, orphaned subtitles) are collected by every scan and deleted only from the Leftover Folders dialog — see the organization [README](README.md#leftover-folders-designed-2026-10-28-not-built-yet).
