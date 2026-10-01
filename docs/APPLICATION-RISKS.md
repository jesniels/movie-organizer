# Application Risks — Data-Loss Register

A list of the ways Movie Organizer could damage or lose library files, and what protects against each one.
It covers the web UI (`py/ui/`) and the CLI tools (`py/*.py`).

**Rule:** before changing anything that moves, renames, overwrites or deletes files, read this document.
If you add such an operation, add it here — with its guard, or as an open risk.

Last full audit: 2026-09-27.

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
| R-13 | Collection folders are scanned as one movie | Scanner / Delete | **Open** (warning only) |
| R-14 | Whole-file NFO copy overwrites without backup or dry run | NFO | **Open** |
| R-15 | Renaming a multi-video movie renames extras too | Rename | **Open** (dry run shows it) |
| R-16 | Items under names ending with a space/dot: Move/Rename/Delete may fail | Scanner / file ops | **Open** (fails safely) |

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

---

## Open risks

### R-13 — Collection folders are scanned as one movie
- **Risk:** a folder that directly contains even one video (e.g. `Marvel\trailer.mkv` next to `Marvel\Iron Man (2008)\...`) is treated as a single movie, and all videos in its subfolders count as that movie's files. Deleting or moving it acts on the whole collection.
- **Current mitigation:** the Delete dialog shows a red warning when a movie folder contains more than one video file.
- **Proper fix:** change how the scanner treats such folders. Must be agreed against [organization/CONTEXT.md](organization/CONTEXT.md) and the ADRs first.

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

---

## By design (not risks)

- **Images are deleted** by the CLI organizer (Jellyfin regenerates them).
- **`delete-empty-folders.py`** deletes folders with no files or only a lone `.nfo`, never when a video exists anywhere below; requires `--doit`.
- **"Use transcoded video"** deletes an original with a different extension (e.g. `.mp4` → `.mkv` replaces it), after dry run, confirmation and verification (R-07).
- **Kill/cancel of a transcode** deletes the partial output file ([ADR 0003](adr/0003-cancel-no-rollback-delete-partial-files.md)).

## General safeguards

- Destructive web-UI actions default to **dry run** (Move, Rename, Delete, Use transcoded video).
- Delete only works inside configured locations (library, download, non-overlapping transcode output folder).
- Transcoding only reads source files and only writes inside `<output folder>\Movies\` (never overwrites: `-n` + existence checks).
- Operations run against the scan cache — **rescan** after changing files outside the app.

## How guards were verified

Each fix was exercised against real files (and real ffmpeg for transcoding) in a temporary folder, with the config swapped in memory so the real library and `web/config.yaml` were never touched. Pattern: create a temp dir, patch `load_config` on the `fileops` / `scanner` / `transcode` modules and `scanner.CACHE_DIR`, call the module functions directly, then remove the temp dir.
