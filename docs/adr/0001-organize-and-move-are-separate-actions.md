# Organize and Move are separate actions

The Organize action and the Move action serve different intents and are kept as two distinct operations rather than one unified dialog.

Move is a raw relocation: it moves an item as-is to any configured path with no folder creation, NFO handling, or image management — useful for staging, sending something back to a Download Location, or moving to a transcoding folder.

Organize is the intelligent workflow: it creates per-movie subfolders for Loose Files, renames NFOs to `movie.nfo`, optionally deletes images, shows a detailed per-item preview with conflict detection, and executes with a progress modal.

## Considered Options

- **Single unified dialog with a mode toggle** — rejected because the two intents are different enough that shared controls would either expose unnecessary options for simple moves or hide important controls for the organize workflow.
- **Replace Move with Organize entirely** — rejected because Move covers quick raw relocations where the Organize overhead (destination picker, preview, per-item checkboxes) is unwanted friction.
