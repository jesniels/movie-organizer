# Organisation Features

This document set describes the movie library and download folder organisation capabilities of Movie Organizer Web.

## Overview

The tool manages two categories of configured paths:

- **Library Locations** — destinations for organised, Jellyfin-ready content (e.g. `M:\Movies`, `M:\Series`)
- **Download Locations** — staging areas for newly downloaded, unorganised content (e.g. `D:\Downloaded\Netflix`)

The core workflow is: content lands in a Download Location → user selects items → **Organize** moves them into a Library Location with the correct folder structure. Every scan also surfaces remaining issues as special filters: duplicates, incomplete NFOs and possible **bundles**.

## Feature Documents

| Document | Description |
|---|---|
| [organize.md](./organize.md) | Organize action — move items to library with subfolder creation and image management (NFOs keep their name) |
| [bundles.md](./bundles.md) | Bundles — proposals computed during every scan (NFO collection, similar titles), "not related" marks, moving movies into a new or existing bundle |

## Other Actions

### Move

Raw relocation of an item to any configured path (Library or Download Location). No subfolder creation, no NFO renaming, no image handling. Useful for staging, moving back to downloads, or relocating to a transcoding folder.

The Move target picker lists all configured paths from `config.yaml` (both `locations:` and `downloads:`) plus a free-text custom path field.

A loose movie file moves as the file only (never its containing folder). Move refuses when the destination already exists or the destination folder does not exist.

### Rename

Renames an item's folder, video files, or both. Available as a context action on single items. Loose files can only rename the file; series can only rename the folder (episode names carry `SxxEyy`). Names must be a single path component, and an existing target is refused.

### Delete

Permanently removes an item from disk: a movie/series folder with all its contents, or — for a loose movie file — only that file. Only items whose path originates from a configured location can be deleted, a configured location itself is never deleted, and a folder holding videos not known from the last scan is refused (rescan first).

> All file-changing actions and their safeguards are tracked in [../APPLICATION-RISKS.md](../APPLICATION-RISKS.md).

### Leftover Folders (designed 2026-10-28, not built yet)

Folders left behind after movies were moved or deleted — often not literally empty, because Jellyfin leaves its NFOs and images there. The scan lists them; the user deletes the ones they choose.

**What counts as a leftover folder** — a folder inside an (enabled) Library or Download Location that has **no video anywhere below it** and holds only files from the configured lists (below). It is never:

- the location itself, nor a folder that **is or contains another configured folder** (library, download or transcode output, enabled or not — the same list the scan uses to skip nested locations);
- a folder **inside a scanned movie or series folder** — `Heat (1995)\Subs\`, `Heat (1995)\extrafanart\`, `Heat (1995)\Heat.trickplay\` or an empty `Season 00\` belong to a working item. Only folders the scan walks through as grouping folders can be leftovers;
- a folder named in `ignored_folders` (NAS system folders such as Synology `@eaDir`, QNAP `.@__thumb`): never listed and never entered. A leftover folder that contains one is listed, but the ignored folder is left in place, so the parent stays too (reported, not an error).

| Content | Shown as |
| :--- | :--- |
| nothing | *empty* |
| `jellyfin_files` (default `.nfo`, `.jpg`, `.jpeg`, `.png`) | *Jellyfin data* |
| `ignored_files` (default `Thumbs.db`, `desktop.ini`, `.DS_Store`) | not shown, deleted with the folder |
| `subtitle_files` (default `.srt`, `.sub`, `.idx`, `.ass`, `.ssa`, `.vtt`, `.sup`) | *subtitle, no video* |
| sub-folders that are leftover folders themselves; those named in `jellyfin_folders` (default `extrafanart`, `extrathumbs`, `*.trickplay`) are labelled *Jellyfin data* | part of the parent |

Any other file (`.txt`, `.exe`, …) means the folder is **not** listed — it may hold something the user wants. A video is any file with a video extension (`VIDEO_EXTS`), not configurable. Transcode output is not included: it is temporary until the user moves the files into the library.

**Configuration** — `config.yaml`, editable in Settings (a “Leftover folders” tab, one comma-separated field per list, with a *Reset to defaults* button); a missing or partial section is merged with the defaults, like `transcode:` and `naming:`:

```yaml
leftover:
  jellyfin_files:   [.nfo, .jpg, .jpeg, .png]
  subtitle_files:   [.srt, .sub, .idx, .ass, .ssa, .vtt, .sup]
  ignored_files:    [Thumbs.db, desktop.ini, .DS_Store]
  jellyfin_folders: [extrafanart, extrathumbs, "*.trickplay"]
  ignored_folders:  ["@eaDir", ".@__thumb", "#recycle", "$RECYCLE.BIN", "System Volume Information"]
```

- File entries match case-insensitively on the whole file name (`Thumbs.db`, `.DS_Store`) or, when they start with a dot, on the extension (`.jpg`). Folder entries match the folder name; `*` is a wildcard.
- `jellyfin_folders` only sets the label: the files inside such a folder must still be on one of the file lists, and a video inside still blocks the whole tree.
- Video extensions can never be added to a list (refused on save).
- Defaults live in `DEFAULT_LEFTOVER` in `py/ui/settings.py`. Jellyfin saves posters/backdrops as `.jpg`; logos and clear art from fanart.tv are often `.png`.
- Changes apply at the next scan; the re-check before deleting always uses the current settings.

**Detection**
- **During every scan:** the scan already walks every grouping folder, so collecting them costs nothing extra. Only the topmost leftover folder of a tree is listed (with its sub-folders inside it). The list is saved in the per-location cache, so it is there at startup without a rescan.
- **After an action** (Move, Delete, bundle move, Reorganize, Use transcoded video): the server checks the source parent folders of the changed items on disk, up to the location root, and adds the leftovers it finds to the in-memory list — a few folders, no full scan. That gives the “N leftover folders — Review…” count right away.

**Out of scope (deliberate):** leftover *files* — orphaned sidecars in a folder that still holds videos (e.g. `Heat (1995).nfo` at a download root) — are not listed.

**Where it shows**
- A sidebar row **Leftover folders (N)** below the special filters (folders are not movies, so it is not a filter); clicking it opens the dialog.
- Result dialogs of Move, Delete, bundle moves and Reorganize show “N leftover folders — Review…” when the action left some behind (the Organize post-operation prompt does the same).

**Dialog**

```
Leftover folders
  ☐ M:\Movies\James Bond\               folder.jpg, James Bond.nfo
  ☐ D:\Downloaded\Netflix\Fury (2014)\   (empty)
  ☐ D:\Downloaded\Netflix\Heat (1995)\   movie.nfo, poster.jpg, Heat.en.srt (subtitle, no video)  ▸
  [All] [None]    ☑ Dry run
                                          [Delete selected]  [Close]
```

- Each row shows its content and expands to the full file list. **Nothing is ticked by default.**
- Dry run by default; a real run asks for confirmation inside the dialog.
- **Server re-check per folder before deleting:** still inside a configured location, not the location itself, not (containing) another configured folder, not inside a scanned movie or series folder, still no video below it, only files on the configured lists, and still exactly the files the user saw. Then the listed files are deleted one by one and the folders removed with `rmdir`, deepest first — never a recursive delete, so anything that appeared in the meantime makes that folder fail safely. Risk register: R-29.

The CLI `delete-empty-folders.py` keeps its own, stricter rules (empty, or a single `.nfo`).

## Bundle Display

When a Movie lives inside a Bundle folder, the library list shows the bundle name as a subtle secondary line below the title:

```
GoldenEye (1995)
James Bond
```

This is derived from the **Bundle Path** tracked by the scanner (its last element is the Bundle). See [bundles.md](./bundles.md).

## New `config.yaml` Settings

```yaml
settings:
  organize:
    delete_images_default: true   # Default for "Delete images" checkbox in Organize dialog
    warn_mixed_types: true        # Warn when organizing movies and series to the same destination
    picker_depth: 2               # Subdirectory levels shown in the destination picker
```

All three settings are editable in the Settings modal in the UI. (There is no NFO-rename setting: NFOs keep their name — `movie.nfo` or `<video>.nfo` — decided 2026-10-01.)
