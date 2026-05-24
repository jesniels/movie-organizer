# Smart Scan

Smart Scan is a deeper library analysis pass that runs separately from the Basic Scan. It surfaces intelligence about the library that a simple filesystem walk cannot provide.

---

## Triggering

Click the **Smart Scan** button in the navbar.

**While Smart Scan is running:**
- A dedicated progress indicator appears in the navbar, separate from the Basic Scan badge.
- A **Cancel** button stops the analysis immediately.
- The **Rescan** button is disabled — stop Smart Scan first if a Basic Scan is needed (see [ADR 0002](../adr/0002-smart-scan-blocks-basic-rescan.md)).

The two navbar indicators are independent:

```
[● Scan: Idle]  [▶ Smart Scan: Analysing… ✕]  [Rescan ⊘]
```

---

## Findings

| Finding | Description |
|---|---|
| **Proposed Bundles** | Groups of movies with shared title prefixes or sequel patterns that could be organised into a Bundle folder |
| **Duplicates** | Movies appearing more than once across all Library and Download Locations |
| **NFO Gaps** | Items with missing or incomplete NFO data (`nfo_quality` of `none` or `partial`) |

Additional finding types will be added in future phases (e.g. ffprobe codec/quality analysis).

---

## Sidebar Section

Results are shown in a dedicated **Smart Scan** section in the left sidebar:

```
─── Smart Scan ──────────────────── [Clear]
Last run: 2026-05-24 14:32

  Proposed Bundles       3  ›
  Duplicates             2  ›
  NFO Gaps              11  ›
```

- Each finding type is a clickable row that opens the corresponding action dialog.
- The **Clear** button removes all Smart Scan results from the sidebar and from the persisted cache.
- If no Smart Scan has been run yet (or results were cleared), the section shows: _"No Smart Scan results. Click Smart Scan to analyse your library."_

---

## Staleness Warning

If the Basic Scan was run after the last Smart Scan (i.e. the library may have changed), the sidebar section header and any open finding dialogs show:

> ⚠ Results may be outdated — library has changed since last Smart Scan.

The warning is based on timestamps: `last_scan > last_smart_scan`.

---

## Persistence

Smart Scan results are saved to `web/cache/smart-scan.json` on completion and reloaded on application startup. Results survive application restarts and are shown in the sidebar immediately on load, alongside the last-run timestamp.

The cache file is separate from the per-location library cache files.

---

## Action Dialogs

Each finding type opens its own focused dialog when clicked in the sidebar:

### Proposed Bundles

Lists each proposed bundle group with its member movies:

```
Back to the Future  (3 movies)
  ☑ Back to the Future (1985)         M:\Movies
  ☑ Back to the Future Part II (1989) M:\Movies
  ☑ Back to the Future Part III (1990) M:\Movies
  Bundle name: [Back to the Future        ]
  [Create Bundle]   [Skip]

James Bond  (5 movies)
  …
```

- The user can accept the proposed bundle name or edit it.
- Clicking **Create Bundle** triggers the Organize action for those movies into a new bundle subfolder.
- Clicking **Skip** dismisses that proposal without action.

### Duplicates

Lists each duplicate group with location and path for each copy:

```
Inception (2010)  — 2 copies
  ☑ M:\Movies\Inception (2010)\         library
  ○ D:\Downloaded\Netflix\Inception (2010)\ download

[Delete selected]   [Skip]
```

- The user selects which copy or copies to delete.
- At least one copy must remain (the dialog prevents selecting all).

### NFO Gaps

Lists items with `nfo_quality` of `none` or `partial`, showing what is missing (no year, no external ID, etc.). Action for resolving NFO gaps is to be defined in a future phase.

---

## Bundle Proposal Algorithm

The algorithm normalises movie titles by:
1. Stripping file extensions, quality tags, and year markers.
2. Removing sequel indicators: `Part 1 / Part I / Part One`, Roman numerals, plain trailing numbers, and colon-separated subtitles.
3. Grouping items that share the same normalised base title.

Groups with fewer than two members are not proposed. The proposed bundle name is the shared base title in title case.
