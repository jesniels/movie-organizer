# Organize Action

The Organize action moves selected movies or series into a Library Location, creating the correct Jellyfin-compatible folder structure and handling related files.

## Triggering

Select one or more items in the library list → click **Organize** in the selection action bar.

Available for both Movies and Series. If the selection mixes both types, a confirmation prompt warns the user before the destination picker opens (configurable in Settings under `warn_mixed_types`, default: on).

---

## Destination Picker

The picker is two-level:

**Level 1 — Library Location**
Lists all `locations:` entries from `config.yaml`. Includes an "Add new library location…" option that prompts for a path, validates it, and persists it to `config.yaml` immediately.

**Level 2 — Subdirectory**
Lists existing immediate subdirectories of the chosen Library Location, plus a "Create new folder…" text field. The number of subdirectory levels shown is controlled by the `picker_depth` setting (default: 2). Users who need deeper nesting can type a full relative path in the text field.

The chosen destination is displayed once at the top of the preview dialog:
> _Moving 5 movies to M:\Movies_

---

## Preview Dialog

Each item appears as one line:

```
☑  Inception (2010).mkv          →  Inception (2010)
☑  The Dark Knight (2008).mkv    →  The Dark Knight (2008)
⚠☑ GoldenEye (1995)              →  GoldenEye (1995) (1)   [Folder already exists]
```

**Per-row checkbox** — include or exclude this item from the operation. Default: checked.

**Conflict rows** — shown in red when the planned destination folder already exists.
- An alternative name is auto-suggested using Windows-style numbering (`GoldenEye (1995) (1)`, `(2)`, etc.)
- The checkbox on conflict rows is **unchecked by default** — the user must actively tick it to approve the suggested name.
- The user may also deselect a conflict row entirely to skip that item.

**Option checkboxes** (defaults from `config.yaml`):

| Option | Applies to | Default |
|---|---|---|
| Delete images (jpg / jpeg / png) | Movies and Series | `delete_images_default` |

**NFO files keep their name** (decided 2026-10-01, same rule as Reorganize): an NFO named `movie.nfo` or after the video (`<video>.nfo`) already works for Jellyfin and is moved as-is — never renamed. There is no “Rename NFO to `movie.nfo`” option; Organize never creates or renames to `movie.nfo`.

---

## What Happens per Item Type

### Loose Movie File
A video file sitting directly in a Download Location without its own subfolder (e.g. `Inception (2010).mkv`).

1. Derive subfolder name from the video file's stem, stripping quality tags (e.g. `Inception (2010) 1080p BluRay.mkv` → `Inception (2010)`). If an NFO is present and contains a title + year, that is used as the authoritative name.
2. Collect all sibling files sharing the same stem (video, NFO, subtitles, etc.).
3. If "Delete images" is checked: remove `.jpg`/`.jpeg`/`.png` files from the collected set.
4. NFOs in the set keep their name (`<video>.nfo` stays `<video>.nfo`).
5. Create the subfolder at the chosen destination and move all remaining files into it.

### Already-Organised Movie
A folder containing a single film's files (e.g. `The Dark Knight (2008)\`).

1. If "Delete images" is checked: delete image files inside the folder.
2. Move the folder to the chosen destination. NFOs inside (`movie.nfo` or `<video>.nfo`) travel with it unchanged.

### Series
A top-level folder with season subfolders (`S01\`, `S02\`, …).

1. If "Delete images" is checked: delete image files found inside the folder tree.
2. Move the entire top-level series folder to the chosen destination. Season folders, episode files, and `tvshow.nfo` all travel with it.
3. NFOs are left as-is — `tvshow.nfo` and per-episode `<video>.nfo` are the correct Jellyfin names.

---

## Execution — Progress Modal

Clicking **Do it** closes the preview dialog and opens a locked progress modal. The main UI is non-interactive while this modal is open.

```
Organizing 5 items to M:\Movies…

[████████████████░░░░░░░░░░░░]  3 / 5

Currently moving: The Dark Knight (2008)\

                              [Cancel]
```

- Progress is shown per item (item count) with the current item name displayed.
- **Cancel** stops at the next item boundary. Already-moved items stay at their new location — there is no rollback (see [ADR 0003](../adr/0003-cancel-no-rollback-delete-partial-files.md)).
- If a file transfer is interrupted mid-copy, the partial destination file is deleted before stopping.

---

## Post-Operation Prompt

On completion or cancellation:

```
✓ 3 of 5 items moved.

☑ Clean up 2 empty source folders:          ← shown only when empty folders exist
   • D:\Downloaded\Netflix\OldFolder1\
   • D:\Downloaded\Netflix\OldFolder2\

[Rescan now]    [Done]
```

- **Clean up empty folders** — only shown when empty folders are detected in the source location after the operation. Lists each folder so the user knows what will be deleted.
- **Rescan now** — triggers a Basic Scan immediately.
- If a Smart Scan is currently running, the Rescan button is replaced with:
  > ⚠ A rescan is needed but Smart Scan is running.  [Stop Smart Scan]

  Clicking "Stop Smart Scan" cancels it and re-enables the Rescan button.

---

## Creating a Bundle via Organize

Bundles are created naturally through the Organize action. To group three Bond films into a bundle:

1. Select the Bond movies in the library list.
2. Click Organize.
3. In the destination picker, choose `M:\Movies\Action` then type `James Bond` as a new subfolder — or navigate into an existing `James Bond\` folder.
4. Execute. Each film gets its own subfolder inside `M:\Movies\Action\James Bond\`.

No dedicated "Create Bundle" action is needed.
