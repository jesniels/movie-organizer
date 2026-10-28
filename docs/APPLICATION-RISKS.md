# Application Risks — Data-Loss Register

A list of the ways Movie Organizer could damage or lose library files, and what protects against each one.
It covers the web UI (`py/ui/`) and the CLI tools (`py/*.py`).

**Rule:** before changing anything that moves, renames, overwrites or deletes files, read this document.
If you add such an operation, add it here — with its guard, or as an open risk.

Last full audit: 2026-09-27. Reorganize (bulk rename/foldering/sanitize, `py/ui/reorganize.py`) added 2026-10-04: R-17 – R-24.

## Status overview

| ID | Risk | Area | Status |
| :--- | :--- | :--- | :--- |
| R-01 | Delete of a loose file removed its whole containing folder | Delete | Fixed |
| R-02 | Move of a loose file moved its whole containing folder | Move | Fixed |
| R-03 | Move silently overwrote an existing file at the destination | Move | Fixed |
| R-04 | Rename accepted path segments / renamed the download folder of a loose file | Rename | Fixed |
| R-05 | File rename on a series renamed every episode to `Name - Part N` | Rename | Fixed |
| R-06 | Delete trusted a stale cache | Delete | Fixed |
| R-07 | "Use transcoded video" did not verify the transcoded file | Transcoding | Fixed |
| R-08 | Transcode output folder could overlap a library/download folder | Transcoding | Fixed |
| R-09 | CLI organizer grabbed other movies' files by name prefix | CLI | Fixed |
| R-10 | A transcode could delete an existing output file | Transcoding | Fixed |
| R-11 | A failed "Use transcoded video" could lose the transcoded file | Transcoding | Fixed |
| R-12 | NFO field copy wiped an unparsable target NFO | NFO | Fixed |
| R-13 | Collection folders are scanned as one movie | Scanner / Delete | Fixed |
| R-14 | Whole-file NFO copy overwrites without backup or dry run | NFO | **Open** |
| R-15 | Renaming a multi-video movie renames extras too | Rename | **Open** (dry run shows it) |
| R-16 | Items under names ending with a space/dot: Move/Rename/Delete may fail | Scanner / file ops | **Open** (fails safely) |
| R-17 | Reorganize could overwrite or merge into an existing file/folder | Reorganize | Guarded |
| R-18 | Reorganize could act on what the client claims instead of what is on disk | Reorganize | Guarded |
| R-19 | Reorganize foldering could grab or lose sidecars of other movies | Reorganize | Guarded |
| R-20 | Reorganize could rename episodes / season folders and break `SxxEyy` | Reorganize | Guarded |
| R-21 | Reorganize could delete NFOs or images the user did not choose | Reorganize | Guarded |
| R-22 | Reorganize sanitize could reach outside the library or change names unexpectedly | Reorganize | Guarded |
| R-23 | A Reorganize item can stop half-way (no rollback) | Reorganize | **Open** (reported) |
| R-24 | After Reorganize the library shows old paths until a rescan | Reorganize | **Open** (fails safely) |
| R-25 | Deleting a loose file could take sidecars another video still uses | Delete | Guarded |
| R-26 | Moving a loose file left its sidecars behind / could take or overwrite others' sidecars | Move | Guarded |
| R-27 | Moving items out of a common folder (e.g. `James Bond\`) scattered them in the destination | Move | Guarded |
| R-28 | Moving movies into a bundle could create folders in the wrong place, overwrite, or take other movies' sidecars | Bundles | **Planned** (design) |
| R-29 | Deleting leftover folders could delete a video or files the user did not see | Leftover folders | **Planned** (design) |
| R-30 | Loose-file Move/Delete/Reorganize leave the `<stem>.trickplay` folder behind | Move / Delete / Reorganize | **Open** (fix decided) |

---

## Fixed risks

### R-01 — Delete of a loose file removed its whole containing folder
- **What happened:** a movie that is a loose file (e.g. `D:\Downloaded\Netflix\Movie.mkv`) has `path` = the folder it sits in. Delete ran `rmtree(item["path"])`, deleting the entire download folder.
- **Guard:** `_item_target(item)` in `py/ui/fileops.py` — loose-file items (id ≠ path) act on the file, folder items on the folder. Delete also refuses to delete a configured location itself.
- **UI:** the Delete dialog shows a file or folder icon plus the exact path that will be deleted.

### R-02 — Move of a loose file moved its whole containing folder
- **What happened:** same cause as R-01 — Move used `item["path"]`.
- **Guard:** Move uses `_item_target(item)`.

### R-03 — Move silently overwrote an existing file at the destination
- **What happened:** on Windows, `shutil.move` falls back to copy+delete when the target file exists, and the copy **replaces** the target without warning. (Introduced by the R-02 fix, found in the audit.)
- **Guard:** Move refuses if the destination exists, if the destination folder does not exist, or if the item is already in that folder.
- **Rule for new code:** never call `shutil.move` without checking `dst.exists()` first.
- **Pre-checks (2026-10-01), same for dry run and real run:** the destination folder must be writable (`_write_probe` creates and deletes an empty `.movie-organizer-write-test-*.tmp` — `os.access` ignores ACLs/share rights on Windows; `tempfile.mkstemp` is not used because it loops on `PermissionError` there), the source's parent folder must be writable (the source is removed after the copy), a source file must not be read-only, two selected items may not share a destination name, and a cross-drive move needs enough free space for the whole batch. A failed check skips that item before anything is copied.
- **UI:** the dry-run result says clearly whether everything can be moved and returns to the Move dialog. A real move runs one item per request with a progress bar; the dialog and page cannot be closed while it runs, and it stops at the first destination-level error.

### R-04 — Rename accepted path segments / renamed the download folder of a loose file
- **What happened:** the backend accepted names like `..\somewhere` (moves the folder elsewhere) and a "folder" rename on a loose file (renames the download folder). The UI hid these options but the API did not.
- **Guard:** `rename_item` accepts a single path component only, allows only "files" for loose files, refuses an existing target, and plans all changes before renaming anything.
- **Single-component rule** (2026-09-29): shared helper `settings.valid_component` — used by Rename and Reorganize so the rules cannot drift. Refuses empty names, `\ / : * ? " < > |` and control characters, `.`/`..`, a trailing dot or space (Windows silently strips them, so the result would differ from the name checked) and reserved device names (`CON`, `NUL`, `COM1`…).

### R-05 — File rename on a series renamed every episode
- **What happened:** "files"/"both" rename on a series renamed all episodes to `Name - Part 1..N`, destroying the `SxxEyy` naming Jellyfin and missing-episode detection rely on.
- **Guard:** series can only rename the folder (backend + Rename dialog).

### R-06 — Delete trusted a stale cache
- **What happened:** items come from the scan cache. If a folder changed after the last scan, Delete removed whatever is there now.
- **Guard:** Delete refuses a folder that contains video files the item does not know about ("rescan first").

### R-07 — "Use transcoded video" did not verify the transcoded file
- **What happened:** a truncated or broken encode could permanently replace the original video.
- **Guard:** `_verify_transcoded` in `py/ui/transcode.py` (ffprobe) requires a video **and** an audio stream, and a duration within max(2 s, 1 %) of the original. Runs for dry runs too.

### R-08 — Transcode output folder could overlap a library/download folder
- **What happened:** an output folder equal to, above, or inside a library/download folder writes transcodes into the library, and widens the area Delete is allowed to touch.
- **Guard:** `transcoded_root_conflict()` in `py/ui/settings.py`. Saving such a config is refused; start-transcode and use-transcoded refuse; the scanner ignores an overlapping output folder and Delete does not accept it as a base (`safe_transcoded_root()`).

### R-09 — CLI organizer grabbed other movies' files by name prefix
- **What happened:** `py/movie-organizer.py` collected every file whose name *started with* the movie name, so organizing `Alien` pulled `Aliens.mkv` into the `Alien` folder (and deleted its images).
- **Guard:** related files are the exact stem, or the stem followed by `.` or `-` (`Alien.en.srt`, `Alien-poster.jpg`).

### R-10 — A transcode could delete an existing output file
- **What happened:** if the destination appeared before encoding, or two files in one job had the same destination, ffmpeg (`-n`) failed and the partial-file cleanup deleted the **existing** file.
- **Guard:** duplicate destinations in one job are skipped; the destination is re-checked right before encoding and skipped if it exists, so cleanup only ever removes files the job created.

### R-11 — A failed "Use transcoded video" could lose the transcoded file
- **What happened:** the transcoded file was moved next to the original first; if the final replace failed, the cleanup deleted it.
- **Guard:** on a failed replace the transcoded file is moved back; an incomplete cross-drive copy is removed only while the source still exists. The original stays untouched in both cases.

### R-12 — NFO field copy wiped an unparsable target NFO
- **What happened:** "copy selected fields" into an NFO that is not valid XML started a fresh NFO with only the copied fields, discarding the rest.
- **Guard:** the merge refuses an unparsable target (fix it, or use whole-file copy).

### R-25 — Delete of a loose file and its sidecars (2026-10-20)
- **Behaviour:** deleting a loose-file item (Delete dialog, or the trash button per copy in the duplicate compare window) also deletes its exact-stem sidecars — NFO, images, subtitles (`reorganize.related_files`, R-09 rule). A folder item is deleted as a whole, as before.
- **Risk:** two videos with the same stem in one folder (`Movie.mkv` + `Movie.avi`, a typical duplicate) share `Movie.nfo`; deleting one copy would take the other copy's NFO.
- **Guard:** `fileops._own_sidecars` skips every sidecar that also belongs to another video in the folder. Sidecars are only deleted after the video itself was deleted. The dry run lists every sidecar (`also`); failed sidecar deletes are reported as a warning.
- **After the delete:** deleted items are dropped from memory and duplicates recomputed (`state.forget_items`), so a copy left alone is no longer flagged; a rescan follows.

### R-26 — Move of a loose file and its sidecars (2026-10-20)
- **What happened:** Move of a loose-file item moved only the video; its NFO, images and subtitles stayed in the download folder.
- **Behaviour:** a loose file now takes its own exact-stem sidecars along (`fileops._own_sidecars`, same rule as R-25); a sidecar shared with another video in the folder stays. A folder item moves as a whole, as before.
- **Guard:** every sidecar gets the video's checks — destination must not exist (R-03), not claimed by another item in the batch, not read-only, counted in the free-space check. Sidecars are moved only after the video arrived, each re-checked for an existing destination right before; a failed sidecar move leaves it in the source folder and is reported as a warning (the result dialog opens instead of the success toast). The dry run lists every sidecar (`also`).

### R-27 — Move of items in a common folder (2026-10-20)
- **What happened:** all movies in `Downloaded\James Bond\` were selected and moved; each landed straight in the destination and the common folder was lost.
- **Guard:** when the Move dialog opens it asks the server (`fileops.move_groups`) which selected items sit in a folder below their location root, shows those folders ("n of m items selected") and asks **Move them together?** — *Yes* (default): the item goes into the same folder path below the destination (`keep_groups`); *No*: straight into the destination, with an offer to **Unselect** them instead. Move is disabled until the check has answered.
- **Safety:** all R-03/R-26 checks apply to the path inside the common folder; an existing folder of that name in the destination is merged into item by item (an existing item is refused, never overwritten). After a move, the source common folder (and parents up to the root) is removed **only when empty** (`rmdir`, which cannot delete content); removed folders are listed in the result.

---

## Reorganize — guarded operations (2026-10-04)

Reorganize ([design](reorganizing/REORGANIZE-DESIGN.md)) creates folders for loose files, renames movie/series folders, video files and NFOs, deletes NFOs/images and sanitizes illegal names. **Nothing runs without the user ticking it in the Reorganize dialog and clicking Do it with dry run off, then Confirm.** All execution is in `reorganize.apply_changes`; verified by `py/test/test_reorganize_execution.py` (real files in `py/test/_tmp*`).

### R-17 — Overwrite or merge into an existing file/folder
- **Risk:** a rename/move onto an existing name replaces it (`os.rename` overwrites silently on Linux) or mixes two movies.
- **Guard:** every operation of an item is planned first against a view of the disk plus the operations planned so far (`_Planner` / `_View`); an existing target refuses the item before anything is touched, and each target is checked again right before `os.rename`. Case-only renames of the same path are allowed. A dry run over several items also catches two ticked changes with the same target. The dialog warns live when a ticked folder/file name equals another ticked row or a folder/video of the scanned library (advisory — the server decides).

### R-18 — Acting on what the client claims
- **Risk:** a stale or manipulated request renames/deletes paths the user never saw.
- **Guard:** at execute time the server re-runs the analysis per item on the current disk and takes only the user's choices (ticks, edited names, NFO action) from the request; move lists, sidecars, NFO targets and sanitize targets are recomputed. Anything no longer proposed refuses the item. Edited names must pass `valid_component` as a whole, file stems also on their own. Stale cache (videos added/missing since the scan) refuses format operations ("rescan first"). Refused with 409 while a scan or a transcode runs.

### R-19 — Sidecars of other movies
- **Risk:** foldering `Alien` takes `Aliens.en.srt` (same cause as R-09).
- **Guard:** `reorganize.related_files` — exact stem, or stem + `.`/`-`; other videos excluded. Loose files act on the file (`item["id"]`), never `item["path"]` (R-01/R-02).

### R-20 — Episodes and season folders
- **Guard:** series get a folder rename only; episodes and season folders are only sanitized (illegal characters, trailing dot/space), and a sanitize that would change the `SxxEyy` token is blocked.

### R-21 — Deleting NFOs or images
- **Guard:** NFOs are only deleted by an explicit *use the best* / *delete all* choice (default *leave as is*; only the user changes it — neither the row checkbox nor All picks a deletion), and only when the folder still holds exactly the NFOs the user saw (`nfo_files`). A lone `movie.nfo` / `<video>.nfo` is never touched. Image deletion is its own checkbox, unticked by default, and only covers exact-stem images of a loose file being foldered.

### R-22 — Sanitize scope and result
- **Guard:** only paths inside configured locations/downloads (`_scope`); only paths the server's own re-analysis produced; target recomputed with `sanitize_component` (never a client name); empty results blocked; deepest paths first, Parent folders after all items. Names ending with a space/dot are reached via the `\\?\` prefix (`fs_path`). After a Parent folder is renamed, the dialog locks the rows below it ("rescan and refresh first") because their paths changed.

### R-13 — Collection folders are scanned as one movie
- **What happened:** a folder that directly contains even one video (e.g. `Netflix\The Guns of Navarone (1961).mkv` next to `Netflix\Fury (2014)\...`) was treated as a single movie, and all videos in its subfolders counted as that movie's files. Rename, Delete or Move acted on the whole collection.
- **Guard (2026-10-20):** `scanner._is_leaf_folder` treats such a folder as a Bundle when a subfolder holds videos and is not a season folder or a Jellyfin extras folder (`EXTRAS_DIR_NAMES`). Its own videos become Loose Files; each subfolder is scanned as its own item. Test: `py/test/test_scanner_bundles.py`.
- **Still in place:** the Delete dialog shows a red warning when a movie folder contains more than one video file.

## Bundles — planned operation (design 2026-10-28)

[Design](organization/bundles.md). Not built yet; the guards below are requirements for the implementation.

### R-28 — Moving movies into a bundle
- **Risk:** *Create bundle* / *Add to bundle* / *Out of bundle* / *Bundle…* create folders and move movies, and *Rename* in the Bundles overview renames a bundle folder. A bad name or parent could create a folder outside the library, a move could overwrite an existing movie, and foldering a loose file could take another movie's sidecars.
- **Required guards:** an extension of Move, so R-03 (never overwrite, checked again right before each move), R-26 (exact-stem sidecars incl. the `<stem>.trickplay` folder, shared sidecars stay) and R-27 (an emptied source folder removed by `rmdir` only) apply. The only folders created are a new bundle folder directly in a configured Library Location (name must pass `valid_component`; an existing file or folder of that name refuses) and a loose file's own `{title} ({year})` folder inside the bundle (refused without title/year, or when it exists). *Out of bundle* goes to the bundle's Library Location root and never recreates the bundle folder (no R-27 "keep together"). A bundle rename uses the Rename rules (single component, existing target refused, case-only allowed). Series and transcoded items refused. Dry run by default, in-dialog confirmation, Jellyfin warning, a rescan follows. Detection itself never changes files.
- **Organize's "Create new folder…"** uses the same checks (`valid_component`, only below a configured Library Location).

## Leftover folders — planned operation (design 2026-10-28)

[Design](organization/README.md#leftover-folders-designed-2026-10-28-not-built-yet). Not built yet; the guards below are requirements for the implementation.

### R-29 — Deleting leftover folders
- **Risk:** a folder listed from the scan cache may have changed (a video copied in, new files added), a folder could lie outside the library, or it could be live data of a working movie (`Heat (1995)\extrafanart\`) or another configured location.
- **Required guards:** only folders the scan walks through as grouping folders inside an enabled Library or Download Location — never the location itself, never a folder that is or contains another configured folder (library, download, transcode output; enabled or not), never a folder inside a scanned movie or series folder, never a folder on the `ignored_folders` list (left in place, so its parent stays). Nothing ticked by default; dry run by default; in-dialog confirmation. Before deleting, the server re-checks each folder on disk against all of these rules, plus: no video anywhere below it, only files on the configured `leftover:` lists (video extensions can never be added to them) and exactly the files the user saw — otherwise that folder is refused. Files are deleted one by one, then folders with `rmdir` deepest first; no `rmtree`, so anything unexpected stops that folder.

## Open risks

### R-30 — Loose-file Move/Delete/Reorganize leave the `<stem>.trickplay` folder behind (fix decided 2026-10-28)
- **Risk:** Jellyfin can store trickplay images in a `<stem>.trickplay\` folder next to the video. `fileops._own_sidecars` and `reorganize.related_files` only collect files, so moving, deleting or foldering a loose file leaves that folder behind (and Delete leaves it as an orphan).
- **Decided fix (not built):** everywhere a loose file's sidecars are collected (Move R-26, Delete R-25, Reorganize R-19, bundle moves R-28), the `<stem>.trickplay` folder follows under the same exact-stem rule: destination must not exist, a folder shared with another video stays, listed in the dry run, moved only after the video.

### R-14 — Whole-file NFO copy overwrites without backup or dry run
- **Risk:** copying a whole NFO replaces the target NFO permanently (hand-edited metadata is lost). Only the field-picker confirmation protects it.
- **Possible fix:** write `<name>.nfo.bak` before overwriting, or add a dry-run preview.

### R-15 — Renaming a multi-video movie renames extras too
- **Risk:** a movie folder with several video files (extras, trailers, parts) gets all of them renamed to `Name - Part N`.
- **Current mitigation:** Rename is dry run by default and lists every file change.

### R-16 — Items under names ending with a space/dot: Move/Rename/Delete may fail
- **Background (2026-10-01):** Windows strips a trailing space/dot from the last part of a path, so `Path.is_dir()` returned False for e.g. `Marvel ` or `Season 1 ` and the scanner skipped them. The scanner now sees them (`scanner._is_dir`, `_walk_entries` and `settings.fs_path` use the `\\?\` prefix), so such items appear in the UI for the first time.
- **Risk:** Move, Rename and Delete use normal paths. On an item whose **own** folder name ends with a space/dot they fail with an error (e.g. Delete treats the folder as a file and `unlink` fails; the stale-cache check is skipped because `is_dir()` is False). Nothing is lost, but the action does not work.
- **Mitigation:** sanitize the name first via Reorganize (it uses the `\\?\` prefix). **Possible fix:** use `fs_path` in `fileops` too.

### R-23 — A Reorganize item can stop half-way (no rollback)
- **Risk:** if an operation fails mid-item (file locked, permissions), the earlier operations of that item stay done — e.g. the video renamed but the folder not.
- **Current mitigation:** the result says how many operations were done and marks each one (✓ done / · not done); the row stays selectable so the user can run it again after fixing the cause (the server re-analyses first). Already finished items stay done ([ADR 0003](adr/0003-cancel-no-rollback-delete-partial-files.md) precedent).

### R-24 — Old paths until a rescan
- **Risk:** Reorganize does not update the in-memory library (item ids are paths). Until the user rescans, Move/Rename/Delete/Reorganize on a changed item use its old path.
- **Current mitigation:** those actions fail on the missing path (nothing is lost); the dialog offers **Rescan now** after every execution, locks done rows, and "not a duplicate" marks are moved to the new paths right away.

---

## By design (not risks)

- **Images are deleted** by the CLI organizer (Jellyfin regenerates them). Reorganize offers the same for loose files, but only as a separate, unticked-by-default choice (R-21).
- **`delete-empty-folders.py`** deletes folders with no files or only a lone `.nfo`, never when a video exists anywhere below; requires `--doit`.
- **"Use transcoded video"** deletes an original with a different extension (e.g. `.mp4` → `.mkv` replaces it), after dry run, confirmation and verification (R-07).
- **Kill/cancel of a transcode** deletes the partial output file ([ADR 0003](adr/0003-cancel-no-rollback-delete-partial-files.md)).
- **Jellyfin user data:** Jellyfin identifies items by path, so a moved or renamed movie (Move, Reorganize, bundle moves, bundle rename) may lose its played status and other user data. The bundle dialogs warn about it.

## General safeguards

- Destructive web-UI actions default to **dry run** (Move, Rename, Delete, Use transcoded video, Reorganize).
- Confirmations happen **inside the dialog** (no browser popups): the first click warns and relabels the button, a second click confirms (`confirmInDialog` in `web/static/js/core.js`; Reorganize has its own *Confirm (N)* step).
- Delete only works inside configured locations (library, download, non-overlapping transcode output folder).
- Transcoding only reads source files and only writes inside `<output folder>\Movies\` (never overwrites: `-n` + existence checks).
- Operations run against the scan cache — **rescan** after changing files outside the app.

## How guards were verified

Each fix was exercised against real files (and real ffmpeg for transcoding) in a temporary folder, with the config swapped in memory so the real library and `web/config.yaml` were never touched. Pattern: create a temp dir, patch `load_config` on the `fileops` / `scanner` / `transcode` modules and `scanner.CACHE_DIR`, call the module functions directly, then remove the temp dir.
