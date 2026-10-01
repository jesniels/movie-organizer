"""Paths, constants, logging, and config file handling."""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

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

DEFAULT_TRANSCODE: Dict[str, Any] = {
    "ffmpeg_path":  "",
    "ffprobe_path": "",
    "output_root":  "",
    "encoder":      "hevc_amf",
    "qp":           22,
    "audio_languages":    ["eng"],
    "audio_reject_words": ["description", "descriptive", "described", "commentary", "sdh", "visual"],
    "audio_prefer":       "first",
}

DEFAULT_NAMING: Dict[str, Any] = {
    "movie_folder":       "{title} ({year})",
    "movie_file":         "{title} ({year})",
    "series_folder":      "{title} ({year})",
    "file_equals_folder": True,   # movie_file follows movie_folder; default for the dialog's per-row toggle
    "rename_files":       True,
    "resolve_nfos":       True,   # suggest fixing NFO names (lone NFO with another name, several NFOs)
    "delete_images":      True,
    "sanitize_names":     True,   # suggest sanitizing illegal names of files/folders in the analysed items
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "locations": [],
    "downloads": [],
    "server_mode": False,
    "local_shares": {},
    "transcode": dict(DEFAULT_TRANSCODE),
    "naming": dict(DEFAULT_NAMING),
}


def _clean_title(name: str) -> str:
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name)
    name = QUALITY_STRIP.sub("", name)
    return name.strip()


def transcoded_root(config: Dict[str, Any]) -> str:
    """Configured transcode output folder, stripped of the quotes "Copy as path" adds; '' if unset."""
    return ((config.get("transcode") or {}).get("output_root") or "").strip().strip('"').strip()


def transcoded_root_conflict(config: Dict[str, Any]) -> Optional[str]:
    """Error text when the output folder is, contains, or is inside a library/download folder."""
    root = transcoded_root(config)
    if not root:
        return None
    norm = lambda p: os.path.normcase(os.path.normpath(p))
    r = norm(root)
    for p in config.get("locations", []) + config.get("downloads", []):
        if not p:
            continue
        q = norm(p)
        if r == q or r.startswith(q + os.sep) or q.startswith(r + os.sep):
            return (f"The transcode output folder ({root}) must not be, contain, or be inside "
                    f"a library/download folder ({p})")
    return None


def safe_transcoded_root(config: Dict[str, Any]) -> str:
    """The output folder, or '' when unset or overlapping a library/download folder."""
    return "" if transcoded_root_conflict(config) else transcoded_root(config)


def map_to_local(config: Dict[str, Any], path: str) -> Optional[str]:
    """Translate a server-side path to its configured local share, or None when no share covers it.

    Matches the longest configured root (locations/downloads/transcode output) that has a
    local share in config['local_shares'], and rewrites the path separators to match the share.
    """
    shares = config.get("local_shares") or {}
    p_norm = os.path.normpath(path)
    p_cmp  = os.path.normcase(p_norm)
    best: Optional[tuple] = None   # (root_len, share)
    for root, share in shares.items():
        share = (share or "").strip()
        if not root or not share:
            continue
        r_cmp = os.path.normcase(os.path.normpath(root))
        if p_cmp == r_cmp or p_cmp.startswith(r_cmp + os.sep):
            if best is None or len(r_cmp) > best[0]:
                best = (len(r_cmp), share)
    if best is None:
        return None
    rel = p_norm[best[0]:].lstrip("\\/")
    sep = "/" if ("/" in best[1] and "\\" not in best[1]) else "\\"
    rel = rel.replace("\\", sep).replace("/", sep)
    share = best[1].rstrip("\\/")
    return share + sep + rel if rel else share


# ── Naming ─────────────────────────────────────────────────────────────────────
_ILLEGAL_NAME_CHARS = set('\\/:*?"<>|')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_NAMING_FIELDS = {
    "movie_folder":  "Movie folder format",
    "movie_file":    "Movie file format",
    "series_folder": "Series folder format",
}


def valid_component(name: str) -> Optional[str]:
    """Error text when name is not a single, Windows-legal path component; None when it is."""
    if not name or not name.strip():
        return "Name must not be empty"
    bad = sorted({c for c in name if c in _ILLEGAL_NAME_CHARS or ord(c) < 32})
    if bad:
        shown = " ".join(c if ord(c) >= 32 else repr(c) for c in bad)
        return f"Invalid name {name!r}: characters not allowed in file names: {shown}"
    if name.strip() in (".", ".."):
        return f"Invalid name {name!r}"
    if name[-1] in ". ":
        return f"Invalid name {name!r}: must not end with a dot or space"
    if name.split(".")[0].strip().upper() in _RESERVED_NAMES:
        return f"Invalid name {name!r}: reserved Windows device name"
    return None


def sanitize_component(value: str) -> str:
    """The one sanitize rule (titles and existing names): ':' → ' - ', other illegal chars dropped, trailing dots/spaces stripped."""
    value = re.sub(r"\s*:\s*", " - ", value)
    value = "".join(c for c in value if c not in _ILLEGAL_NAME_CHARS and ord(c) >= 32)
    return value.rstrip(". ")


def expand_naming(template: str, title: str, year: Any) -> Optional[str]:
    """Expand a naming template; None when the title is empty or {year} is required but unknown."""
    title = sanitize_component((title or "").strip())
    year = str(year).strip() if year else ""
    if not title or ("{year}" in template and not year):
        return None
    values = {"title": title, "year": year}
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def naming_error(naming: Dict[str, Any]) -> Optional[str]:
    """Error text for the first invalid naming template; None when all are valid."""
    for key, label in _NAMING_FIELDS.items():
        tpl = naming.get(key) or ""
        if "{title}" not in tpl:
            return f"{label} must contain {{title}}"
        unknown = [p for p in _PLACEHOLDER_RE.findall(tpl) if p not in ("title", "year")]
        if unknown:
            return f"{label}: unknown placeholder {{{unknown[0]}}} — only {{title}} and {{year}} are supported"
        sample = expand_naming(tpl, "Alien", 1979) or ""
        if "{" in sample or "}" in sample:
            return f"{label}: unmatched {{ or }}"
        err = valid_component(sample)
        if err:
            return f"{label}: {err}"
    return None


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
    cfg = {**DEFAULT_CONFIG, **raw}
    tc = raw.get("transcode")
    cfg["transcode"] = {**DEFAULT_TRANSCODE, **(tc if isinstance(tc, dict) else {})}
    nm = raw.get("naming")
    cfg["naming"] = {**DEFAULT_NAMING, **(nm if isinstance(nm, dict) else {})}
    if cfg["naming"]["file_equals_folder"]:
        cfg["naming"]["movie_file"] = cfg["naming"]["movie_folder"]
    cfg["server_mode"]  = bool(cfg.get("server_mode"))
    ls = cfg.get("local_shares")
    cfg["local_shares"] = ls if isinstance(ls, dict) else {}
    return cfg


def _write_config(cfg: Dict[str, Any]) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(cfg, fh, default_flow_style=False, allow_unicode=True)
    except OSError as exc:
        log.error("Cannot write config file %s: %s", CONFIG_PATH, exc)
        raise
