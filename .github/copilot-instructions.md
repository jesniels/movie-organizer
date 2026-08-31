# Copilot Instructions — Jellyfin Movie Organizer

## What this repository is

A pure-Python toolset for organizing movie/series files for Jellyfin media libraries. It groups loose movie files (`.mkv`, `.nfo`, images) into standard `Movie Title (Year)` folders, plus a web UI for managing libraries and downloads.

## Language policy

- **Python only.** All application code, tools, and scripts must be written in Python. Do not introduce other backend languages, Node.js tooling, or build systems.
- The only non-Python code is the browser front-end in `web/static/` (vanilla JS + Bootstrap) and the Jinja2 templates in `web/templates/`. Keep it that way — no frameworks, bundlers, or npm.


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
| `web/config.yaml` | UI configuration: library `locations` and `downloads` folders. |
| `web/cache/` | Cached JSON scans of libraries/downloads. |
| `web/static/`, `web/templates/` | Front-end assets (JS/CSS) and Jinja2 templates. |
| `docs/` | Solution design, organization docs, and ADRs (`docs/adr/`). |
| `test_filename_parsing.py`, `test_normalize.py` | Tests at repo root. |


## Conventions

- CLI tools in `py/` should stay dependency-light; `movie-organizer.py` uses only the standard library.
- The web UI runs from the repo root and resolves paths relative to the project (`web/` for config, cache, templates, static).
- Destructive file operations must support a dry-run mode and be safe by default (see ADRs in `docs/adr/`).
- Jellyfin conventions apply: `.nfo` renamed to `movie.nfo`, `Title (Year)` folder naming, images deleted from source (Jellyfin regenerates them).
- Consult `docs/organization/CONTEXT.md` and the ADRs before changing organize/scan behavior.

