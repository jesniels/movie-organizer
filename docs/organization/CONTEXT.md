# Movie Organizer

A web-based tool for managing a personal movie and series library across multiple storage locations, supporting organised folder structures compatible with Jellyfin.

## Language

### Locations

**Library Location**:
A top-level path in `config.yaml` representing a destination for organised, Jellyfin-ready content.
_Avoid_: Library folder, media path, destination path

**Download Location**:
A top-level path in `config.yaml` representing a staging area for newly downloaded, unorganised content.
_Avoid_: Source folder, incoming folder, input path

### Items

**Movie**:
A single film, represented either as a Loose File or as an organised folder containing the film's files.
_Avoid_: Film (use Movie throughout)

**Series**:
A TV show with episodes organised into season folders (`S01/`, `S02/`, …) under a single top-level series folder.
_Avoid_: TV show, show, TV series

**Bundle**:
A grouping folder inside a Library Location that contains multiple related individual movie folders. Not a Series — has no season structure or episode files.
_Example_: `M:\Movies\James Bond\` containing `GoldenEye (1995)\`, `Casino Royale (2006)\`, etc.
_Avoid_: Collection folder, franchise folder, movie group

**Bundle Path**:
The sequence of intermediate grouping folders between a Library Location root and an item's own folder, used to render breadcrumb navigation in the UI.
_Example_: For `M:\Movies\Action\James Bond\GoldenEye (1995)\` the Bundle Path is `Action > James Bond`.
_Avoid_: Parent path, relative path, breadcrumb path

**Loose File**:
A video file residing directly inside a location folder without its own dedicated subfolder.
_Avoid_: Unorganised file, flat file, raw file

**NFO Quality**:
A classification (`none` / `partial` / `full`) of how complete an item's NFO metadata file is for Jellyfin recognition. `full` requires at least a year and one external unique ID (tmdb/imdb/tvdb).
_Avoid_: NFO status, NFO completeness, metadata quality

### Operations

**Basic Scan**:
A fast filesystem walk across all configured locations that discovers items, reads NFO files, detects duplicates and missing episodes, and updates the per-location cache.
_Avoid_: Scan, rescan, quick scan, refresh

**Smart Scan**:
A slower, deeper analysis pass that surfaces proposed Bundles, duplicate movies, and NFO Gaps across the library. Runs separately from the Basic Scan; results are persisted independently.
_Avoid_: Deep scan, analysis scan, enhanced scan, library scan

**Organize**:
The operation of moving one or more selected items to a chosen Library Location, creating per-movie subfolders for Loose Files, optionally renaming NFO files and deleting image files, and presenting a per-item preview before execution.
_Avoid_: Move (Move is a separate, raw-relocation action), sort, transfer, migrate

**Move**:
A raw relocation of an item as-is to any configured path (Library Location or Download Location), with no subfolder creation, NFO renaming, or image handling.
_Avoid_: Organize (Organize is the intelligent version with preview and file handling)

### UI Concepts

**Organize Dialog**:
The confirmation preview shown before executing an Organize operation, listing each planned move as a `FileName → FolderName` row with conflict warnings, per-row checkboxes, and option checkboxes.
_Avoid_: Move dialog, organize modal, confirmation modal

**Conflict**:
A state in the Organize Dialog where the planned destination folder already exists at the target location. Shown in red with an auto-suggested alternative name; the row checkbox is unchecked by default requiring explicit user approval.
_Avoid_: Collision, duplicate folder, name clash

**Staleness Warning**:
A notification displayed in Smart Scan results when the Basic Scan timestamp is newer than the Smart Scan timestamp, indicating the analysis may no longer reflect the current library state.
_Avoid_: Outdated warning, stale results

---

## Example dialogue

> **Dev**: "Should I put the Bond films in a Series folder?"
>
> **Domain expert**: "No — James Bond is a Bundle. A Series has season folders and episode files; a Bundle is just related Movies grouped together under one folder. No `S01/` structure."
>
> **Dev**: "Got it. And when I Organize `GoldenEye (1995).mkv` from Downloads into the Bond Bundle, does the Organize action create the `James Bond/` folder?"
>
> **Domain expert**: "The destination picker lets you navigate into `M:\Movies\Action\James Bond\` — that Bundle folder may already exist. The Organize action creates the per-movie subfolder `GoldenEye (1995)/` inside it, not the Bundle itself."
>
> **Dev**: "What if `GoldenEye (1995)/` already exists there?"
>
> **Domain expert**: "That's a Conflict. The Organize Dialog flags the row in red, suggests `GoldenEye (1995) (1)`, and leaves the checkbox unchecked. The user decides."
>
> **Dev**: "Should I trigger a Basic Scan or a Smart Scan to find other Bond films that need bundling?"
>
> **Domain expert**: "Smart Scan — it detects Bundle proposals from title patterns. Basic Scan just discovers files; it won't propose groupings."
