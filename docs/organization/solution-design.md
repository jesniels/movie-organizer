# Solution Design: Movie Library Organisation

**Phase:** Organisation features (movie library and download folder management)
**Status:** Approved for implementation

---

## Problem Statement

The existing web UI (`movie-organizer-ui.py`) can scan library and download locations and display their contents, but it lacks the ability to intelligently organise movies from download folders into a proper Jellyfin-compatible library structure. Users must currently use the CLI tool (`movie-organizer.py`) for this, which is a one-shot, command-line-only operation with no preview, no conflict handling, and no visibility into what is happening.

Beyond the core organise operation, users also have no in-app way to surface library-level intelligence — identifying movies that belong together in a Bundle, finding duplicates, or seeing which items have incomplete NFO metadata. These all require manual inspection.

The result is a fragmented workflow: the web UI provides visibility, but organising and analysing the library requires switching to separate CLI tools or manual filesystem operations.

---

## Solution

Extend the web UI with two major capabilities:

1. **Organize action** — a bulk operation that moves selected movies or series from any configured location into a chosen Library Location, creating per-movie subfolders for Loose Files, handling image deletion (NFO files keep their name), showing a full per-item preview with conflict detection, and executing with a visible progress modal that can be cancelled safely.

2. **Smart Scan** — a separate, deeper analysis pass that runs alongside (but independently of) the Basic Scan. It surfaces proposed Bundles, duplicate movies, and NFO gaps. Results are persisted across restarts and displayed in a dedicated sidebar section with actionable dialogs for each finding type.

Supporting these, the scanner is extended to track Bundle Paths, the destination picker supports multi-level subdirectory navigation, and the settings system gains new user-configurable defaults.

---

## User Stories

### Organize Action

1. As a user, I want to select multiple movies from the library list and click Organize, so that I can move them to a Library Location in one operation.
2. As a user, I want to select multiple series from the library list and click Organize, so that entire series folder trees are moved to a Library Location.
3. As a user, I want a destination picker that lists my configured Library Locations, so that I do not have to type a path manually.
4. As a user, I want to browse subdirectories within a Library Location in the destination picker, so that I can place items inside Bundle folders or genre subfolders.
5. As a user, I want to configure how many subdirectory levels the destination picker shows, so that I can match the depth of my actual folder structure.
6. As a user, I want to create a new subdirectory in the destination picker, so that I can organise into a folder that does not exist yet.
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
29. As a user, I want a "Clean up empty folders" option in the post-operation prompt, so that I can remove leftover empty source folders in one step.
30. As a user, I want the empty folder cleanup option to list the folders that will be deleted, so that I know exactly what will be removed.
31. As a user, I want the empty folder cleanup option to only appear when empty folders actually exist, so that the prompt is not cluttered when there is nothing to clean.
32. As a user, I want a warning in the post-operation prompt if a Smart Scan is running and blocking the rescan, so that I understand why the Rescan button is unavailable.
33. As a user, I want a "Stop Smart Scan" button inline in that warning, so that I can unblock the rescan without navigating away.

### Bundle Display

34. As a user, I want movies that live inside a Bundle folder to show a subtle breadcrumb below their title in the library list, so that I can see at a glance which bundle they belong to.
35. As a user, I want the breadcrumb to show all intermediate grouping folders between the Library Location root and the item, so that deeply nested items are fully identified.

### Move Action (Enhancement)

36. As a user, I want the Move action's destination picker to list all configured paths — both Library Locations and Download Locations — so that I can move an item back to downloads or to a staging folder for transcoding.

### Smart Scan

37. As a user, I want a Smart Scan button in the navbar, so that I can trigger a deeper library analysis at any time.
38. As a user, I want a separate Smart Scan progress indicator in the navbar, so that I can distinguish it from the Basic Scan indicator.
39. As a user, I want to be able to cancel a Smart Scan in progress, so that I can stop it if it is causing excessive disk or network IO.
40. As a user, I want the Rescan button to be disabled while Smart Scan is running, so that I am not running competing disk operations.
41. As a user, I want Smart Scan results shown in a dedicated sidebar section, so that findings are always visible without navigating away from the library list.
42. As a user, I want the sidebar section to show finding counts per type (Proposed Bundles, Duplicates, NFO Gaps), so that I can see the size of each problem at a glance.
43. As a user, I want to click a finding type in the sidebar to open its action dialog, so that I can act on findings without hunting through the library list.
44. As a user, I want Smart Scan results to be saved to disk and reloaded on application startup, so that I do not lose findings between sessions.
45. As a user, I want a Clear button in the Smart Scan sidebar section, so that I can discard results when I am done acting on them.
46. As a user, I want a staleness warning when the library has been rescanned since the last Smart Scan, so that I know the findings might not reflect the current state.
47. As a user, I want Proposed Bundle findings to list the member movies and suggest a bundle name, so that I can review the proposal before creating the bundle.
48. As a user, I want to edit the proposed bundle name before accepting it, so that the bundle folder name matches my convention.
49. As a user, I want accepting a bundle proposal to trigger the Organize action for those movies, so that the bundle is created using the same flow I already understand.
50. As a user, I want Duplicate findings to show the location and path of each copy, so that I can decide which copy to keep.
51. As a user, I want to select which duplicate copies to delete from the Duplicates dialog, so that I can resolve duplicates without leaving the Smart Scan workflow.
52. As a user, I want the Duplicates dialog to prevent me from deleting all copies of a movie, so that I cannot accidentally remove the only copy.
53. As a user, I want NFO Gap findings to list items with missing or partial NFO data and describe what is missing, so that I know which items need attention.

### Settings

54. As a user, I want to configure the destination picker depth in Settings, so that the picker matches the depth of my actual folder structure.
55. As a user, I want to configure the default value of the "Delete images" checkbox in Settings, so that it is already set correctly when I open the Organize dialog.
56. _(Removed 2026-10-01 — there is no NFO-rename option, so no default to configure.)_
57. As a user, I want to configure whether the mixed-types warning is shown in Settings, so that power users who intentionally mix movies and series can suppress it.

---

## Implementation Decisions

### New `config.yaml` Schema

A new top-level `settings` section is added. Existing keys (`locations`, `downloads`, `ffmpeg`) are unchanged.

```yaml
settings:
  organize:
    delete_images_default: true
    warn_mixed_types: true
    picker_depth: 2
```

The config loader merges defaults so existing config files without a `settings` key continue to work.

### Scanner: Bundle Path Tracking

The scanner (`_process_leaf`, `_scan_recursive`) is modified to accept and propagate the current "relative path from library root" as it descends into grouping folders. Each item gains a new `bundle_path` field — a list of folder name strings representing the intermediate grouping folders between the Library Location root and the item's own folder.

Examples:
- `M:\Movies\GoldenEye (1995)\` → `bundle_path: []`
- `M:\Movies\Action\James Bond\GoldenEye (1995)\` → `bundle_path: ["Action", "James Bond"]`

Items with an empty `bundle_path` show no breadcrumb. Items with a non-empty `bundle_path` show it in the library list as a secondary line.

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
| `GET` | `/api/locations/subdirs?path=…&depth=…` | Returns the subdirectory tree of a configured path up to the given depth. Only returns directories, not files. Only paths that are under a configured location are accepted. |
| `POST` | `/api/config/locations` | Adds a new Library Location path to `config.yaml`. Validates that the path exists. |

### Smart Scan Engine (new deep module)

A self-contained smart scan module with three independent analysers that can each be tested in isolation:

**Bundle Proposer** — takes a list of items, normalises titles (strips quality tags, year, sequel markers, colon subtitles), groups items by normalised base title, and returns proposed bundle groups with at least two members. Pure function, no I/O.

**Duplicate Finder** — takes a list of items, groups by normalised title, returns groups with more than one member across any location. The existing `_find_duplicates` function is a starting point but will be extended to normalise more aggressively. Pure function, no I/O.

**NFO Gap Detector** — takes a list of items, filters to those with `nfo_quality` of `none` or `partial`, and annotates each with which fields are missing (`year`, `uniqueids`). Pure function, no I/O.

The Smart Scan runner executes the three analysers sequentially, reporting progress. It is run as a cancellable background thread, separate from the Basic Scan thread.

### Smart Scan State and Persistence

Smart Scan state is held in a separate state dictionary from the library state, under the same `_lock`. It includes: `running`, `cancelled`, `last_scan_time`, `results` (findings per type), `error`.

Results are persisted to `web/cache/smart-scan.json` on completion, with the same write-to-temp-then-replace pattern used by the library cache. They are loaded at startup alongside the library cache.

Staleness is determined by comparing `_state["last_scan"]` (Basic Scan timestamp) against the Smart Scan `last_scan_time`. If `last_scan > last_smart_scan`, the staleness flag is set in the status response.

### Smart Scan API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/smart-scan/start` | Starts a Smart Scan. Returns 409 if Basic Scan is running. |
| `GET` | `/api/smart-scan/status` | Returns running flag, progress, last run timestamp, staleness flag. |
| `POST` | `/api/smart-scan/cancel` | Signals the running Smart Scan to stop. |
| `GET` | `/api/smart-scan/results` | Returns persisted findings. |
| `DELETE` | `/api/smart-scan/results` | Clears persisted findings and the cache file. |

### Basic Scan / Smart Scan Mutual Exclusion

The two background operations use separate named flags (`_state["scanning"]` for Basic Scan, `_smart_state["running"]` for Smart Scan). The Smart Scan start endpoint checks `_state["scanning"]` and returns 409 if a Basic Scan is in progress. The Rescan endpoint (`POST /api/scan`) checks `_smart_state["running"]` and returns 409 if Smart Scan is in progress. The frontend reflects these states by disabling the respective buttons.

### Frontend Modules

**Organize dialog** — opens from the selection action bar. Steps: destination picker (location + subdirectory) → preview table (per-row checkboxes, conflict highlighting, option checkboxes) → confirmation. Calls plan endpoint when destination is chosen; calls execute endpoint on "Do it".

**Progress modal** — shared component used by Organize execution. Polls `/api/organize/status` every 500 ms. Shows item count progress and current file name. Exposes a cancel callback. Used only for Organize in this phase; Smart Scan progress uses the navbar badge.

**Smart Scan sidebar section** — rendered in the existing left sidebar alongside the library status panel. Polls `/api/smart-scan/status` when a scan is running. Renders finding counts and a Clear button. Each finding row is clickable.

**Smart Scan action dialogs** — separate modal for each finding type (Bundles, Duplicates, NFO Gaps). Bundles dialog calls the Organize flow for accepted proposals.

**Destination picker component** — reusable two-level picker (location dropdown + subdirectory list/input). Calls `/api/locations/subdirs` when a location is selected. Supports "Add new location" and "Create new subfolder" inline.

---

## Testing Decisions

A good test verifies the external behaviour of a module given a specific input, without asserting on internal implementation details (function calls, intermediate state, data structure shapes beyond what the caller sees).

### Modules to test

**Organize plan generator** — highest priority. Pure function with well-defined inputs and outputs. Test cases: loose file → subfolder name derived from stem; NFO present → NFO title used; quality tags stripped; conflict detected when destination exists; auto-suggested conflict name increments correctly (`(1)`, `(2)`, …); series item always produces a folder-move plan (no subfolder creation); image files excluded from move list when delete_images is true; NFO files keep their name in the plan (never renamed to `movie.nfo`).

**Bundle path extractor** — pure function. Test cases: item directly under library root → empty bundle path; item one level deep → single-element bundle path; item two levels deep → two-element bundle path; path not under library root → error or empty.

**Bundle proposer** — pure function. Test cases: two movies with sequel indicators stripped to same base → proposed group; single movie → not proposed; movies from different locations → still grouped; title with colon subtitle → base extracted correctly; Roman numeral suffix → base extracted correctly.

**Duplicate finder** — pure function. Test cases: same title in library and download → duplicate group; same title in two Library Locations → duplicate group; unique title → not in results; title normalisation handles year and quality-tag differences.

**NFO gap detector** — pure function. Test cases: item with `nfo_quality: none` → included with missing fields noted; item with `nfo_quality: partial` (has year, no uniqueids) → included with `uniqueids` flagged as missing; item with `nfo_quality: full` → not included.

**Config loader with new settings section** — test that a config without `settings:` merges defaults correctly; test that a config with partial `settings.organize` fills in missing keys.

### Prior art in the codebase

The existing codebase has no test files for the web backend. The closest prior art is `test_filename_parsing.py` and `test_normalize.py` at the repo root, which test pure string-manipulation functions directly. The new deep modules (plan generator, bundle proposer, duplicate finder, etc.) follow the same pattern: import the function, call with a constructed input, assert on the output.

---

## Out of Scope

- **Series organize** — series are moved as a whole folder unit in this phase; detailed season/episode reorganisation (e.g. renaming individual episode files, restructuring season folders) is a separate future feature.
- **ffprobe / ffmpeg integration** — codec inspection and transcoding are acknowledged future features but are not part of this design.
- **Jellyfin API integration** — triggering Jellyfin library refreshes via API after organizing is a future enhancement; for now, the user manually triggers a Jellyfin scan.
- **NFO creation / editing** — the NFO Gaps finding surfaces items that need attention, but in-app NFO editing is out of scope. Only the detection and display are implemented.
- **Undo / rollback** — by ADR 0003, cancelled Organize operations do not roll back completed moves.
- **Concurrent organize operations** — only one Organize execution runs at a time.
- **`fix-renamed-movies.py` integration** — the iTunes m4v/mp4 matching workflow is not integrated into the web UI in this phase.
- **`movie-convert-checker.py` integration** — iTunes library cross-checking is not integrated in this phase.

---

## Further Notes

- The existing `_find_duplicates` function already exists in the backend and produces duplicate groups. The Smart Scan duplicate finder should reuse this logic, but run it against the full merged library (library + downloads) with more aggressive normalisation, rather than inline during the Basic Scan.
- The `_clean_title` function and `QUALITY_STRIP` regex are directly reusable by the bundle proposer for title normalisation.
- The existing scan badge polling pattern (`/api/scan/status` polled every 500 ms) should be replicated for Smart Scan status polling rather than introducing a WebSocket or SSE mechanism in this phase.
- The `picker_depth` setting controls UI rendering only; the `/api/locations/subdirs` endpoint always returns one level at a time regardless of depth, and the frontend makes multiple calls as the user navigates deeper.
- When a "Add new library location" path is added via the Organize picker, it must be validated on the backend (path must exist as a directory) before being written to `config.yaml`.
- The post-Organize empty folder cleanup deletes only folders that are completely empty after the move. It does not recurse into partially-emptied folder trees.
