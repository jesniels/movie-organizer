"""Leftover folders (R-29): detection during the scan, the check after an action, and the delete — real files.

Builds a Library Location and a Download Location in a temporary folder inside py/test (removed
afterwards), scans them with the real scanner and calls fileops / leftovers directly — the real library,
web/config.yaml, the caches, not-duplicates.json and not-related.json are never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_leftovers.py
Exit code 0 = all checks passed.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from py.ui import bundles, duplicates, fileops, leftovers, scanner  # noqa: E402
from py.ui.settings import DEFAULT_LEFTOVER, DEFAULT_NAMING  # noqa: E402
from py.ui.state import _state  # noqa: E402

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


class Tree:
    """A Library Location (lib, with a disabled nested location lib/Holder/Inner) and a Download Location (dl)."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="_tmp_leftovers_", dir=Path(__file__).resolve().parent))
        self.lib, self.dl = self.tmp / "lib", self.tmp / "dl"
        self.inner = self.lib / "Holder" / "Inner"
        for p in (self.lib, self.dl, self.inner):
            p.mkdir(parents=True)
        self.config = {"locations": [str(self.lib), str(self.inner)], "downloads": [str(self.dl)],
                       "disabled_paths": [str(self.inner)], "naming": dict(DEFAULT_NAMING), "transcode": {},
                       "leftover": {k: list(v) for k, v in DEFAULT_LEFTOVER.items()}}

    def mk(self, rel: str, text: str = "x") -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def path(self, rel: str) -> str:
        return str(self.tmp / rel)

    def scan(self) -> List[Dict]:
        """Scan both locations like a Scan; returns the leftover folders found."""
        nesting = scanner._nesting_paths(self.config)
        found: List[Dict] = []
        lib, dl = [], []
        for root, loc, bucket in ((self.lib, "library", lib), (self.dl, "download", dl)):
            collector = leftovers.LeftoverCollector(self.config, str(root), loc)
            bucket.extend(scanner.scan_path(root, loc, scanner._nested_locations(str(root), nesting), collector))
            found.extend(collector.found)
        _state["library"], _state["downloads"], _state["transcoded"] = lib, dl, []
        _state["status"] = scanner._compute_status(lib, dl, [])
        _state["leftovers"] = list(found)
        return found

    def listing(self) -> List[str]:
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*"))

    def has(self, rel: str) -> bool:
        return (self.tmp / rel).exists()


def detection(t: Tree) -> None:
    t.mk("lib/James Bond/folder.jpg")
    t.mk("lib/James Bond/James Bond.nfo")
    t.mk("lib/James Bond/Old/poster.jpg")
    t.mk("lib/Mixed/GoldenEye (1995)/GoldenEye (1995).mkv")
    t.mk("lib/Mixed/Empty (2000)/movie.nfo")
    t.mk("lib/Notes/readme.txt")
    t.mk("lib/Recycle/movie.nfo")
    t.mk("lib/Recycle/$RECYCLE.BIN/x.jpg")
    t.mk("lib/$RECYCLE.BIN/Junk/a.nfo")
    t.mk("lib/Heat (1995)/Heat (1995).mkv")
    t.mk("lib/Heat (1995)/extrafanart/fanart1.jpg")
    t.mk("lib/Heat (1995)/@eaDir/SYNOINDEX_MEDIA_INFO")
    t.mk("lib/Heat (1995)/Subs/readme.txt")
    t.mk("lib/Show/tvshow.nfo")
    (t.lib / "Show" / "S01").mkdir()
    t.mk("lib/Series2/Season 1/Series2 S01E01.mkv")
    (t.lib / "Series2" / "Season 2").mkdir()
    t.mk("lib/Holder/Inner/inner.nfo")
    t.mk("lib/Synology/movie.nfo")
    t.mk("lib/Synology/@eaDir/SYNOINDEX_MEDIA_INFO")
    t.mk("lib/Synology/@eaDir/movie.nfo/SYNOPHOTO_THUMB_M.jpg")
    t.mk("lib/@eaDir/SYNOINDEX_MEDIA_INFO")
    t.mk("lib/Hidden/Thumbs.db")
    t.mk("lib/Subs/Heat.en.srt")
    t.mk("lib/Trick/Heat.trickplay/320 - 10x10/0.jpg")
    t.mk("lib/Loose/A (2000).mkv")
    t.mk("lib/Loose/B (2001)/B (2001).mkv")
    t.mk("lib/Loose/Gone (1990)/poster.jpg")
    t.mk("lib/Batch/A (2000).mkv")
    t.mk("lib/Batch/Fury (2014)/poster.jpg")
    t.mk("dl/Loose.2020.mkv")
    t.mk("dl/Fury (2014)/movie.nfo")
    (t.dl / "EmptyDir").mkdir()
    t.mk("dl/VideoInThumbs/@eaDir/clip.mp4")

    found = {os.path.relpath(e["path"], t.tmp): e for e in t.scan()}
    s = os.sep
    print("  found:", sorted(found))
    check("topmost only: James Bond listed, not its Old sub-folder",
          f"lib{s}James Bond" in found and f"lib{s}James Bond{s}Old" not in found
          and found[f"lib{s}James Bond"]["files"] == ["folder.jpg", "James Bond.nfo", f"Old{s}poster.jpg"])
    check("video below -> not listed; the emptied movie folder inside it is",
          f"lib{s}Mixed" not in found and f"lib{s}Mixed{s}Empty (2000)" in found)
    check("unknown file -> not listed", f"lib{s}Notes" not in found)
    check("system folder inside -> not listed; a system folder is never entered",
          f"lib{s}Recycle" not in found and not any("$RECYCLE.BIN" in k for k in found))
    check("nothing inside a movie folder (extrafanart, @eaDir)", not any(k.startswith(f"lib{s}Heat (1995)") for k in found))
    check("emptied series shell listed", f"lib{s}Show" in found and found[f"lib{s}Show"]["label"] == "Jellyfin data")
    check("series with episodes and an empty season -> not listed", not any(k.startswith(f"lib{s}Series2") for k in found))
    check("configured folder / folder containing one -> not listed", not any(k.startswith(f"lib{s}Holder") for k in found))
    check("@eaDir inside a leftover is part of it (any non-video file)",
          f"lib{s}Synology" in found and f"@eaDir{s}SYNOINDEX_MEDIA_INFO" in found[f"lib{s}Synology"]["files"]
          and "NAS thumbnails" in found[f"lib{s}Synology"]["label"])
    check("@eaDir never listed on its own", f"lib{s}@eaDir" not in found)
    check("ignored files hidden, label empty",
          found[f"lib{s}Hidden"]["hidden"] == ["Thumbs.db"] and found[f"lib{s}Hidden"]["label"] == "empty")
    check("orphaned subtitle labelled", found[f"lib{s}Subs"]["label"] == "subtitle, no video")
    check("trickplay folder labelled Jellyfin data", found[f"lib{s}Trick"]["label"] == "Jellyfin data")
    check("in a Bundle that also holds a loose file -> listed", f"lib{s}Loose{s}Gone (1990)" in found)
    check("in a movie folder (one video, no other movie folder) -> not listed",
          not any(k.startswith(f"lib{s}Batch") for k in found))
    check("download root may hold videos: emptied folder in it listed", f"dl{s}Fury (2014)" in found)
    check("empty folder listed as empty", found.get(f"dl{s}EmptyDir", {}).get("label") == "empty")
    check("a video inside @eaDir blocks the folder", f"dl{s}VideoInThumbs" not in found)

    # ── Cache round trip ──────────────────────────────────────────────────────────
    lib_found = [e for e in found.values() if e["location"] == "library"]
    scanner._save_location_cache(str(t.lib), "library", _state["library"], "2026-10-31T00:00:00", lib_found)
    scanner._save_location_cache(str(t.dl), "download", _state["downloads"], "2026-10-31T00:00:00")   # old format
    payload = json.loads(scanner._cache_file_for(str(t.lib)).read_text(encoding="utf-8"))
    check("cache: path relative to the location root, files relative to the folder",
          {"path": "James Bond", "files": ["folder.jpg", "James Bond.nfo", f"Old{s}poster.jpg"]} in payload["leftovers"])
    _state["leftovers"] = []
    scanner._load_all_caches(t.config)
    key = lambda e: (e["path"], tuple(e["files"]), e["label"], tuple(e["hidden"]), e["location"], e["root"])
    check("cache: restored to the in-memory form; a cache without leftovers loads as none",
          sorted(map(key, _state["leftovers"])) == sorted(map(key, lib_found)))


def after_action(t: Tree) -> None:
    t.mk("dl/Common/Movie (2001)/Movie (2001).mkv")
    t.mk("dl/Common/folder.jpg")
    t.mk("dl/Pair/A (2000)/A (2000).mkv")
    t.mk("dl/Pair/B (2001)/B (2001).mkv")
    t.mk("dl/Solo/X (2003)/X (2003).mkv")
    t.mk("dl/Solo/poster.jpg")
    t.scan()
    movie, a, x = t.path("dl/Common/Movie (2001)"), t.path("dl/Pair/A (2000)"), t.path("dl/Solo/X (2003)")
    _state["leftovers"] = []

    dry = fileops.move_items([movie], str(t.lib), True)
    check("dry run -> nothing reported or remembered", "leftovers" not in dry[0] and _state["leftovers"] == [])
    [r] = fileops.move_items([movie], str(t.lib), False)
    check("moving the last movie out of a folder with folder.jpg -> that folder reported",
          r["ok"] and r["leftovers"] == [t.path("dl/Common")])
    check("... and added to the in-memory list", [e["path"] for e in _state["leftovers"]] == [t.path("dl/Common")])
    [r] = fileops.move_items([a], str(t.lib), False)
    check("a parent that still holds a video -> nothing", r["ok"] and r["leftovers"] == [])
    [r] = fileops.delete_items([x], False)
    check("delete: the emptied parent folder reported", r["ok"] and r["leftovers"] == [t.path("dl/Solo")])


def delete(t: Tree) -> None:
    for rel in ("lib/James Bond/folder.jpg", "lib/James Bond/James Bond.nfo", "lib/James Bond/Old/poster.jpg",
                "lib/Synology/movie.nfo", "lib/Synology/@eaDir/SYNOINDEX_MEDIA_INFO",
                "lib/Heat (1995)/Heat (1995).mkv", "lib/Heat (1995)/extrafanart/fanart1.jpg",
                "lib/Heat (1995)/@eaDir/SYNOINDEX_MEDIA_INFO", "lib/Holder/Inner/inner.nfo", "lib/Notes/readme.txt",
                "lib/Loose/A (2000).mkv", "lib/Loose/B (2001)/B (2001).mkv", "lib/Loose/Gone (1990)/poster.jpg",
                "lib/Batch/A (2000).mkv", "lib/Batch/Fury (2014)/poster.jpg"):
        t.mk(rel)
    t.scan()
    scanner._save_location_cache(str(t.lib), "library", _state["library"], "2026-10-31T00:00:00",
                                 [e for e in _state["leftovers"] if e["location"] == "library"])
    entries = {e["path"]: e for e in _state["leftovers"]}
    bond, syn = t.path("lib/James Bond"), t.path("lib/Synology")
    body = lambda p: {"path": p, "files": list(entries[p]["files"])}
    by_path = lambda results: {r["path"]: r for r in results}

    before = t.listing()
    res = by_path(leftovers.delete_leftovers([body(bond), body(syn)], True))
    check("dry run touches nothing and lists the operations",
          t.listing() == before and res[bond]["ok"]
          and [o["op"] for o in res[bond]["operations"]] == ["delete", "delete", "delete", "rmdir", "rmdir"])

    changed = {"path": bond, "files": ["folder.jpg"]}
    check("content differing from the sent list -> refused",
          "changed" in leftovers.delete_leftovers([changed], True)[0]["error"])
    t.mk("lib/James Bond/new.jpg")
    r = leftovers.delete_leftovers([body(bond)], False)[0]
    check("a file added in the meantime -> folder stays", not r["ok"] and t.has("lib/James Bond/folder.jpg"))
    (t.lib / "James Bond" / "new.jpg").unlink()
    t.mk("lib/James Bond/Old/copy.mkv")
    r = leftovers.delete_leftovers([{"path": bond, "files": entries[bond]["files"] + ["Old/copy.mkv"]}], False)[0]
    check("a video copied in -> refused", not r["ok"] and "video" in r["error"] and t.has("lib/James Bond/Old/copy.mkv"))
    (t.lib / "James Bond" / "Old" / "copy.mkv").unlink()

    fanart = t.path("lib/Heat (1995)/extrafanart")
    r = leftovers.delete_leftovers([{"path": fanart, "files": ["fanart1.jpg"]}], False)[0]
    check("inside a movie folder (on disk) -> refused", not r["ok"] and "movie or series" in r["error"]
          and t.has("lib/Heat (1995)/extrafanart/fanart1.jpg"))
    r = leftovers.delete_leftovers([{"path": t.path("lib/Batch/Fury (2014)"), "files": ["poster.jpg"]}], True)[0]
    check("inside a folder that became a movie folder -> refused", not r["ok"] and "movie or series" in r["error"])
    r = leftovers.delete_leftovers([{"path": t.path("lib/Loose/Gone (1990)"), "files": ["poster.jpg"]}], True)[0]
    check("inside a Bundle that also holds a loose file -> allowed", r["ok"])
    r = leftovers.delete_leftovers([{"path": str(t.lib), "files": []}], False)[0]
    check("the location itself -> refused", not r["ok"])
    r = leftovers.delete_leftovers([{"path": str(t.inner), "files": ["inner.nfo"]}], False)[0]
    check("a configured (disabled) location -> refused", not r["ok"] and "configured" in r["error"])
    r = leftovers.delete_leftovers([{"path": t.path("lib/Holder"), "files": [f"Inner{os.sep}inner.nfo"]}], False)[0]
    check("a folder containing a configured location -> refused", not r["ok"] and "configured" in r["error"])
    r = leftovers.delete_leftovers([{"path": t.path("lib/Notes"), "files": ["readme.txt"]}], False)[0]
    check("a file on no list -> refused", not r["ok"] and t.has("lib/Notes/readme.txt"))
    outside = t.tmp / "outside"
    outside.mkdir()
    r = leftovers.delete_leftovers([{"path": str(outside), "files": []}], False)[0]
    check("outside every location -> refused", not r["ok"] and outside.exists())

    _state["scanning"] = True
    try:
        leftovers.delete_leftovers([body(bond)], True)
        check("409 while a scan runs", False)
    except HTTPException as e:
        check("409 while a scan runs", e.status_code == 409)
    _state["scanning"] = False

    res = by_path(leftovers.delete_leftovers([body(bond), body(syn)], False))
    check("real delete: folders gone, @eaDir with SYNOINDEX_MEDIA_INFO deleted with its parent",
          res[bond]["ok"] and res[syn]["ok"] and not t.has("lib/James Bond") and not t.has("lib/Synology"))
    check("deleted folders dropped from the in-memory list",
          not any(e["path"] in (bond, syn) for e in _state["leftovers"]))
    cached = json.loads(scanner._cache_file_for(str(t.lib)).read_text(encoding="utf-8"))["leftovers"]
    check("deleted folders dropped from the cache, the others kept",
          [e["path"] for e in cached] == [f"Loose{os.sep}Gone (1990)"])
    check("Heat (1995) and its @eaDir untouched", t.has("lib/Heat (1995)/@eaDir/SYNOINDEX_MEDIA_INFO"))


def main() -> int:
    saved = (duplicates.NOT_DUP_PATH, bundles.NOT_RELATED_PATH, bundles.load_config, fileops.load_config,
             leftovers.load_config, scanner.CACHE_DIR)
    trees: List[Tree] = []
    try:
        for part in (detection, after_action, delete):
            t = Tree()
            trees.append(t)
            duplicates.NOT_DUP_PATH = t.tmp / "not-duplicates.json"
            bundles.NOT_RELATED_PATH = t.tmp / "not-related.json"
            bundles.load_config = fileops.load_config = leftovers.load_config = lambda t=t: t.config
            scanner.CACHE_DIR = t.tmp / "cache"
            scanner.CACHE_DIR.mkdir()
            print(f"-- {part.__name__}")
            part(t)
    finally:
        (duplicates.NOT_DUP_PATH, bundles.NOT_RELATED_PATH, bundles.load_config, fileops.load_config,
         leftovers.load_config, scanner.CACHE_DIR) = saved
        _state["scanning"] = False
        for t in trees:
            shutil.rmtree(t.tmp, ignore_errors=True)
    print(f"\n{'OK' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
