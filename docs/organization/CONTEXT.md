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
A folder inside a Library Location (never the location root) that holds at least one movie folder — one is enough. A folder holding only video files (e.g. `CD1`/`CD2` parts) is not a Bundle. Not a Series — has no season structure or episode files. Only the folder directly above a movie's own folder counts as its Bundle. Bundles exist only in Library Locations.
_Example_: `M:\Movies\James Bond\` containing `GoldenEye (1995)\`, `Casino Royale (2006)\`, etc.
_Avoid_: Collection folder, franchise folder, movie group

**Bundle Path**:
The sequence of intermediate grouping folders between a Library Location root and an item's own folder. Its last element is the item's Bundle. There are no category folders today, so it normally holds just the Bundle.
_Example_: For `M:\Movies\Action\James Bond\GoldenEye (1995)\` the Bundle Path is `Action > James Bond`; the Bundle is `James Bond`.
_Avoid_: Parent path, relative path, breadcrumb path

**Collection**:
The TMDB collection a movie belongs to, read from its NFO (`<set><name>…</name></set>`). The strongest signal for a Bundle Proposal. Not a folder — a Collection only becomes a Bundle when the user creates one.
_Example_: `James Bond Collection`
_Avoid_: Set (except when naming the NFO tag)

**Bundle Proposal**:
A suggestion, computed during every Scan, that related movies belong together: either a **new bundle** (two or more different movies, none in a Bundle) or **add to bundle** (a movie outside a Bundle related to an existing one). Each proposal states its reason (same Collection, or similar titles). One movie is never a Bundle.
_Avoid_: Bundle suggestion, smart bundle, proposed bundle finding

**Not Related**:
The user's remembered "no" to a Bundle Proposal: a record that a movie does not belong to a given Bundle or proposal. Reviewed and removed in Settings, like "not a duplicate" marks.
_Avoid_: Dismissed, skipped, ignored

**Loose File**:
A video file residing directly inside a location folder without its own dedicated subfolder.
_Avoid_: Unorganised file, flat file, raw file

**Leftover Folder**:
A folder inside a Library or Download Location, walked by the Scan as a grouping folder, with no video anywhere below it, holding nothing or only files from the configured `leftover:` lists (Jellyfin data such as `.nfo`/`.jpg`, system files such as `Thumbs.db`, orphaned subtitles). Never a folder inside a movie or series folder, never a configured location. Listed by every Scan; deleted only when the user selects it.
_Avoid_: Empty folder (it usually is not empty), orphan folder

**NFO Quality**:
A classification (`none` / `partial` / `full`) of how complete an item's NFO metadata file is for Jellyfin recognition. `full` requires at least a year and one external unique ID (tmdb/imdb/tvdb).
_Avoid_: NFO status, NFO completeness, metadata quality

### Operations

**Scan**:
A filesystem walk across all configured locations that discovers items, reads NFO files, updates the per-location cache, and then — from the data in memory — detects duplicates, missing episodes and Bundle Proposals. Runs in the background with one navbar indicator; started at startup (uncached locations only) or by Rescan.
_Avoid_: Basic Scan, Smart Scan (both dropped 2026-10-28 — there is only one scan), quick scan, refresh

**Organize**:
The operation of moving one or more selected items to a chosen Library Location, creating per-movie subfolders for Loose Files, optionally deleting image files (NFO files keep their name — `movie.nfo` or `<video>.nfo`), and presenting a per-item preview before execution.
_Avoid_: Move (Move is a separate, raw-relocation action), sort, transfer, migrate

**Move**:
A raw relocation of an item as-is to any configured path (Library Location or Download Location), with no subfolder creation, NFO renaming, or image handling. Exception: moving into a Bundle may create the Bundle folder and a Loose File's own folder (see `bundles.md`).
_Avoid_: Organize (Organize is the intelligent version with preview and file handling)

### UI Concepts

**Organize Dialog**:
The confirmation preview shown before executing an Organize operation, listing each planned move as a `FileName → FolderName` row with conflict warnings, per-row checkboxes, and option checkboxes.
_Avoid_: Move dialog, organize modal, confirmation modal

**Conflict**:
A state in the Organize Dialog where the planned destination folder already exists at the target location. Shown in red with an auto-suggested alternative name; the row checkbox is unchecked by default requiring explicit user approval.
_Avoid_: Collision, duplicate folder, name clash

---

## Example dialogue

> **Dev**: "Should I put the Bond films in a Series folder?"
>
> **Domain expert**: "No — James Bond is a Bundle. A Series has season folders and episode files; a Bundle is just related Movies grouped together under one folder. No `S01/` structure."
>
> **Dev**: "Got it. And when I Organize `GoldenEye (1995).mkv` from Downloads into the Bond Bundle, does the Organize action create the `James Bond/` folder?"
>
> **Domain expert**: "The destination picker lets you navigate into `M:\Movies\James Bond\` — that Bundle folder may already exist. The Organize action creates the per-movie subfolder `GoldenEye (1995)/` inside it, not the Bundle itself."
>
> **Dev**: "What if `GoldenEye (1995)/` already exists there?"
>
> **Domain expert**: "That's a Conflict. The Organize Dialog flags the row in red, suggests `GoldenEye (1995) (1)`, and leaves the checkbox unchecked. The user decides."
>
> **Dev**: "How do I find other Bond films that need bundling?"
>
> **Domain expert**: "Nothing to trigger — every Scan computes Bundle Proposals. *No Time to Die* in Downloads has `James Bond Collection` in its NFO, so it shows up under *Possible bundles* as 'add to bundle'. If it didn't belong there, the user marks it Not Related and it stays out."
