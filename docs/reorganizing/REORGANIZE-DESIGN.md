# Reorganize — Feature Design

Status: **Phase 1 (Naming Format settings) and Phase 2 (analysis, `py/ui/reorganize.py`) implemented 2026-09-29; execution and dialog not implemented.** Companion plan: [REORGANIZE-PLAN.md](REORGANIZE-PLAN.md)

> **Ground rule: Reorganize never changes anything on its own.** Settings and the analysis only decide what is *suggested*. A file or folder is renamed, moved or deleted only after the user selects that suggestion in the Reorganize Dialog and clicks **Do it** with dry run switched off.

## Summary

Bring the CLI organizer's "loose files into `Title (Year)` folders" capability into the web UI, and extend it: a configurable **Naming Format** in Settings, an analysis over the already-scanned items that finds **candidates** (loose files needing folders *and* existing folders/files that do not follow the standard), a **Sanitize** check over every file and folder in the library locations and download folders (illegal characters, trailing dots/spaces), and a **Reorganize Dialog** where the user reviews, edits, selects and executes the proposed changes — including renaming video files and NFO files.

## Terminology (extends [organization/CONTEXT.md](../organization/CONTEXT.md))

| Term | Meaning |
| :--- | :--- |
| **Reorganize** | Standardizing items **in place**: creating a folder for a Loose File, renaming a non-conforming folder, renaming video/NFO files to the Naming Format, sanitizing illegal names. Never moves an item to another location — that is **Organize** (move to a Library Location) or **Move** (raw relocation). |
| **Naming Format** | The configurable templates (Settings) that define what a compliant folder / file name looks like, e.g. `{title} ({year})`. |
| **Sanitize rule** | The one rule that makes a name legal: `:` → ` - `, other illegal characters (`\ / * ? " < > \|`, control chars) dropped, trailing dots/spaces stripped. Nothing else changes. Used for titles before expansion *and* for existing names (`settings.sanitize_component`). |
| **Sanitize** | Renaming an existing file or folder with the Sanitize rule only (no format applied). Applies to every file and folder in the locations/downloads, including episodes and season folders. |
| **Proposal** | The set of concrete changes Reorganize suggests for one item (create folder, rename folder, rename file, resolve several NFOs, delete images, sanitize names). |
| **Compliant** | An item whose folder name, video file name and NFO name already match the Naming Format and whose files/folders all have legal names — nothing to propose. |
| **Blocked** | An item Reorganize cannot safely propose for (see [Blocked items](#blocked-items)). Shown with a reason; never silently skipped. |

_Avoid_: "cleanup", "normalize", "fix names" — use Reorganize.

---

## Naming Format (Settings)

New config section (defaults in `DEFAULT_NAMING` in `py/ui/settings.py`, merged by `load_config()` like `transcode:`):

```yaml
naming:
  movie_folder:  "{title} ({year})"   # folder name of an organised movie
  movie_file:    "{title} ({year})"   # video file stem (extension kept)
  series_folder: "{title} ({year})"   # series folder name (Jellyfin recommendation; was "{title}")
  file_equals_folder: true            # movie_file follows movie_folder; default for the dialog's per-row "= folder" toggle
  rename_files:   true                # suggest video file renames at all
  resolve_nfos:   true                # suggest fixing NFO names (lone NFO with another name; several NFOs)
  delete_images:  true                # suggest deleting jpg/jpeg/png when foldering a Loose File (Jellyfin regenerates)
  sanitize_names: true                # suggest sanitizing illegal names of the analysed items' files/folders
```

Every switch only controls whether something is **suggested** — never whether it is done.

**Placeholders:** `{title}` and `{year}` only.
**Value sources:** NFO `title`/`year` are authoritative per field when present; otherwise they are parsed from the folder name (then the video file stem) by `reorganize.parse_name`, which also understands scene names: dots/underscores → spaces, a bare `19xx`/`20xx` year (not at the start, not after a quality tag, not in the future), quality tags stripped — `Inception.2010.1080p.BluRay` → `Inception`, `2010`; `Blade.Runner.2049.2017.1080p` → `Blade Runner 2049`, `2017`; `1917.2019.720p` → `1917`, `2019`. The proposal reports `source` = `nfo` / `name` / `nfo+name` so the user can judge it. If the format uses `{year}` and no year is known, the item is **Blocked** — never emit `Title ()`.
**Title sanitizing:** before expansion the title is trimmed and passed through the **Sanitize rule** — `Alien: Covenant` → `Alien - Covenant (2017)`, `Face/Off` → `FaceOff (1997)`.

**Validation (on save):**
- `movie_folder` / `movie_file` / `series_folder` must contain `{title}`; unknown placeholders are rejected.
- Expanded names must be a single path component: no `\ / : * ? " < > |`, not `.`/`..`, no trailing dot/space (Windows), not a reserved device name. The same check (`settings.valid_component`) is used by the Rename dialog.
- Settings tab shows a **live preview** ("Alien (1979)") as the user types.

**Settings UI:** a dedicated **Naming** tab in Settings (decided 2026-09-28). At the top a highlighted statement: *“These settings never change any files. They only decide what the Reorganize dialog suggests …”*. All checkboxes are labelled “Suggest …”. Below each format field: an explanation of the supported placeholders (`{title}`, `{year}`) and naming rules, plus the live preview. A **“File name = folder name”** checkbox (stored as `file_equals_folder`) keeps `movie_file` synced to `movie_folder` (the file field is disabled while checked); it is also the default for the dialog's per-row toggle. Unchecking `rename_files` greys the file-format field and removes file-rename proposals from the analysis. A **“Sanitize names”** checkbox (`sanitize_names`) has an explanation below it stating exactly what happens: every file and folder in the locations/downloads is checked, illegal names are offered for sanitizing with the Sanitize rule, `SxxEyy` stays intact, same-stem subtitles/NFOs follow their video, nothing changes until executed in the dialog. The series-folder help text states that episodes and season folders are never renamed to a format but are offered for sanitizing. A note states that changing the format never touches files — it only changes what the analysis proposes.

---

## Analysis — finding candidates

Runs **when the Reorganize Dialog is opened** (right away) or its Refresh button is clicked, and **only on the items currently shown by the left-bar filters** (location, movies/series, search text, special filter) — the dialog sends their ids. It is **never triggered by anything else** (not by a Basic Scan, not by saving Settings), refused while a scan is running, and it **only reads** the disk. Changing the Naming Format therefore requires no rescan, just reopening the dialog. Transcoded items are never analysed.

Name comparisons are case-insensitive (Windows), and compare against the format expanded with the item's title/year.

### Checks per movie item

| # | Condition | Proposal |
| :--- | :--- | :--- |
| 1 | **Loose File** (id ≠ path) | Create `<movie_folder>` next to the file and move the video + its exact-stem sidecars into it (stem rules identical to the CLI organizer, see R-09). The video is renamed to `<movie_file>` when `rename_files`; sidecars (incl. `<video>.nfo`) follow the new stem. Exact-stem images are suggested for deletion when `delete_images`, otherwise moved along. Illegal characters in the moved names are sanitized as part of the move (visible in the move list). Several loose files that would get the same folder name (e.g. Part 1 / Part 2) each get their own suggestion; the second one is a **Conflict**. |
| 2 | Folder name ≠ `movie_folder` format | Rename the folder. |
| 3 | Video file stem ≠ `movie_file` format (single-video movies only, when `rename_files`) | Rename the video file; exact-stem sidecars (incl. `<video>.nfo`) follow, keeping their suffix chain (`Old.en.srt` → `New.en.srt`). |
| 4 | (when `resolve_nfos`) **a)** exactly one top-level `.nfo`, named neither `movie.nfo` nor `<video>.nfo` (Jellyfin ignores it) — **b)** several top-level `.nfo` files | **a)** Rename it to `<video>.nfo` (the video's name after any check-3 rename). **b)** Offer a choice, default **leave as is**: *use the best* (delete the others; the best keeps its name when it is `movie.nfo` or `<video>.nfo`, otherwise it is renamed to `<video>.nfo`), *delete all* (Jellyfin recreates it on its next scan), *leave as is* (resolve manually). Best = highest quality (full > partial > none), then most filled fields; on a tie *use the best* is not offered. |

**NFO naming rule:** an NFO named `movie.nfo` or `<video>.nfo` already works for Jellyfin and is **never renamed** (decision #18) — except that `<video>.nfo` follows its video when the video itself is renamed, so it keeps matching. Reorganize **never creates or suggests `movie.nfo`**. A single NFO with any other name gets a rename suggestion to `<video>.nfo` (4a); several NFOs get the choice (4b).

A single item can combine 2+3+4 into one Proposal. Multi-video movie folders get folder renames only — file renames are **Blocked** for them (extras/parts, see R-15).

### Checks per series item

| # | Condition | Proposal |
| :--- | :--- | :--- |
| 5 | Series folder name ≠ `series_folder` format | Rename the series folder. |

Episode files, season folders and per-episode NFOs are **never renamed to a format** — that risks destroying `SxxEyy` naming (risk R-05). They **are** covered by the Sanitize check below, which only removes what is illegal. The dialog states this explicitly.

### Check 6 — Sanitize (all files and folders, when `sanitize_names`)

Scope: **every file and folder inside the analysed items** (the item folder itself, all sub-folders such as season folders and extras, all files; for a Loose File the file and its exact-stem sidecars) **plus the folders above them** up to — not including — the configured location/download folder (e.g. a grouping folder `Marvel `). The transcode output folder is never included. Files that belong to no item are not analysed (they can't be matched by the filters).

| Condition | Proposal |
| :--- | :--- |
| A name contains `\ / : * ? " < > \|` or a control character, or ends with a dot or space. For **files** the check applies to the stem (name before the extension): `Show S01E01 .mkv` is flagged; for **folders** to the whole name. | Rename with the Sanitize rule applied to the stem (files, extension kept) or the name (folders). |

- **Grouping:** each entry is attached to the item it belongs to; the folders above the items are listed once in a **“Parent folders”** group. Each sanitize entry is individually selectable.
- **Sidecars follow:** when a video is sanitized, its exact-stem sidecars (`.srt`, `.nfo`, …) are renamed with it, keeping their suffix chain (`Show S01E01 .en.srt` → `Show S01E01.en.srt`), so Jellyfin keeps matching them.
- **Format wins:** when checks 1–5 already rename the same file/folder, the sanitize entry is dropped (the format result is legal by construction).
- **Order:** sanitize renames run deepest path first (children before their parent folder), then the item's format renames.
- **Windows note:** NTFS cannot hold `: * ? " < > |`, so on a Windows backend the check mostly finds trailing dots/spaces (and names on shares from a Linux NAS may appear mangled). On a Linux/NAS backend (server mode) all illegal characters are found. Names ending with a dot/space are only reachable on Windows via the `\\?\` extended path prefix — the analysis reads the disk that way (`reorganize._fs`) and execution must too.
- **Scanner (fixed 2026-10-01):** the scanner used to skip folders whose name ends with a space/dot on Windows, so items below them were missing from the scan and the analysis. It now finds them (plan Phase 2b); see risk R-16 for the remaining limitation of Move/Rename/Delete on such items.

### Blocked items

Shown in the dialog under a collapsed "Cannot propose" section, each with its reason:

- No year available while the format requires `{year}` → *"Add a year (NFO) first"*.
- Multi-video movie folder (file rename part only) → *"Contains N video files — rename manually"*.
- Proposed target already exists and the user has not edited/approved it → **Conflict** row (red, unchecked by default, `(1)` suffix suggested — same rules as the Organize Dialog in [organize.md](../organization/organize.md)).
- Item's on-disk state no longer matches the cache (extra/missing videos) → *"Rescan first"* (stale-cache guard, R-06). Blocks checks 1–5 only; sanitize entries are computed from disk and still offered.
- Several NFOs are **not** blocked — they get the check-4 choice (default leave as is).
- Sanitize result is empty (name consisted only of illegal characters) → *"No legal characters left — rename manually"*.
- Sanitize target already exists (e.g. `A.mkv` and `A .mkv` side by side) → **Conflict** row, same handling as above.
- Sanitizing would change the `SxxEyy` token (defensive — cannot happen with the Sanitize rule) → *"Episode number would change — rename manually"*.

### Proposal shape (API, as implemented)

```jsonc
// POST /api/reorganize/proposals   { "item_ids": [ ...ids shown by the filters... ] }
{
  "generated": "2026-09-29T12:00:00",
  "analysed_count": 426,
  "compliant_count": 412,
  "proposals": [
    {
      "item_id": "D:\\Downloaded\\Netflix\\Dune.2021.2160p.mkv",
      "type": "movie", "kind": "loose_file",         // loose_file | movie_folder | series
      "location": "download", "path": "…", "title": "Dune", "year": "2021",
      "source": "nfo",                                   // nfo | name | nfo+name
      "folder": { "from": null, "to": "Dune (2021)", "create": true, "conflict": false, "suggested": null },
      "file":   { "from": "Dune.2021.2160p.mkv", "to": "Dune (2021).mkv", "conflict": false,
                  "follows": [{ "from": "Dune.2021.2160p.en.srt", "to": "Dune (2021).en.srt" }] },
      "move":   [{ "from": "Dune.2021.2160p.mkv", "to": "Dune (2021).mkv" }, …],   // loose files only
      "images": ["Dune.2021.2160p-poster.jpg"],          // suggested for deletion (delete_images)
      "nfo":    null,                                    // 4a: { "from": "Heat.1995.1080p.nfo", "to": "Heat (1995).nfo", "conflict": false }
      "nfos":   null,                                    // or the 4b choice:
      // { "files": [{name, after, quality, fields}], "best": "movie.nfo"|null, "target": "<video>.nfo",
      //   "rename_best": false, "options": ["use_best", "delete_all", "leave"], "default": "leave", "detail": null }
      // target = the best's own name when it is movie.nfo / <video>.nfo (rename_best false), else <video>.nfo
      "sanitize": [
        { "path": "…\\Show\\Season 1\\Show S01E01 .mkv", "kind": "file", "current": "Show S01E01 .mkv",
          "proposed": "Show S01E01.mkv", "follows": [{ "from": "Show S01E01 .en.srt", "to": "Show S01E01.en.srt" }],
          "conflict": false }
      ]
    }
  ],
  "parent_folders": [ /* sanitize entries for folders above the items, same shape */ ],
  "blocked": [ { "item_id": "…", "path": "…", "reason": "no_year", "detail": "…" } ]
}
```

Blocked reasons: `no_year`, `multi_video`, `stale_cache`, `sanitize_empty`, `episode_changed`. Conflicts are flagged on the operation (`conflict: true`, folder conflicts with a `suggested` “(1)” name), not blocked.

---

## UI

### Entry point

A **Reorganize** button in the top toolbar (next to Rescan/Settings). Opening it runs the analysis right away on the items shown by the current filters and shows the dialog — nothing runs in the background and nothing fires after scans (decision #1). A badge on the button shows the count from the most recent analysis in this session (absent before the first one; it may go stale until the dialog is opened again). No selection is required; the left-bar filters decide the scope.

### Reorganize Dialog

```
Reorganize — 14 proposals, 3 blocked, 412 compliant          [location filter ▾]

[All] [None]                                              ☑ dry run (default on)

☑  Inception.2010.1080p.mkv        →  📁 [Inception (2010)        ]  (new folder, file+NFO renamed, 1 image deleted)
☑  the dark knight                 →  📁 [The Dark Knight (2008)  ]  (folder renamed)
⚠☐ GoldenEye (1995) [exists]       →  📁 [GoldenEye (1995) (1)    ]  (conflict — approve or edit)
▸ Cannot propose (3)…

                                              [Preview / Do it]  [Close]
```

- **Editable proposal** — **two text inputs per row: folder name and file name** (stem; extension kept). A per-row **“= folder”** toggle keeps the file name identical to the folder name (default from `file_equals_folder`), and file renaming can be switched off per row (or globally via `rename_files`). Editing re-runs the single-component validation and the conflict check live. An expander per row shows the exact resulting operations (folder / file / sidecars / NFO / images / sanitize).
- **Sanitize entries** — listed in the row's expander with their own checkbox (current → sanitized name, followers shown); a “Parent folders” group holds the folders above the items. A header counter shows the number of sanitize entries.
- **Several NFOs** — a per-row choice *use the best* / *delete all* / *leave as is*, preset to **leave as is**; the NFO list shows each file's quality so the user can judge.
- **Images** — image deletion is its own tickable entry per row, so it is approved separately from creating the folder.
- **Select all / none / individual** — per-row checkboxes; conflict rows unchecked by default (same convention as the Organize Dialog).
- **Dry run** — checked by default (repo convention). "Preview" returns per-row results without touching disk; unticking and clicking "Do it" executes.
- **Execution** — sequential, with a progress state per row (pending → done/failed + reason). No rollback; already-completed rows stay done ([ADR 0003](../adr/0003-cancel-no-rollback-delete-partial-files.md) precedent). On completion, prompt to run a Basic Scan (same pattern as "Use transcoded video").
- **Settings hint** — a footer link: "Naming format: `{title} ({year})` — change in Settings".

### API

| Endpoint | Purpose |
| :--- | :--- |
| `POST /api/reorganize/proposals` | `{ item_ids }` — the items shown by the filters. Build and return the analysis (above). Read-only; 409 while a scan is running. |
| `POST /api/reorganize` | `{ changes: [{item_id, folder_name, file_name, apply_folder, apply_file, apply_nfo, nfo_action: "use_best"\|"delete_all"\|"leave", delete_images, sanitize: [paths]}], dry_run }` (`item_id` null for “Parent folders”) → per-item results. Names are re-validated and conflict-checked server-side at execute time, and sanitize targets are recomputed server-side from the path — the client's view is advisory only. |

Backend module: `py/ui/reorganize.py` (analysis + execution), thin routes in `app.py`, request models in `models.py` — matching the existing package split.

---

## Safety (must be registered in [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) during implementation)

| Guard | Rule |
| :--- | :--- |
| User approval | Nothing is ever changed without the user selecting it in the dialog and executing with dry run off. The analysis is read-only; settings only control suggestions; defaults never pick a destructive option (several NFOs default to *leave as is*; conflict rows start unticked). |
| Never overwrite | Every rename/move checks `dst.exists()` first and refuses (R-03 rule). `shutil.move` is never called without it. |
| Exact-stem sidecars | Related files are exact stem or stem + `.`/`-` — identical to the CLI fix for R-09 (organizing `Alien` must not grab `Aliens.mkv`). |
| Single path component | Proposed names re-validated server-side like `rename_item` (R-04): no separators, no `.`/`..`, refuse existing targets. |
| Loose Files act on the file | Via `_item_target(item)` semantics (R-01/R-02) — never `item["path"]` for a Loose File. |
| Stale cache | Before executing, the item's folder is re-listed; unknown video files ⇒ refuse with "rescan first" (R-06). |
| Series | Folder rename to the format only — episode files and season folders are never renamed to a format (R-05); they are only sanitized, and sanitizing must leave the `SxxEyy` token identical (verified server-side). |
| Sanitize | Only the Sanitize rule is applied — the server recomputes the target from the current name, never trusts a client-supplied name. Refuse when the result is empty or the target exists. Sidecars follow via the exact-stem rule. Deepest paths first. |
| NFOs | Never renames to or creates `movie.nfo`; a single `movie.nfo` / `<video>.nfo` is never touched; a single NFO with another name is only renamed to `<video>.nfo` after explicit selection. With several NFOs, *use the best* renames the best to `<video>.nfo` and deletes the others, *delete all* deletes them — both only after explicit selection; re-checked at execute time (same files as analysed, else refuse). |
| Scope | Only paths inside configured locations/downloads are ever touched. |
| Dry run | Default on; the execute endpoint honours it per request, not per session. |
| Image deletion | Only images matched by the exact-stem rule of the item being foldered — by design (Jellyfin regenerates), same as the CLI. |

## Relationship to existing features

- **Organize** ([organize.md](../organization/organize.md), not yet implemented) moves items *between* locations and also creates folders for Loose Files. Reorganize is the **in-place** counterpart. They share the folder-name derivation and conflict rules; a future Organize implementation should reuse `reorganize.py`'s name-building and validation helpers.
- **Rename dialog** (existing) is the manual single-item tool; Reorganize is the bulk, format-driven version. Both must keep the R-04/R-05 guards. Reorganize reuses/extends `fileops.rename_item`'s planning logic rather than duplicating it.
- **CLI `movie-organizer.py`** stays unchanged and stdlib-only. The UI feature replicates its foldering behaviour (folder from stem/NFO, image deletion, skip-on-existing) with the additions of format compliance, folder/file renaming and sanitizing. **Difference:** the CLI renames the NFO to `movie.nfo`; Reorganize never does — NFOs keep the video's name (decision #13).

## Out of scope (v1)

- Episode/season renaming **to a format** (R-05). Sanitizing their illegal names is in scope (check 6).
- Moving items between locations (that is Organize).
- Fixing NFO contents (title/year) — Blocked items point the user at the NFO tools instead.
- Resolving duplicate/redundant NFOs automatically.
- Bundles: a Bundle Path is preserved as-is; only the item's own folder is renamed.

## Decisions (2026-09-28)

1. **Analysis is on demand only — never automatic.** It runs when the Reorganize Dialog is opened or refreshed; there is no post-scan hook and no background analysis. The toolbar badge only reflects the most recent analysis of the session.
2. **Settings: dedicated “Naming” tab**, with the supported placeholders and naming rules explained directly below each format field, plus a live preview.
3. **Folder and file names are independent.** The config keeps separate `movie_folder` / `movie_file` templates; the dialog has two fields per row with a “file name = folder name” sync option, and file renaming can be disabled per row and globally (`rename_files`).
4. **Server mode: no special handling needed.** Reorganize executes on the machine running the backend — which is where the files are (the server/NAS in server mode, the local machine otherwise). Local shares only affect playing and are irrelevant here.

## Decisions (2026-09-29, Phase 1)

5. **Illegal characters in titles are sanitized**, not blocked: `:` → ` - `, other illegal characters dropped, trailing dots/spaces stripped.
6. **One single-component validator** (`settings.valid_component`) for Rename and Reorganize — Rename became stricter accordingly.
7. **`file_equals_folder` is a config key** (default `true`) and the default for the dialog's per-row “= folder” toggle, which can be overridden per row.
8. **Sanitize check covers every file and folder** in the library locations and download folders — including episodes, season folders, sidecars and extras — with an “Other files” group for entries outside scanned items. Switch: `sanitize_names` (default `true`), explained in the Settings Naming tab.
9. **One Sanitize rule everywhere** — exactly decision 5 (`:` → ` - `, other illegal characters dropped, trailing dots/spaces stripped), for titles and existing names alike; no extra normalisation (no whitespace collapsing). For files it applies to the stem, so the extension is kept.
10. **Video sidecars follow a sanitized video** (exact-stem rule) so Jellyfin keeps matching subtitles/NFOs.

## Decisions (2026-09-29, Phase 2)

11. **Nothing is ever automatic** — stated at the top of this design, in the Settings Naming tab and as the first Safety guard. All setting labels read “Suggest …”.
12. **Analysis starts right away when the dialog opens** and covers **only the items shown by the left-bar filters**. Sanitize covers those items' files/folders plus the folders above them; the “Other files” group is dropped.
13. **NFOs are named after the video — never `movie.nfo`.** A single NFO (`movie.nfo` from Jellyfin or `<video>.nfo`) is left alone. Several NFOs → choice *use the best* (rename to `<video>.nfo`, delete the others) / *delete all* / *leave as is* (default). Config key `rename_nfo` replaced by `resolve_nfos`.
14. **Series default format is `{title} ({year})`** (Jellyfin recommendation).
15. **Own name parser** (`reorganize.parse_name`) for items without NFO values, understanding scene names.
16. **Loose files in the same folder that map to the same movie folder** (e.g. parts) each get their own suggestion — no special blocking; the second becomes a conflict.
17. **A lone NFO with neither `movie.nfo` nor the video's name** (Jellyfin ignores it) gets a rename suggestion to `<video>.nfo` (2026-10-01; amends #13).
18. **An NFO named `movie.nfo` or `<video>.nfo` is never renamed** — it works for Jellyfin. Applies to *use the best* too: the best only gets the video's name when it has another name (2026-10-01; amends #13).
