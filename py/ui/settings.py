"""Paths, constants, logging, and config file handling."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("movie-organizer")

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH   = BASE_DIR / "web" / "config.yaml"
CACHE_DIR     = BASE_DIR / "web" / "cache"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR    = BASE_DIR / "web" / "static"
NOT_DUP_PATH  = BASE_DIR / "web" / "not-duplicates.json"

# ── Constants ──────────────────────────────────────────────────────────────────
VIDEO_EXTS     = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
EPISODE_RE     = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
SEASON_DIR_RE  = re.compile(r"^(?:season[\s_.-]*\d+|s\d{1,2})$", re.IGNORECASE)
YEAR_RE        = re.compile(r"\((\d{4})\)")
QUALITY_STRIP  = re.compile(
    r"\s*(1080p|720p|2160p|4[Kk]|HDR|BluRay|WEB[-.]DL|WEBRip|HEVC|x264|x265|REMUX).*$",
    re.IGNORECASE,
)

DEFAULT_CONFIG: Dict[str, Any] = {"locations": [], "downloads": []}


def _clean_title(name: str) -> str:
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name)
    name = QUALITY_STRIP.sub("", name)
    return name.strip()


# ── Config ─────────────────────────────────────────────────────────────────────
def load_config() -> Dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _write_config(DEFAULT_CONFIG)
        log.info("Created default config at %s", CONFIG_PATH)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        # Show exactly where the problem is — no stacktrace noise
        mark = getattr(exc, 'problem_mark', None)
        location = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem  = getattr(exc, 'problem', str(exc))
        log.error("Invalid YAML in %s%s: %s", CONFIG_PATH, location, problem)
        log.error("Fix the config file and restart the server.")
        sys.exit(1)
    except OSError as exc:
        log.error("Cannot read config file %s: %s", CONFIG_PATH, exc)
        sys.exit(1)
    if not isinstance(raw, dict):
        log.error("Config file %s must be a YAML mapping, got %s.", CONFIG_PATH, type(raw).__name__)
        sys.exit(1)
    return {**DEFAULT_CONFIG, **raw}


def _write_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)
    except OSError as exc:
        log.error("Cannot write config file %s: %s", CONFIG_PATH, exc)
        raise
