# The front-end is split into classic scripts per feature, not ES modules

The web UI's JavaScript lives in `web/static/js/`, one file per feature (`core.js`, `library.js`, `detail.js`, `duplicates.js`, `scan.js`, `settings.js`, `file-actions.js`, `reorganize.js`, `transcode.js`, `main.js`). It replaced a single `app.js` of about 3,600 lines that mixed every feature and was hard to navigate and review.

The files are plain (non-module) `<script>` tags sharing one global scope. ES modules were considered and rejected: `index.html` and the HTML built by the scripts call functions from inline `onclick`/`onchange` handlers, which only see globals. Modules would need every such function exported to `window` or every handler rewritten. That is a large behaviour-neutral change with real regression risk and no build step to catch mistakes. Classic scripts keep the code unchanged and need no bundler, as the project's no-npm, no-build-tools rule requires.

Rules that follow from the shared global scope:

- **Load order** is the `UI_SCRIPTS` list in `py/ui/app.py`, rendered by `index.html` with an mtime `?v=` per file for cache-busting. `core.js` loads first and holds state shared by several features, `api()`/`_apiErr()`, modal/result/confirm/toast helpers and `esc()`. `main.js` loads last and makes the startup calls.
- **State used by one feature lives in that feature's file**; only cross-feature state lives in `core.js`.
- **Each file wires its own events** in its own `DOMContentLoaded` handler. These run in load order, so all wiring is in place before `main.js` starts loading data.
- **Top-level names must be unique across all files**: a second `let`/`const` with the same name is a SyntaxError that stops the whole later file. Calls between files are fine, because they happen at runtime after every file has loaded.
- `py/test/test_static_js.py` checks this without a JS engine: `UI_SCRIPTS` matches the files on disk, there are no duplicate top-level names, and every function called from an inline handler is declared.
