# Copilot Instructions — Jellyfin Movie Organizer

## What this repository is

A pure-Python toolset for organizing movie/series files for Jellyfin media libraries. It groups loose movie files (`.mkv`, `.nfo`, images) into standard `Movie Title (Year)` folders, plus a web UI for managing libraries and downloads.

## Language policy

- **Python only.** All application code, tools, and scripts must be written in Python. Do not introduce other backend languages, Node.js tooling, or build systems.
- The only non-Python code is the browser front-end in `web/static/` (vanilla JS + Bootstrap) and the Jinja2 templates in `web/templates/`. Keep it that way — no frameworks, bundlers, or npm.

## Guardrails

- Always run CLI tools from the project root.
- Do not start the UI application for testing, this is done by the user
- **No non-Python tooling — not even for a quick check.** Never run Node.js, npm/npx or any other non-Python tool (e.g. `node --check` on a script in `web/static/js/`). Checks and tests are written in Python and run with the venv's interpreter.
- **Never write outside the workspace.** No files in system temp folders or scratch directories outside the repo. Test programs and their temporary files live in `py/test/` (temporary folders named `py/test/_tmp*`, removed by the test and ignored by git).
- **Ask before deciding for the user.** When a request leaves a real choice open, explain the options and ask before changing code; when told to only explain or document something, do not change code.
- **No browser popups** (`alert`/`confirm`/`prompt`) in new UI code — confirmations happen inside the dialog.
- **Dialogs return to where the user came from.** A dialog opened from another dialog returns to that dialog when cancelled (Cancel, ×, Esc): open it with `leaveModalFor(parentId, childId)`, close it after its action ran with `closeModalDone(id)`. A dry-run result has a **Back to …** button that reopens the dialog with its input kept: `swapModal(id, () => showResultModal({ …, closeText: 'Back to …', onClose: () => getModal(id).show() }))`. Helpers are in `web/static/js/core.js`.
- Do not use node or npm. This is a python project!
- Do not introduce other frontend frameworks or build tools; stick to vanilla JS, Bootstrap, and Jinja2 templates.

## Virtual environment

- The venv lives in the `venv/` folder at the repo root.
- Activate on Windows/PowerShell via scripts (`.\venv\Scripts\Activate.ps1` or `.\venv\Scripts\Activate.bat`).
- Install dependencies with `pip install -r requirements.txt` (pywin32, fastapi, uvicorn, PyYAML, pydantic, jinja2, python-multipart).
- Always use the venv's Python interpreter when running scripts or tests.


## Project layout

| Path | Purpose |
| :--- | :--- |
| `py/movie-organizer.py` | CLI tool: organizes loose movie files into `Title (Year)` folders using `.nfo` metadata. Stdlib only. Supports `--dryrun` and `--verbose`. |
| `py/movie-organizer-ui.py` | FastAPI web UI backend. Run from repo root: `python py/movie-organizer-ui.py`. Serves `web/templates/` and `web/static/`. |
| `py/movie-convert-checker.py` | Tool to check movies for conversion needs. |
| `py/fix-renamed-movies.py` | Tool to fix previously renamed movies. |
| `py/delete-empty-folders.py` | Tool to clean up empty folders. |
| `py/test` | Folder for all test and unittest python programs. |
| `web/config.yaml` | UI configuration: library `locations` and `downloads` folders. |
| `web/cache/` | Cached JSON scans of libraries/downloads. |
| `web/static/`, `web/templates/` | Front-end assets (CSS, Bootstrap) and Jinja2 templates. |
| `web/static/js/` | Front-end JavaScript, one classic script per feature, loaded in the order of `UI_SCRIPTS` in `py/ui/app.py` (see ADR 0004). |
| `docs/` | Solution design, organization docs, and ADRs (`docs/adr/`). |
| `docs/APPLICATION-RISKS.md` | Data-loss risk register: every file-changing operation, its safeguard, and open risks. |
| `docs/transcoding/` | Transcoding feature spec and implementation plan. |
| `test_filename_parsing.py`, `test_normalize.py` | Tests at repo root. |


## Conventions

- CLI tools in `py/` should stay dependency-light; `movie-organizer.py` uses only the standard library.
- The web UI runs from the repo root and resolves paths relative to the project (`web/` for config, cache, templates, static).
- Destructive file operations must support a dry-run mode and be safe by default (see ADRs in `docs/adr/`).
- Before adding or changing anything that moves, renames, overwrites or deletes files, read `docs/APPLICATION-RISKS.md`, keep its safeguards intact, and add new risks/guards there. Loose-file items (id = file path) must be acted on as files, never via `item["path"]`; never call `shutil.move` without checking the destination does not exist.
- Jellyfin conventions apply: `Title (Year)` folder naming, images deleted from source (Jellyfin regenerates them). NFOs named `movie.nfo` or after the video (`<video>.nfo`) work for Jellyfin and are never renamed by the web UI (a `<video>.nfo` only follows its video when the video is renamed); the web UI never creates or renames to `movie.nfo`. Only the CLI `movie-organizer.py` still renames to `movie.nfo`.
- The web UI never changes files on its own: every rename/move/delete is suggested, selected by the user and executed explicitly (dry run by default).
- Consult `docs/organization/CONTEXT.md` and the ADRs before changing organize/scan behavior.
- Front-end JS (ADR 0004): put code in the feature's file in `web/static/js/`, with its event wiring in that file's `DOMContentLoaded` handler. Only state and helpers shared by several features go in `core.js`. A new file must be added to `UI_SCRIPTS` in `py/ui/app.py`. Top-level names must be unique across all files. Run `py/test/test_static_js.py` after changing the scripts.
- make sure new functions are documented and follow the established conventions.
- if possible and it makes sense, make sure function parameters and return values are typed.

