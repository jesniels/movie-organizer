# Bundles

A **Bundle** is a folder in a Library Location that holds related movies — e.g. `M:\Movies\James Bond\GoldenEye (1995)\`. The scan finds movies that belong together and suggests creating a bundle or adding a movie to an existing one. **Nothing is moved automatically:** every move is chosen by the user and runs as a dry run by default.

> **Replaces the former Smart Scan design (decided 2026-10-28).** There is no separate analysis pass, button or second indicator — bundle detection is part of every scan ([ADR 0002](../adr/0002-smart-scan-blocks-basic-rescan.md) is superseded). The other two Smart Scan findings already exist as special filters: *Duplicates* and *Incomplete NFO*.

---

## What is a bundle

- A **bundle** is a folder in a Library Location (never the location root) that holds **at least one movie folder**, or **at least two different movies as loose files**. `M:\Movies\James Bond\GoldenEye (1995)\` makes `James Bond` a bundle, even with only that one movie in it; so does `M:\Movies\James Bond\` holding just `GoldenEye (1995).mkv` and `Skyfall (2012).mkv` (decided 2026-10-28 — otherwise the scan would propose a *New bundle* "James Bond" that *Create bundle* must refuse because the folder exists).
- **Different** movies means different title + year, as in rule 4: the parts of one movie (`Heat - CD1.mkv` / `CD2`) and Jellyfin multi-version files (`Heat (1995) - 1080p.mkv` / `- 2160p.mkv`) are one movie. The scan splits such a folder into loose files, but `Heat (1995)\` stays that movie's folder, not a bundle. A loose file lying directly in a bundle folder (next to movie folders, R-13, or next to other movies' files) is a member of that bundle.
- A bundle sits directly in the Library Location root; a movie's bundle is the folder directly above its own folder (or above the loose file). A movie deeper down (`M:\Movies\A\B\Movie (2000)\`) has **no** bundle: `B` is not directly in the root and `A` holds no movie folder. It is treated like any movie outside a bundle (it can be proposed).
- Bundles exist only in **Library Locations**. A common folder in a Download Location is not a bundle, but movies in Downloads can be suggested for a bundle.
- Movies only — series and transcoded items are never part of a bundle proposal.

---

## Detection — part of every scan

Runs at the end of every scan and wherever duplicates are recomputed (after Move, Delete, *Use transcoded video*, a not-related change), on the items already in memory — **no disk access**. The cost is expected to be negligible next to the scan (one pass grouping a few thousand titles). If it turns out to be noticeable, it gets revisited (e.g. a setting).

### Signals (strongest first)

| # | Signal | Source |
| :--- | :--- | :--- |
| 1 | **Collection** — `<collectionnumber>` (TMDB collection ID) and `<set><name>…</name></set>` (or the older `<set>Name</set>`) | NFO (TMDB collection, written by Jellyfin) |
| 2 | **Title** — `<title>` | NFO |
| 3 | **Title** — parsed from the folder or file name | Name; fallback when there is no NFO title |

A sample of 300 NFOs under `M:\Movies` (2026-10-28) had a collection in 123 of them; Jellyfin writes the TMDB collection ID as `<collectionnumber>` next to `<set>` (e.g. `9485`). Actors, director, genre and studio are **not** used — they would group every Nolan or Marvel film.

### Rules

1. **Same collection** on two or more movies → they belong together. When both NFOs have a `<collectionnumber>`, the IDs are compared (robust against a renamed or translated collection); otherwise the names, case-insensitively, a trailing “ Collection” ignored. **Group key:** `collection:<id>` when any movie of the group has a `<collectionnumber>`, otherwise `collection:<normalised name>`. A movie without an ID joins an ID group only when its collection name matches and that name belongs to exactly one ID; if the name is used with two different IDs it joins neither (ambiguous — it can still be grouped by title).
2. **Similar titles** → the titles are normalised and a **sequel marker** is split off (exact rules in [Title normalisation](#title-normalisation-and-sequel-markers) below). Movies with the same base title belong together. Limits: a number that is the whole title is never stripped (`1917`, `9`), and the base title must be at least 3 characters and not only digits. **Title-only grouping needs the original film** (decided 2026-10-28): one movie's whole title must equal the base title (*Back to the Future*) and at least one other must carry a sequel marker (*Back to the Future Part II*). **A colon subtitle counts as a marker** (*Alien* + *Alien: Covenant*, *Dune* + *Dune: Part Two*). The original may be in any location (Library or Download) — it only has to be one of the group's movies. So *Toy Story 2* + *Toy Story 3* without *Toy Story*, *Apollo 13* + *Apollo 18*, or remakes like *The Thing* (1982) + *The Thing* (2011) (no marker) are not grouped by title — a shared collection still groups them.
3. **Different collections are never grouped** — two movies whose NFOs name different collections are not grouped by title either (`Scream` / `Scream Queens`).
4. **One movie is never a bundle** — a group needs at least two *different* movies. Copies of the same movie (one duplicate group) and the parts of one movie (loose files from one folder with the same title and year, e.g. `CD1`/`CD2`) count as one; a collection with a single movie in the library proposes nothing.
5. **Movies already in a bundle are not proposed for another one.** Moving a movie between bundles is still possible by hand. (*Merge bundles* is about whole bundles and is not affected.)
   **Copies of a bundled movie are not proposed either** — when any member of a movie's duplicate group is already in a bundle, no copy of it is proposed for that bundle (or any other). A second *No Time to Die* in Downloads while one is in `James Bond\` is a *Duplicates* issue, not a bundle one.
6. Movies marked *Not related* never count (see below) — until the record is removed in Settings.
7. **A movie appears in at most one proposal.** Priority: *Add to bundle* before *New bundle*; same collection before similar titles; when several existing bundles match (e.g. `James Bond` in two Library Locations), the one in the movie's own Library Location, otherwise the first by config order. A movie marked *Not related* for the winning proposal can show up in the next-best one.
8. **A copy of a movie the bundle already has is never proposed for it.** A movie in the same duplicate group as a member of the bundle (e.g. a second *No Time to Die* in Downloads while `James Bond\` already holds one) is a Duplicates matter, not a bundle one.

### Title normalisation and sequel markers

Applied to every title before comparing (rule 2), in this order:

1. **Source:** the NFO `<title>`; without one, the name parsed by `reorganize.parse_name` (folder name, else video stem — handles scene names and underscores).
2. **Strip the year:** `(1985)` / `[1985]` anywhere, and the year `parse_name` found in a scene name.
3. **Strip quality tags** (`QUALITY_STRIP`, as `_clean_title` does).
4. **Split off one sequel marker at the end** — the first that matches; the rest is the base title:
   - **Subtitle:** everything after the first `: ` (NFO titles) or ` - ` (folder/file names — Windows names cannot hold `:`, the Sanitize rule turns it into ` - `).
   - **Part word + number:** `Part`, `Pt`, `Pt.`, `Chapter`, `Chap.`, `Vol`, `Vol.`, `Volume`, followed by a number.
   - **Trailing number:** a separate word of 1–4 digits (`Toy Story 2`, `Blade Runner 2049`).
   - **Trailing Roman numeral:** a separate upper-case word `II` … `XX` (`I` is excluded).
   - A *number* is digits, a Roman numeral `I` … `XX`, or an English word `One` … `Twenty`.
   - A marker is only split off when at least one word stays before it: `1917` and `9` keep their whole title.
5. **Compare:** case-insensitive, `&` → `and`, apostrophes removed, other punctuation → space, whitespace collapsed. Leading articles are kept (`The Matrix` ≠ `Matrix`).
6. The base title must be at least 3 characters and not only digits; otherwise the movie is not grouped by title.

| Title | Base title | Marker |
| :--- | :--- | :--- |
| `Back to the Future Part II` | back to the future | yes |
| `Alien: Covenant` (NFO) / `Alien - Covenant (2017)` (folder) | alien | yes |
| `Rocky IV` | rocky | yes |
| `Blade Runner 2049` | blade runner | yes |
| `Kill Bill: Vol. 1` | kill bill | yes |
| `Toy Story` | toy story | no — an original |
| `1917` | 1917 | no — the number is the whole title |
| `Malcolm X` | malcolm | yes — but grouped only if a movie titled *Malcolm* exists (original rule) |

### Proposal types

| Type | When | Example |
| :--- | :--- | :--- |
| **New bundle** | ≥ 2 related movies, none of them in a bundle | *Back to the Future* I–III — one in `M:\Movies`, two in Downloads |
| **Add to bundle** | A movie outside a bundle is related to an existing bundle: its collection matches a member's collection, or its base title equals the bundle folder name or the **whole title of a member** (the original film is in the bundle). A member's sequel alone is not enough: *Toy Story 3* is not proposed for `Pixar\` holding only *Toy Story 2* | *No Time to Die (2021)* in Downloads → `M:\Movies\James Bond\` |
| **Merge bundles** | Two bundles in **different** Library Locations with the same name (case-insensitive) or a shared collection among their members | `M:\Movies\James Bond\` + `N:\Movies\James Bond\` → one bundle |

Each proposal shows **why** it was made — “Same collection: James Bond Collection” or “Similar titles” — and an Add-to-bundle proposal also lists the movies already in the bundle (greyed, for context).

### Suggested bundle name

- From a collection: its name without a trailing “ Collection” (`James Bond Collection` → `James Bond`).
- From similar titles: the shared base title (`Back to the Future`).
- Passed through the one Sanitize rule (`sanitize_component`) and always editable before anything is created.

---

## Not related — saying no

| User choice | Remembered as |
| :--- | :--- |
| **Not related** on a whole new-bundle proposal (“these movies are unrelated”) | every member: *not related to this proposal* |
| **Outside** on one movie of a proposal | that movie: *not related to this proposal* |
| **Not related** on a movie suggested for an existing bundle | that movie: *not related to that bundle* |
| **Not related** on a *Merge bundles* proposal | the two bundle folders: *not to be merged* (checked both ways) |

- One record type: *path → key*, stored in `web/not-related.json` (next to `not-duplicates.json`) as a list:

  ```json
  [
    {"path": "D:\\Downloaded\\Netflix\\Heat (1995)", "key": "bundle:M:\\Movies\\Michael Mann"},
    {"path": "M:\\Movies\\Rocky II (1979)",        "key": "title:rocky"},
    {"path": "M:\\Movies\\Skyfall (2012)",          "key": "collection:645"},
    {"path": "N:\\Movies\\James Bond",              "key": "bundle:M:\\Movies\\James Bond"}
  ]
  ```

  `path` is the movie's item id (its folder, or the loose file) — or, for *Merge bundles*, a bundle folder. `key` is `bundle:<folder path>` for an existing bundle, or the proposal's group key (`collection:<id or name>`, `title:<base title>`) for a new one. Paths are absolute, as in `not-duplicates.json`.
- A new-bundle proposal is shown only while it has at least two movies that are not marked; an Add-to-bundle proposal only while at least one candidate is not marked.
- Records follow moves of the movie and renames of the bundle folder done in the app, like “not a duplicate” marks.
- **Create bundle** moves the proposal's records (`title:…` / `collection:…`) to the new folder's key, so a movie marked *Outside* does not come back as “add to bundle”.
- **Settings → “Not related” tab** — every record (`movie — bundle / proposal`), a filter, All/None, **Remove selected** and **Remove all** (in-dialog confirmation). Removing a record makes the movie count again right away (proposals are recomputed in memory). Records whose movie or bundle folder no longer exists (e.g. renamed outside the app) are shown as *not found*, so they can be removed.

---

## UI

### Special filter

**Possible bundles (N)** in the sidebar next to *Duplicates* and *Incomplete NFO*; N = the number of proposals. It lists the movies of all proposals, grouped per proposal (header: name, type, reason), with a **Review…** button per group that opens the Bundle dialog.

### Bundle dialog

```
New bundle — Similar titles
  Name: [Back to the Future        ]   in: [M:\Movies ▾]
  ☑ Back to the Future (1985)               M:\Movies               [Outside]
  ☑ Back to the Future Part II (1989)       D:\Downloaded\Netflix   [Outside]
  ☑ Back to the Future Part III (1990).mkv  D:\Downloaded\Netflix   [Outside]
  ☑ Dry run
  [Not related]                                   [Create bundle]  [Close]
```

```
Add to bundle — Same collection: James Bond Collection
  Bundle: M:\Movies\James Bond\
  Already in the bundle: GoldenEye (1995), Casino Royale (2006), Skyfall (2012)
  ☑ No Time to Die (2021)                   D:\Downloaded\Netflix   [Not related]
  ☑ Dry run
                                                   [Add to bundle]  [Close]
```

```
Merge bundles — Same name
  Into: [M:\Movies\James Bond\ ▾]   7 movies
  From:  N:\Movies\James Bond\      3 movies
  ☑ Dr. No (1962)   ☑ From Russia with Love (1963)   ☑ Goldfinger (1964)
  ☑ Dry run
  [Not related]                                        [Merge]  [Close]
```

- *Merge bundles*: the target is pre-selected as the bundle with more movies (tie → the one in the first Library Location of the config) and can be switched. The movies of the other bundle move into it exactly as with *Add to bundle*; a movie whose folder already exists in the target is refused (a Duplicates matter). The emptied source folder is removed with `rmdir` or shows up under Leftover folders.

- Unticking a movie only leaves it out of this move; **Outside** / **Not related** remember the choice.
- Every bundle dialog warns: *Jellyfin identifies movies by their path — a moved movie may lose its played status and other user data.*
- Location for a new bundle: always a Library Location (dropdown). Pre-selected = the Library Location holding most of the members (tie → the first in the config); when all members are in Downloads, the first Library Location in the config.
- A dry run shows the result with a **Back to …** button that reopens the dialog (repo convention). A real run asks for confirmation inside the dialog, then runs like Move (one item per request with progress; a rescan follows).

### Move to bundle by hand

A **Bundle…** button in the selection bar for the selected movies: choose an existing bundle (the bundles in the Library Locations), **New bundle** (name + Library Location) or **Out of bundle**. Same dialog and execution as above — for movies the detection did not find.

**Out of bundle** moves each selected movie from its bundle to the root of that bundle's Library Location (`M:\Movies\James Bond\Heat (1995)\` → `M:\Movies\Heat (1995)\`). It never asks Move's “Move them together?” question (R-27), so the bundle folder is not recreated elsewhere. A **loose file** in the bundle gets its own `{title} ({year})` folder at the root, exactly as when it moves into a bundle (with its sidecars and `<stem>.trickplay\`; refused without title/year) — the Library Location root does not collect loose files. Same guards as all bundle moves (destination exists → refused).

### Bundles overview — show and rename

A sidebar row **Bundles (N)** (below the special filters) opens a dialog listing every bundle:

```
Bundles
  James Bond           M:\Movies   7 movies   [Show movies]  [Rename]
  Back to the Future   M:\Movies   3 movies   [Show movies]  [Rename]
  ☑ Dry run                                                     [Close]
```

- **Show movies** closes the dialog and limits the library list to that bundle's movies.
- A bundle whose name equals its only movie (`M:\Movies\Heat (1995)\Heat (1995)\`, typical after unpacking a download) is marked *nested movie folder?* — usually not a real bundle; **Out of bundle** on its movie fixes it (the emptied outer folder is removed or shows up under Leftover folders). Only a hint; nothing is changed automatically.
- **Rename** edits the name inline. The new name passes `valid_component` and the Sanitize rule; an existing folder or file with that name refuses (a case-only change is allowed). Dry run by default, in-dialog confirmation, Jellyfin warning; “not related” and “not a duplicate” records follow the new path; a rescan follows.

### Bundle on movie rows

A movie inside a bundle shows the bundle name as a subtle second line:

```
GoldenEye (1995)
James Bond
```

---

## Execution — moving movies into a bundle

An extension of **Move** (`fileops.move_items`), so every Move guard applies: never overwrite (R-03), sidecars of loose files (R-26), write probes, free space, no two items with the same destination, “not a duplicate” and “not related” marks follow.

- **Target:** an existing bundle folder, or a **new** bundle folder `<Library Location>\<name>` — the only folder created besides a loose file's own folder. The name must pass `valid_component`, the parent must be a configured Library Location, and an existing file or folder with that name refuses *Create bundle* (use *Add to bundle* when it is a bundle).
- **Movie folder** → moved as a whole into the bundle (`James Bond\No Time to Die (2021)\`).
- **Loose file** → gets its own folder in the bundle, named with the Naming format's movie folder (`{title} ({year})`; title and year from the NFO or the parsed name). The video and its exact-stem sidecars (`related_files`, R-09 rule) move into it — including Jellyfin's `<stem>.trickplay\` folder (R-30); NFOs keep their name; images move along. Without a title or year the item is refused (“use Reorganize first”).
- **Destination exists** → that item is refused; no automatic `(1)`.
- A source **bundle folder left empty** is removed — `rmdir` only, which cannot delete content (as in R-27). When Jellyfin data is left in it (`folder.jpg`, an `.nfo`), it stays and shows up under **Leftover folders**, where the user decides ([README](./README.md#leftover-folders-designed-2026-10-28-not-built-yet)). A folder that still holds one movie folder stays a bundle; one left with a single loose file no longer is (that file's folder is then simply its movie folder).
- Series and transcoded items are refused; a movie already in the target bundle is skipped (“already in this bundle”), and so is a **copy** of a movie in the target bundle (same duplicate group, “a copy is already in this bundle — see Duplicates”). This applies to *Bundle…* by hand too, so a loose-file copy with another name never ends up as a second copy in the bundle.
- In-memory state is not patched; like Move, a rescan follows a real run.

---

## Implementation notes

- `py/ui/nfo.py`: read the collection (`<set><name>`, older `<set>text`) into the NFO data as `set`, and `<collectionnumber>` as `set_id`. Existing caches lack them until the next full rescan; until then only title-based proposals appear.
- New module `py/ui/bundles.py`: `find_bundle_proposals(items, not_related)` (pure, no I/O) and the not-related registry (load / save / mark / remove / follow path changes). Called from the scan and wherever duplicates are recomputed.
- Bundle membership: a movie's bundle is the folder holding its own folder (or holding the loose file), when that folder is directly in a Library Location root — never the root itself — **and holds at least one movie folder** (the scanner's `_has_movie_subfolder` test) **or loose files of at least two different movies** (title + year). The scanner stores it as the item's `bundle` field: the **full path** of the bundle folder (or empty) — two Library Locations can both have a `James Bond\`; the library list shows its last component. For the parts of one movie (`Heat (1995)\Heat - CD1.mkv`) the item's own folder is not a bundle, so `bundle` is empty.
- Proposals (all three types), the list of existing bundles and the overview data come from `GET /api/bundles` ([API](solution-design.md#bundle-api-endpoints)), which the browser fetches together with `/api/status` whenever it reloads the library (after a scan, an action or a not-related change). They are kept in `_state` next to the duplicate groups and recomputed with them.
- Move extension in `py/ui/fileops.py` (new bundle folder, loose-file foldering), request model in `models.py`, thin routes in `app.py`.
- Front end: new `web/static/js/bundles.js` (special filter, Bundle dialog, Bundle… button, Bundles overview, Settings tab), added to `UI_SCRIPTS` (ADR 0004).
- Risks: [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) R-28 (planned).
