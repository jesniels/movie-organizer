"""Scanner: a folder with a loose video next to movie subfolders is a Bundle, not one movie (R-13).

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
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
sys.exit(1 if failures else 0)
