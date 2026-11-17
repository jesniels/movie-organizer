"""Scanner: Bundle folders (R-13), the item's `bundle` field, its cache round trip, and the NFO collection.

Builds a small tree in a temporary folder inside py/test (removed afterwards) and scans it.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_scanner_bundles.py
Exit code 0 = all checks passed.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from py.ui import scanner  # noqa: E402
from py.ui.nfo import parse_nfo  # noqa: E402

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def touch(path: Path) -> None:
    """Create an empty file and its parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


tmp = Path(tempfile.mkdtemp(prefix="_tmp_bundles_", dir=Path(__file__).parent))
try:
    lib = tmp / "Movies"
    touch(lib / "Netflix" / "The Guns of Navarone (1961).mkv")
    touch(lib / "Netflix" / "The Terminator (1984)" / "The Terminator (1984).mkv")
    touch(lib / "Netflix" / "Fury (2014)" / "Fury (2014).mkv")
    touch(lib / "Alien (1979)" / "Alien (1979).mkv")
    touch(lib / "Alien (1979)" / "Extras" / "Making of.mkv")
    touch(lib / "Show" / "Show S01E01.mkv")
    touch(lib / "Show" / "Season 2" / "Show S02E01.mkv")

    items = {Path(i["id"]).name: i for i in scanner.scan_path(lib, "library")}

    guns = items.get("The Guns of Navarone (1961).mkv")
    check("loose file in a Bundle is its own movie", guns is not None)
    check("loose file has exactly one file", guns is not None and len(guns["files"]) == 1)
    check("Bundle folder itself is not an item", "Netflix" not in items)
    check("movie subfolders of the Bundle are items",
          "The Terminator (1984)" in items and "Fury (2014)" in items)

    alien = items.get("Alien (1979)")
    check("extras folder stays part of its movie", alien is not None and len(alien["files"]) == 2)

    show = items.get("Show")
    check("series with a season folder stays one series",
          show is not None and show["type"] == "series" and len(show["files"]) == 2)

    # ── The item's `bundle` field (Library Locations only) ───────────────────────
    netflix = str(lib / "Netflix")
    check("root movie folder has no bundle", alien is not None and alien["bundle"] == "")
    check("movie folder in a bundle gets the bundle path", items["Fury (2014)"]["bundle"] == netflix)
    check("loose file next to movie folders gets the bundle path", guns is not None and guns["bundle"] == netflix)
    check("series never has a bundle", show is not None and show["bundle"] == "")

    touch(lib / "James Bond" / "GoldenEye (1995)" / "GoldenEye (1995).mkv")
    touch(lib / "Pair" / "GoldenEye (1995).mkv")
    touch(lib / "Pair" / "Skyfall (2012).mkv")
    touch(lib / "Heat (1995)" / "Heat - CD1.mkv")
    touch(lib / "Heat (1995)" / "Heat - CD2.mkv")
    touch(lib / "Up (2009)" / "Up (2009) - 1080p.mkv")
    touch(lib / "Up (2009)" / "Up (2009) - 2160p.mkv")
    touch(lib / "Mann" / "Thief (1981)" / "Thief - CD1.mkv")
    touch(lib / "Mann" / "Thief (1981)" / "Thief - CD2.mkv")
    touch(lib / "Mann" / "Collateral (2004)" / "Collateral (2004).mkv")
    touch(lib / "A" / "B" / "Movie (2000)" / "Movie (2000).mkv")
    touch(lib / "Marvel" / "Agents of SHIELD" / "Season 1" / "Agents of SHIELD S01E01.mkv")
    touch(lib / "Marvel" / "Iron Man (2008).mkv")
    by_id = {i["id"]: i for i in scanner.scan_path(lib, "library")}
    bundle = lambda rel: by_id[str(lib / rel)]["bundle"]
    check("a series folder does not make its parent a bundle", bundle("Marvel/Iron Man (2008).mkv") == "")
    check("one movie folder is enough for a bundle",
          bundle("James Bond/GoldenEye (1995)") == str(lib / "James Bond"))
    check("two different loose movies in a folder make it a bundle",
          bundle("Pair/GoldenEye (1995).mkv") == bundle("Pair/Skyfall (2012).mkv") == str(lib / "Pair"))
    check("CD1/CD2 parts in their folder have no bundle",
          bundle("Heat (1995)/Heat - CD1.mkv") == bundle("Heat (1995)/Heat - CD2.mkv") == "")
    check("multi-version files in their folder have no bundle", bundle("Up (2009)/Up (2009) - 1080p.mkv") == "")
    check("parts of one movie inside a bundle get the bundle",
          bundle("Mann/Thief (1981)/Thief - CD1.mkv") == str(lib / "Mann")
          and bundle("Mann/Collateral (2004)") == str(lib / "Mann"))
    check("movie two folders deep has no bundle", bundle("A/B/Movie (2000)") == "")

    dl_items = scanner.scan_path(lib, "download")
    check("Download Location: never a bundle", all(i["bundle"] == "" for i in dl_items))

    cached = scanner._item_to_cache(by_id[str(lib / "James Bond/GoldenEye (1995)")], str(lib))
    check("cache stores the bundle relative to the root", cached["bundle"] == "James Bond")
    restored = scanner._item_from_cache(cached, str(lib))
    check("cache round trip keeps the absolute bundle path", restored["bundle"] == str(lib / "James Bond"))
    check("old cache without the field loads as empty",
          scanner._item_from_cache({"path": "Alien (1979)", "files": []}, str(lib))["bundle"] == "")
    check("empty bundle not stored in the cache", "bundle" not in scanner._item_to_cache(alien, str(lib)))

    # ── NFO collection ───────────────────────────────────────────────────────────
    def nfo(text: str) -> dict:
        p = tmp / "x.nfo"
        p.write_text(text, encoding="utf-8")
        return parse_nfo(p)

    n = nfo("<movie><title>Skyfall</title><set><name>James Bond Collection</name><overview>o</overview></set>"
            "<collectionnumber>645</collectionnumber></movie>")
    check("NFO <set><name> and <collectionnumber>", n["set"] == "James Bond Collection" and n["set_id"] == "645")
    n = nfo("<movie><title>Rocky</title><set>Rocky Collection</set></movie>")
    check("NFO older <set>text</set>", n["set"] == "Rocky Collection" and n["set_id"] == "")
    n = nfo("<movie><title>X</title><collectionnumber>abc</collectionnumber></movie>")
    check("NFO non-numeric collectionnumber ignored", n["set_id"] == "")
    n = nfo("<movie><title>Heat</title></movie>")
    check("NFO without a collection", n["set"] == "" and n["set_id"] == "")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
sys.exit(1 if failures else 0)
