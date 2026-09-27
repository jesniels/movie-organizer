"""NFO parsing, quality classification, merging, and copy operations."""
from __future__ import annotations

import copy
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .settings import log
from .state import _lock, _state, resolve_item


def parse_nfo(nfo_path: Path) -> Dict[str, Any]:
    try:
        root = ET.parse(nfo_path).getroot()
        uniqueids: Dict[str, str] = {}
        # Jellyfin's NFO saver writes <imdbid>/<tmdbid>/<tvdbid>/<id>; <uniqueid> is Kodi-style
        for tag, t in (("imdbid", "imdb"), ("imdb_id", "imdb"), ("tmdbid", "tmdb"), ("tvdbid", "tvdb")):
            v = root.findtext(tag)
            if v and v.strip() and t not in uniqueids:
                uniqueids[t] = v.strip()
        for uid in root.findall("uniqueid"):
            t = uid.get("type", "")
            if uid.text and t and t not in uniqueids:
                uniqueids[t] = uid.text
        if not uniqueids:
            v = root.findtext("id")
            if v and v.strip().startswith("tt"):
                uniqueids["imdb"] = v.strip()
        actors: List[str] = []
        for actor in root.findall("actor"):
            name = actor.findtext("name")
            if name:
                actors.append(name)
        return {
            "title":     root.findtext("title") or "",
            "year":      root.findtext("year") or "",
            "plot":      root.findtext("plot") or "",
            "rating":    root.findtext("rating") or "",
            "genre":     [g.text for g in root.findall("genre") if g.text],
            "studio":    root.findtext("studio") or "",
            "tagline":   root.findtext("tagline") or "",
            "uniqueids": uniqueids,
            "actors":    actors,
        }
    except Exception:
        return {}


def _nfo_quality(nfo: Dict[str, Any]) -> str:
    """Classify NFO completeness for Jellyfin recognition.

    - 'none'    : no NFO file (empty dict)
    - 'partial' : NFO present but missing year or a unique external ID
    - 'full'    : NFO has at least year + unique ID (tmdb/imdb/tvdb etc.)
    """
    if not nfo:
        return "none"
    if nfo.get("year") and nfo.get("uniqueids"):
        return "full"
    return "partial"


def _find_nfo_path(item: Dict[str, Any]) -> Optional[Path]:
    if item["type"] == "series":
        p = Path(item["path"]) / "tvshow.nfo"
        return p if p.exists() else None
    if item["id"] != item["path"]:   # loose file item
        p = Path(item["id"]).with_suffix(".nfo")
        return p if p.exists() else None
    return next(Path(item["path"]).glob("*.nfo"), None)


# Maps UI field names to NFO XML element tags (a field may span several tag styles)
_NFO_FIELD_TAGS = {
    "title":     ["title"],
    "year":      ["year"],
    "plot":      ["plot"],
    "rating":    ["rating"],
    "genre":     ["genre"],
    "studio":    ["studio"],
    "tagline":   ["tagline"],
    # Both Kodi-style <uniqueid> and Jellyfin-style <imdbid>/<tmdbid>/<tvdbid>/<id>
    "uniqueids": ["uniqueid", "imdbid", "imdb_id", "tmdbid", "tvdbid", "id"],
    "actors":    ["actor"],
}


def _merge_nfo_fields(src_nfo: Path, dst_nfo: Path, fields: List[str], default_root: str) -> None:
    """Replace only the selected elements in the target NFO with the source's."""
    src_root = ET.parse(src_nfo).getroot()
    dst_root: Optional[ET.Element] = None
    if dst_nfo.exists():
        try:
            dst_root = ET.parse(dst_nfo).getroot()
        except ET.ParseError:
            dst_root = None   # unparsable target → start fresh
    if dst_root is None:
        dst_root = ET.Element(default_root)
    for field in fields:
        for tag in _NFO_FIELD_TAGS[field]:
            for el in dst_root.findall(tag):
                dst_root.remove(el)
            for el in src_root.findall(tag):
                dst_root.append(copy.deepcopy(el))
    try:
        ET.indent(dst_root)
    except AttributeError:
        pass   # Python < 3.9
    ET.ElementTree(dst_root).write(dst_nfo, encoding="utf-8", xml_declaration=True)


def read_nfo(item_id: str) -> Dict[str, Any]:
    """Return the raw NFO file content for an item (pretty-printed if valid XML)."""
    item = resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
    nfo_path = _find_nfo_path(item)
    if not nfo_path:
        raise HTTPException(404, "No NFO file found for this item")
    try:
        raw = nfo_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(500, f"Could not read NFO: {exc}")
    content = raw
    try:
        root = ET.fromstring(raw)
        ET.indent(root, space="  ")
        content = ET.tostring(root, encoding="unicode")
    except ET.ParseError:
        pass  # not clean XML (e.g. trailing URL) — show the raw file
    return {"path": str(nfo_path), "content": content}


def copy_nfo(source_id: str, target_id: str, fields: Optional[List[str]]) -> Dict[str, Any]:
    """Copy a whole NFO file — or selected fields — from one item to another."""
    src_item = resolve_item(source_id)
    dst_item = resolve_item(target_id)
    if not src_item or not dst_item:
        raise HTTPException(404, "Item not found")
    if src_item["type"] != dst_item["type"]:
        raise HTTPException(400, "Cannot copy NFO between a movie and a series")
    if fields:
        unknown = [f for f in fields if f not in _NFO_FIELD_TAGS]
        if unknown:
            raise HTTPException(400, f"Unknown NFO fields: {', '.join(unknown)}")
    src_nfo = _find_nfo_path(src_item)
    if not src_nfo:
        raise HTTPException(400, "Source item has no NFO file")
    # Merge into the target's existing NFO when there is one; only fall back to a
    # new file next to the video (or tvshow.nfo) when the target has no NFO yet.
    dst_nfo = _find_nfo_path(dst_item)
    if dst_nfo is None:
        if dst_item["type"] == "series":
            dst_nfo = Path(dst_item["path"]) / "tvshow.nfo"
        else:
            if not dst_item["files"]:
                raise HTTPException(400, "Target item has no video files")
            dst_nfo = Path(dst_item["files"][0]).with_suffix(".nfo")
    try:
        if fields:
            default_root = "tvshow" if dst_item["type"] == "series" else "movie"
            _merge_nfo_fields(src_nfo, dst_nfo, fields, default_root)
        else:
            shutil.copyfile(str(src_nfo), str(dst_nfo))
    except ET.ParseError as exc:
        raise HTTPException(500, f"Source NFO is not valid XML: {exc}")
    except OSError as exc:
        raise HTTPException(500, f"NFO copy failed: {exc}")
    # Update in-memory item so the UI reflects the change immediately
    nfo = parse_nfo(dst_nfo)
    with _lock:
        for i in _state["library"] + _state["downloads"]:
            if i["id"] == dst_item["id"]:
                i["nfo"] = nfo
                i["nfo_quality"] = _nfo_quality(nfo)
                break
    log.info("NFO copied (%s): %s → %s",
             ", ".join(fields) if fields else "whole file", src_nfo, dst_nfo)
    return {"ok": True, "from": str(src_nfo), "to": str(dst_nfo),
            "fields": fields or "all"}
