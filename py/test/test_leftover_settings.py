"""Leftover folder settings: defaults merged into the config, video entries refused, matching helpers.

Uses a temporary config file inside py/test (removed afterwards) — web/config.yaml is never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_leftover_settings.py
Exit code 0 = all checks passed.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from py.ui import app, settings  # noqa: E402
from py.ui.models import ConfigBody, LeftoverConfig  # noqa: E402
from py.ui.settings import (  # noqa: E402
    DEFAULT_LEFTOVER,
    file_matches,
    folder_matches,
    leftover_error,
    leftover_lists,
)

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def run(tmp: Path) -> None:
    # ── Merging ───────────────────────────────────────────────────────────────────
    check("no leftover section -> defaults", leftover_lists({}) == DEFAULT_LEFTOVER)
    partial = leftover_lists({"leftover": {"jellyfin_files": [" .nfo ", ""], "system_folders": "oops"}})
    check("partial section: given list kept (trimmed), invalid and missing lists get their defaults",
          partial["jellyfin_files"] == [".nfo"] and partial["system_folders"] == DEFAULT_LEFTOVER["system_folders"]
          and partial["subtitle_files"] == DEFAULT_LEFTOVER["subtitle_files"])
    settings.CONFIG_PATH.write_text("locations: []\ndownloads: []\n", encoding="utf-8")
    check("load_config without leftover: -> defaults", settings.load_config()["leftover"] == DEFAULT_LEFTOVER)
    settings.CONFIG_PATH.write_text("locations: []\ndownloads: []\nleftover:\n  ignored_files: [Thumbs.db]\n",
                                    encoding="utf-8")
    cfg = settings.load_config()["leftover"]
    check("load_config with a partial section completes it",
          cfg["ignored_files"] == ["Thumbs.db"] and cfg["jellyfin_folders"] == DEFAULT_LEFTOVER["jellyfin_folders"])

    # ── Video entries refused ─────────────────────────────────────────────────────
    check("defaults are valid", leftover_error(DEFAULT_LEFTOVER) is None)
    check(".mkv refused", leftover_error({**DEFAULT_LEFTOVER, "ignored_files": [".MKV"]}) is not None)
    check("a name ending in a video extension refused",
          leftover_error({**DEFAULT_LEFTOVER, "jellyfin_files": ["sample.mp4"]}) is not None)
    check("folder lists are not file lists", leftover_error({**DEFAULT_LEFTOVER, "system_folders": ["x.mkv"]}) is None)

    try:
        app.api_save_config(ConfigBody(locations=[], downloads=[], leftover=LeftoverConfig(subtitle_files=[".srt", ".avi"])))
        check("save with a video entry refused (400)", False)
    except HTTPException as e:
        check("save with a video entry refused (400)", e.status_code == 400)
    check("refused save left the config unchanged", settings.load_config()["leftover"]["ignored_files"] == ["Thumbs.db"])
    res = app.api_save_config(ConfigBody(locations=[], downloads=[],
                                         leftover=LeftoverConfig(ignored_files=[" Thumbs.db ", "", "desktop.ini"])))
    check("save round-trips (trimmed, empty dropped)",
          res["leftover"]["ignored_files"] == ["Thumbs.db", "desktop.ini"]
          and settings.load_config()["leftover"]["ignored_files"] == ["Thumbs.db", "desktop.ini"])
    app.api_save_config(ConfigBody(locations=[], downloads=[]))
    check("older frontend (no leftover) leaves it unchanged",
          settings.load_config()["leftover"]["ignored_files"] == ["Thumbs.db", "desktop.ini"])

    # ── Matching helpers ──────────────────────────────────────────────────────────
    check("whole name, any case", file_matches("THUMBS.DB", ["Thumbs.db"]))
    check("extension, any case", file_matches("poster.JPG", [".jpg"]))
    check("dot entry as a whole name", file_matches(".DS_Store", [".DS_Store"]))
    check("subtitle with a language part", file_matches("Heat.en.srt", [".srt"]))
    check("no partial name match", not file_matches("jpg", [".jpg"]) and not file_matches("Thumbs.db.txt", ["Thumbs.db"]))
    check("folder wildcard", folder_matches("Heat (1995).Trickplay", ["*.trickplay"]))
    check("wildcard needs the rest", not folder_matches("trickplay", ["*.trickplay"]))
    check("folder name, any case, special characters literal",
          folder_matches("$Recycle.Bin", ["$RECYCLE.BIN"]) and folder_matches("@eaDir", ["@eadir"])
          and not folder_matches("eaDir", ["@eaDir"]))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_tmp_leftover_settings_", dir=Path(__file__).resolve().parent))
    saved = settings.CONFIG_PATH, settings.CACHE_DIR
    settings.CONFIG_PATH, settings.CACHE_DIR = tmp / "config.yaml", tmp / "cache"
    try:
        run(tmp)
    finally:
        settings.CONFIG_PATH, settings.CACHE_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'OK' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
