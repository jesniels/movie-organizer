# Organisation Features

This document set describes the movie library and download folder organisation capabilities of Movie Organizer Web.

## Overview

The tool manages two categories of configured paths:

- **Library Locations** — destinations for organised, Jellyfin-ready content (e.g. `M:\Movies`, `M:\Series`)
- **Download Locations** — staging areas for newly downloaded, unorganised content (e.g. `D:\Downloaded\Netflix`)

The core workflow is: content lands in a Download Location → user selects items → **Organize** moves them into a Library Location with the correct folder structure → **Smart Scan** surfaces remaining issues (proposed bundles, duplicates, NFO gaps).

## Feature Documents

| Document | Description |
|---|---|
| [organize.md](./organize.md) | Organize action — move items to library with subfolder creation, NFO handling, and image management |
| [smart-scan.md](./smart-scan.md) | Smart Scan — deeper library analysis: proposed bundles, duplicates, NFO gaps |

## Other Actions

### Move

Raw relocation of an item to any configured path (Library or Download Location). No subfolder creation, no NFO renaming, no image handling. Useful for staging, moving back to downloads, or relocating to a transcoding folder.

The Move target picker lists all configured paths from `config.yaml` (both `locations:` and `downloads:`) plus a free-text custom path field.

### Rename

Renames an item's folder, video files, or both. Available as a context action on single items.

### Delete

Permanently removes an item's folder (and all contents) from disk. Only items whose path originates from a configured location can be deleted.

### Empty Folder Cleanup

Removes empty folders left behind after items are moved out of a Download Location. Available as a standalone action on any location and offered automatically in the post-Organize prompt when empty folders are detected.

## Bundle Display

When a Movie is nested more than one level deep within a Library Location (i.e. it lives inside a Bundle folder), the library list shows a subtle secondary breadcrumb line below the title:

```
GoldenEye (1995)
Action > James Bond
```

This is derived from the **Bundle Path** tracked by the scanner.

## New `config.yaml` Settings

```yaml
settings:
  organize:
    delete_images_default: true   # Default for "Delete images" checkbox in Organize dialog
    rename_nfo_default: true      # Default for "Rename NFO to movie.nfo" checkbox
    warn_mixed_types: true        # Warn when organizing movies and series to the same destination
    picker_depth: 2               # Subdirectory levels shown in the destination picker
```

All four settings are editable in the Settings modal in the UI.
