"""Move of loose files takes their own sidecars along (R-26), against real files.

Builds a small download/library tree in a temporary folder inside py/test (removed afterwards)
and calls fileops.move_items directly — the real library and web/config.yaml are never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_move_sidecars.py
Exit code 0 = all checks passed.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from py.ui import duplicates, fileops  # noqa: E402
from py.ui.state import _state  # noqa: E402

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def mk(base: Path, rel: str) -> Path:
    """Create a small file (and its folders) below base."""
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return p


def loose(video: Path) -> Dict:
    """A loose-file item as the scanner builds it: id = file, path = containing folder."""
    return {"id": str(video), "path": str(video.parent), "folder": video.parent.name,
            "files": [str(video)], "type": "movie", "title": video.stem, "location": "download"}


def names(folder: Path) -> List[str]:
    return sorted(p.name for p in folder.iterdir())


def run(tmp: Path) -> None:
    dl, lib = tmp / "dl", tmp / "lib"
    lib.mkdir(parents=True)
    duplicates.NOT_DUP_PATH = tmp / "not-duplicates.json"

    dune = mk(dl, "Dune.2021.mkv")
    for n in ("Dune.2021.nfo", "Dune.2021-poster.jpg", "Dune.2021.en.srt", "Dune.2021.part2.mkv", "Other.mkv"):
        mk(dl, n)
    heat = mk(dl, "Heat.mkv")
    mk(dl, "Heat.avi")
    mk(dl, "Heat.nfo")              # shared by Heat.mkv and Heat.avi
    up = mk(dl, "Up.mkv")
    mk(dl, "Up.nfo")
    mk(lib, "Up.nfo")               # sidecar destination taken
    folder_item = mk(dl, "Alien (1979)/Alien (1979).mkv").parent
    mk(dl, "Alien (1979)/movie.nfo")
    _state["downloads"] = [loose(dune), loose(heat), loose(up),
                           {"id": str(folder_item), "path": str(folder_item), "folder": folder_item.name,
                            "files": [str(folder_item / "Alien (1979).mkv")], "type": "movie",
                            "title": "Alien", "location": "download"}]
    _state["library"], _state["transcoded"] = [], []
    ids = [i["id"] for i in _state["downloads"]]

    before = names(dl)
    dry = {r["id"]: r for r in fileops.move_items(ids, str(lib), True)}
    check("dry run touches nothing", names(dl) == before and names(lib) == ["Up.nfo"])
    check("dry run lists Dune's sidecars",
          sorted(os.path.basename(a) for a in dry[str(dune)]["also"])
          == ["Dune.2021-poster.jpg", "Dune.2021.en.srt", "Dune.2021.nfo"])
    check("dry run: shared NFO not listed for Heat", dry[str(heat)]["ok"] and dry[str(heat)]["also"] == [])
    check("dry run: Up refused, its NFO exists at the destination",
          not dry[str(up)]["ok"] and "Up.nfo" in dry[str(up)]["error"])

    real = {r["id"]: r for r in fileops.move_items(ids, str(lib), False)}
    check("Dune moved with its sidecars", real[str(dune)]["ok"] and not real[str(dune)].get("warning")
          and {"Dune.2021.mkv", "Dune.2021.nfo", "Dune.2021-poster.jpg", "Dune.2021.en.srt"} <= set(names(lib)))
    check("other videos stay in the download folder",
          {"Dune.2021.part2.mkv", "Other.mkv", "Heat.avi"} <= set(names(dl)))
    check("shared Heat.nfo stays with Heat.avi", "Heat.nfo" in names(dl) and "Heat.mkv" in names(lib))
    check("Up refused: nothing moved, destination NFO untouched",
          not real[str(up)]["ok"] and {"Up.mkv", "Up.nfo"} <= set(names(dl)))
    check("folder item moved as a whole", names(lib / "Alien (1979)") == ["Alien (1979).mkv", "movie.nfo"])

    # ── Common folder (R-27) ──────────────────────────────────────────────────────────
    fileops.load_config = lambda: {"locations": [str(lib)], "downloads": [str(dl)], "transcode": {}}
    drno = mk(dl, "James Bond/Dr. No (1962)/Dr. No (1962).mkv").parent
    gold = mk(dl, "James Bond/Goldfinger.1964.mkv")
    mk(dl, "James Bond/Goldfinger.1964.nfo")
    thun = mk(dl, "James Bond/Thunderball.1965.mkv")
    _state["downloads"] = [{"id": str(drno), "path": str(drno), "folder": drno.name,
                            "files": [str(drno / "Dr. No (1962).mkv")], "type": "movie",
                            "title": "Dr. No", "location": "download"},
                           loose(gold), loose(thun), loose(up)]
    groups = fileops.move_groups([str(drno), str(gold), str(up)])
    check("common folder found, root-level item not grouped",
          len(groups) == 1 and groups[0]["group"] == "James Bond"
          and sorted(groups[0]["ids"]) == sorted([str(drno), str(gold)]) and groups[0]["total"] == 3)

    bond_dl, bond_lib = dl / "James Bond", lib / "James Bond"
    dry = fileops.move_items([str(drno), str(gold)], str(lib), True, keep_groups=True)
    check("keep_groups dry run targets the same folder in the destination",
          all(r["ok"] for r in dry) and all(Path(r["to"]).parent == bond_lib for r in dry)
          and not bond_lib.exists())
    real = fileops.move_items([str(drno), str(gold)], str(lib), False, keep_groups=True)
    check("keep_groups moves into the folder, sidecar follows",
          all(r["ok"] for r in real)
          and names(bond_lib) == ["Dr. No (1962)", "Goldfinger.1964.mkv", "Goldfinger.1964.nfo"])
    check("source common folder kept while it still holds an item",
          names(bond_dl) == ["Thunderball.1965.mkv"] and not any(r["removed_folders"] for r in real))
    last = fileops.move_items([str(thun)], str(lib), False, keep_groups=True)
    check("last item: emptied source folder removed",
          last[0]["ok"] and not bond_dl.exists() and last[0]["removed_folders"] == [str(bond_dl)]
          and "Thunderball.1965.mkv" in names(bond_lib))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_tmp_move_", dir=Path(__file__).resolve().parent))
    saved = {k: _state.get(k) for k in ("library", "downloads", "transcoded")}
    saved_nd = duplicates.NOT_DUP_PATH
    saved_cfg = fileops.load_config
    try:
        run(tmp)
    finally:
        _state.update(saved)
        duplicates.NOT_DUP_PATH = saved_nd
        fileops.load_config = saved_cfg
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
