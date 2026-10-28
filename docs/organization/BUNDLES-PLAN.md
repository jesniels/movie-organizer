# Implementation Plan — Bundles

Source design: [bundles.md](bundles.md) · terms: [CONTEXT.md](CONTEXT.md) · API and models: [solution-design.md](solution-design.md#bundle-detection-and-bundle-moves-replaces-the-smart-scan-engine-2026-10-28) · risks: [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) R-28, R-30

> **Ground rule (design):** detection never changes files. Every move, merge or rename is chosen by the user, runs as a dry run by default and asks for confirmation inside the dialog.
>
> **Operational note:** the UI server does not auto-reload — restart it after every backend phase, or new endpoints 404 and new config keys are silently dropped. Phase 2 changes the cache content: after it, **Rescan** once so `set`, `set_id` and `bundle` are in every cache file (until then only title-based proposals appear).
>
> **Before any phase that moves, renames or deletes files** read [APPLICATION-RISKS.md](../APPLICATION-RISKS.md) and keep its guards. Tests use real files only in `py/test/_tmp*` and run with `venv\Scripts\python.exe`.

## Status

| Phase | Scope | Status |
| :--- | :--- | :--- |
| 1 | R-30: `<stem>.trickplay` folder follows a loose file (Move, Delete, Reorganize) | Not started |
| 2 | Scanner + NFO: `set` / `set_id`, the item `bundle` field, row label | Not started |
| 3 | Detection (`py/ui/bundles.py`) + "not related" registry + read API | Not started |
| 4 | Bundle moves: into, out of, merge, rename (R-28) | Not started |
| 5 | Bundle UI: special filter, Bundle dialog, Bundle…, overview, Settings tab | Not started |

Each phase is independently shippable; nothing is visible to the user until Phase 5, except the bundle name on movie rows (Phase 2).

**Not in this plan:** Organize is not built yet — its "New bundle…" picker field ([organize.md](organize.md)) comes with Organize. The "N leftover folders — Review…" link in the bundle dialogs belongs to the [Leftover folders plan](LEFTOVERS-PLAN.md) (Phase 5 there); whichever plan lands second wires it.

---

## Phase 1 — R-30: trickplay folder follows its loose file

Fixes an existing gap in built code first, so the bundle moves in Phase 4 inherit it.

**Backend** (`py/ui/fileops.py`, `py/ui/reorganize.py`):
- A helper next to `fileops._own_sidecars` returns the loose video's `<stem>.trickplay` folder (case-insensitive, exact stem) when it exists and no other video in the folder has the same stem.
- **Move** (`move_items`, R-26): the folder moves after the video, like the file sidecars — destination must not exist (checked again right before), counted in the free-space check, listed in the dry run (`also`). A failed move leaves it in place and is reported as a warning.
- **Delete** (`delete_items`, R-25): removed only when it holds nothing but image files — files one by one, then folders with `rmdir` deepest first; anything else leaves it in place and is reported. No `rmtree`.
- **Reorganize** foldering (`apply_changes`, R-19): the folder moves into the new movie folder with the video; listed in the analysis and the dry run.

**Tests:** new `py/test/test_trickplay_sidecars.py` — moved with the video; existing destination → refused, video still moves; shared stem (two videos `Heat.mkv` / `Heat.mp4`) → stays; Delete with only images → removed; with an unknown file → stays and reported; Reorganize foldering → inside the new folder; dry run touches nothing. `test_move_sidecars.py` and `test_reorganize_execution.py` still pass.

**Done when:** tests pass; R-30 in APPLICATION-RISKS set to **Guarded** with the date and test name.

---

## Phase 2 — Scanner and NFO: collection and bundle

**Backend:**
- `py/ui/nfo.py` `parse_nfo`: `set` = `<set><name>` text, or the older `<set>text</set>`; `set_id` = `<collectionnumber>` text (digits only, else empty). Both empty when absent.
- `py/ui/scanner.py`: each **movie in a Library Location** gets `bundle` — the full path of the folder directly above its own folder (or above the loose file) when that folder is a Bundle: directly in the location root (never the root itself) and holding at least one movie folder (`_has_movie_subfolder`) or loose files of at least two different movies (title + year). Otherwise `""`. Download, transcoded and series items: always `""`. See the examples in [solution-design.md](solution-design.md#scanner-bundle-tracking).
- Cache (`_save_location_cache` / loader): `bundle` stored relative to the location root like `path`, converted back to absolute on load. Old caches without the field load as `""`.

**Frontend** (`web/static/js/library.js`): a movie row with a `bundle` shows its last path component as a subtle second line.

**Tests:** extend `py/test/test_scanner_bundles.py` — root item → `""`; movie folder in a bundle → bundle path; one movie folder is enough; two different loose movies in a folder → that folder; `CD1`/`CD2` or multi-version files in their folder → `""`; `M:\Movies\A\B\Movie (2000)\` → `""`; Download Location → `""`; cache round trip keeps the absolute path. NFO: `<set><name>`, `<set>text`, `<collectionnumber>`, none.

**Verify:** restart, **Rescan**, a movie in `M:\Movies\James Bond\` shows *James Bond* under its title. Run `py/test/test_static_js.py`.

---

## Phase 3 — Detection and "not related"

**Backend** — new module `py/ui/bundles.py` (documented, typed functions):
- **Title normalisation** exactly as [bundles.md → Title normalisation](bundles.md#title-normalisation-and-sequel-markers): source (NFO title, else `reorganize.parse_name`), strip year and `QUALITY_STRIP` tags, split one sequel marker (subtitle, part word + number, trailing number, Roman numeral, number words), compare form, minimum base length. Returns `(base, has_marker, is_whole_title)`.
- **`find_bundle_proposals(items, duplicate_groups, not_related) -> {"proposals": [...], "bundles": [...]}`** — pure, no I/O. Implements rules 1–8 and the three proposal types (*new*, *add*, *merge*), the collection group key (`collection:<id>` / `collection:<name>`, ambiguous names join no ID group), the original-film rule, the priority order (rule 7), suggested name (`sanitize_component`) and pre-selected Library Location. The existing-bundle list gives path, Library Location, movie count and the *nested movie folder?* hint.
- **Duplicate groups come from `duplicates._find_duplicates`** (the same groups the Duplicates filter shows) — not recomputed. They decide "copies count as one" (rule 4) and "a copy of a bundled movie is never proposed" (rules 5 and 8).
- **Not-related registry** in the same module: load / save `web/not-related.json` (format in [bundles.md](bundles.md#not-related--saying-no)), mark (whole proposal, one movie, merge pair), remove, follow path changes (same pattern as `duplicates._update_not_duplicates_paths`), move proposal-key records to `bundle:<path>` on *Create bundle*, flag records whose path no longer exists as *not found*.
- Called at the end of every scan and wherever duplicates are recomputed (`scanner.recompute_status`, `duplicates.recompute_duplicates`); the result is kept in `_state["bundles"]` (`py/ui/state.py`). Nothing persisted except `not-related.json`.

**API** (`py/ui/app.py`, models in `py/ui/models.py`): `GET /api/bundles`, `GET /api/bundles/not-related`, `POST /api/bundles/not-related`, `POST /api/bundles/not-related/remove` (`NotRelatedBody`). Thin routes.

**Tests:** new `py/test/test_bundle_detection.py` — every row of the normalisation table in bundles.md; every *Bundle proposer* case in [solution-design.md → Testing](solution-design.md#modules-to-test); collection ID vs name, ambiguous name; merge proposal for same-name bundles in two Library Locations; priority (add before new, collection before title, own Library Location first); not-related records respected and converted on create. New `py/test/test_not_related.py` — load/save, follow a move and a bundle rename, *not found*.

**Verify:** restart; `GET /api/bundles` in the browser returns proposals for known franchises (e.g. a Bond film in Downloads → *add* to `James Bond\`).

---

## Phase 4 — Bundle moves (R-28)

**Backend** (`py/ui/fileops.py`, `py/ui/models.py`, `py/ui/app.py`):
- Extend `move_items` (or a sibling built on its checks) for a bundle target, as [bundles.md → Execution](bundles.md#execution--moving-movies-into-a-bundle): new bundle folder only directly in a configured Library Location (`valid_component`, existing name refuses); movie folder moves whole; loose file gets its `{title} ({year})` folder (Naming format; refused without title/year) with its exact-stem sidecars and `<stem>.trickplay` (Phase 1); destination exists → refused, no `(1)`; series and transcoded refused; already in the bundle, or a copy of a member → skipped with the reason; emptied source bundle removed by `rmdir` only.
- **Out of bundle**: to the bundle's Library Location root, loose file foldered; never the R-27 keep-together question.
- **Merge**: every movie of `source` into `target`, as *Add to bundle*.
- **Rename**: Rename rules for the bundle folder (single component, existing target refused, case-only change allowed).
- After every real run: not-related and not-a-duplicate records follow the new paths; proposal-key records move to the new bundle on *Create bundle*; a rescan follows (state is not patched).
- Endpoints `POST /api/bundles/move`, `/out`, `/merge`, `/rename` with `BundleMoveBody`, `BundleOutBody`, `BundleMergeBody`, `BundleRenameBody` (`dry_run = True` default). One item per request for the progress, like Move. 409 while a scan or transcode runs.

**Tests:** new `py/test/test_bundle_moves.py` — every *Bundle move* case in [solution-design.md → Testing](solution-design.md#modules-to-test), plus merge (folder existing in the target → refused) and the records following a rename.

**Done when:** tests pass; R-28 in APPLICATION-RISKS moved from *Planned* to **Guarded** with the test name.

---

## Phase 5 — Bundle UI

**Frontend** — new `web/static/js/bundles.js`, added to `UI_SCRIPTS` in `py/ui/app.py` (ADR 0004; event wiring in its own `DOMContentLoaded`, unique top-level names); markup in `web/templates/index.html`:
- Fetch `GET /api/bundles` together with `/api/status` whenever the library reloads.
- **Possible bundles (N)** special filter: movies grouped per proposal (name, type, reason) with **Review…**.
- **Bundle dialog** for *new*, *add* and *merge* (layouts in bundles.md): editable name and Library Location, per-movie ticks, **Outside** / **Not related**, Jellyfin warning, dry run with **Back to …**, in-dialog confirmation, progress per item, rescan after a real run. No browser popups.
- **Bundle…** in the selection bar: existing bundle, **New bundle**, **Out of bundle**.
- **Bundles (N)** sidebar row → overview: movie count, *nested movie folder?* hint, **Show movies**, inline **Rename** (dry run, confirmation).
- **Settings → "Not related"** tab: records, filter, All/None, Remove selected / Remove all (in-dialog confirmation), *not found* marked.

**Tests:** `py/test/test_static_js.py` passes.

**Verify (user, in the browser):** a proposal from detection through dry run to a real *Add to bundle*; *Outside* and *Not related* survive a rescan and can be removed in Settings; *Out of bundle*, merge and rename each with a dry run first.

**Done when:** user has tested the flows; this plan's status table and the "not built yet" notes in [bundles.md](bundles.md) and the [README](README.md) are updated.
