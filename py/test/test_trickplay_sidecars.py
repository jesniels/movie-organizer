"""A loose file's Jellyfin `<stem>.trickplay` folder follows it in Move, Delete and Reorganize (R-30).

Builds small download/library trees in a temporary folder inside py/test (removed afterwards) and
calls fileops / reorganize directly — the real library and web/config.yaml are never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_trickplay_sidecars.py
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

from py.ui import duplicates, fileops, reorganize, scanner  # noqa: E402
from py.ui.models import ReorganizeChange  # noqa: E402
from py.ui.settings import DEFAULT_NAMING  # noqa: E402
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


def trickplay(base: Path, stem: str, extra: str = "") -> Path:
    """A Jellyfin trickplay folder with tiles in a sub-folder (plus an optional non-image file)."""
    folder = base / f"{stem}.trickplay"
    mk(folder, "320 - 10x10/0.jpg")
    mk(folder, "320 - 10x10/1.jpg")
    if extra:
        mk(folder, extra)
    return folder


def loose(video: Path) -> Dict:
    """A loose-file item as the scanner builds it: id = file, path = containing folder."""
    return {"id": str(video), "path": str(video.parent), "folder": video.parent.name,
            "files": [str(video)], "type": "movie", "title": video.stem, "location": "download"}


def listing(folder: Path) -> List[str]:
    """Every file and folder below folder, relative to it."""
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_move(tmp: Path) -> None:
    dl, lib = tmp / "move" / "dl", tmp / "move" / "lib"
    lib.mkdir(parents=True)
    dune = mk(dl, "Dune.mkv")
    trickplay(dl, "Dune")
    heat = mk(dl, "Heat.mkv")
    mk(dl, "Heat.mp4")
    trickplay(dl, "Heat")               # shared by Heat.mkv and Heat.mp4
    up = mk(dl, "Up.mkv")
    trickplay(dl, "Up")
    mk(dl, "Up.trickplay/320 - 10x10/new.jpg")
    trickplay(lib, "Up")                # destination holds only images → replaced
    odd = mk(dl, "Odd.mkv")
    trickplay(dl, "Odd")
    trickplay(lib, "Odd", extra="notes.txt")   # destination holds something else → refused
    _state["downloads"] = [loose(dune), loose(heat), loose(up), loose(odd)]
    ids = [str(dune), str(heat), str(up), str(odd)]

    before = listing(dl), listing(lib)
    dry = {r["id"]: r for r in fileops.move_items(ids, str(lib), True)}
    check("move dry run touches nothing", (listing(dl), listing(lib)) == before)
    check("move dry run lists the trickplay folder", dry[str(dune)]["also"] == [str(dl / "Dune.trickplay")])
    check("move dry run: shared trickplay folder not listed", dry[str(heat)]["ok"] and dry[str(heat)]["also"] == [])
    check("move dry run: image-only trickplay destination will be replaced",
          dry[str(up)]["ok"] and dry[str(up)]["replaces"] == [str(lib / "Up.trickplay")])
    check("move dry run: trickplay destination with other files refuses the item",
          not dry[str(odd)]["ok"] and "Odd.trickplay" in dry[str(odd)]["error"])

    real = {r["id"]: r for r in fileops.move_items(ids, str(lib), False)}
    check("trickplay folder moved with the video",
          real[str(dune)]["ok"] and (lib / "Dune.mkv").is_file()
          and (lib / "Dune.trickplay" / "320 - 10x10" / "1.jpg").is_file() and not (dl / "Dune.trickplay").exists())
    check("shared trickplay folder stays", (lib / "Heat.mkv").is_file() and (dl / "Heat.trickplay").is_dir())
    check("image-only destination trickplay replaced by the moving one",
          real[str(up)]["ok"] and not real[str(up)].get("warning") and (lib / "Up.mkv").is_file()
          and (lib / "Up.trickplay" / "320 - 10x10" / "new.jpg").is_file() and not (dl / "Up.trickplay").exists())
    check("refused item: nothing moved, destination untouched",
          (dl / "Odd.mkv").is_file() and (dl / "Odd.trickplay").is_dir() and (lib / "Odd.trickplay" / "notes.txt").is_file())

    # Destination appears between the check and the move: the folder stays, the video still moves
    gold = mk(dl, "Goldfinger.mkv")
    trickplay(dl, "Goldfinger")
    _state["downloads"] = [loose(gold)]
    real_move = shutil.move

    def racing_move(src: str, dst: str) -> str:
        out = real_move(src, dst)
        if src.endswith("Goldfinger.mkv"):
            (lib / "Goldfinger.trickplay").mkdir()
        return out

    shutil.move = racing_move
    try:
        res = fileops.move_items([str(gold)], str(lib), False)[0]
    finally:
        shutil.move = real_move
    check("trickplay destination appeared right before: video moved, folder stays, warning",
          res["ok"] and (lib / "Goldfinger.mkv").is_file() and (dl / "Goldfinger.trickplay" / "320 - 10x10").is_dir()
          and "Goldfinger.trickplay" in res.get("warning", ""))


def test_delete(tmp: Path) -> None:
    dl = tmp / "delete" / "dl"
    alien = mk(dl, "Alien.mkv")
    trickplay(dl, "Alien")
    heat = mk(dl, "Heat.mkv")
    mk(dl, "Heat.mp4")
    trickplay(dl, "Heat")
    odd = mk(dl, "Odd.mkv")
    trickplay(dl, "Odd", extra="notes.txt")
    _state["downloads"] = [loose(alien), loose(heat), loose(odd)]
    ids = [str(alien), str(heat), str(odd)]
    fileops.load_config = lambda: {"locations": [], "downloads": [str(dl)], "transcode": {}}

    before = listing(dl)
    dry = {r["id"]: r for r in fileops.delete_items(ids, True)}
    check("delete dry run touches nothing", listing(dl) == before)
    check("delete dry run lists the image-only trickplay folder", dry[str(alien)]["also"] == [str(dl / "Alien.trickplay")])
    check("delete dry run reports a trickplay folder with other files",
          dry[str(odd)]["also"] == [] and "notes.txt" in dry[str(odd)].get("warning", ""))

    real = {r["id"]: r for r in fileops.delete_items(ids, False)}
    check("image-only trickplay folder removed", real[str(alien)]["ok"] and not (dl / "Alien.trickplay").exists())
    check("shared trickplay folder stays", not heat.exists() and (dl / "Heat.trickplay").is_dir())
    check("trickplay folder with an unknown file stays and is reported",
          not odd.exists() and (dl / "Odd.trickplay" / "notes.txt").is_file()
          and (dl / "Odd.trickplay" / "320 - 10x10" / "0.jpg").is_file() and "notes.txt" in real[str(odd)].get("warning", ""))


def test_reorganize(tmp: Path) -> None:
    dl = tmp / "reorganize" / "dl"
    mk(dl, "Heat.1995.mkv")
    trickplay(dl, "Heat.1995")
    mk(dl, "Dune.2021.mkv")
    trickplay(dl, "Dune.2021")
    config = {"locations": [], "downloads": [str(dl)], "naming": dict(DEFAULT_NAMING), "transcode": {}}
    reorganize.load_config = lambda: config
    _state["library"], _state["transcoded"] = [], []
    _state["downloads"] = scanner.scan_path(dl, "download")
    heat, dune = str(dl / "Heat.1995.mkv"), str(dl / "Dune.2021.mkv")

    analysis = reorganize.analyse([heat, dune])
    props = {p["item_id"]: p for p in analysis["proposals"]}
    check("analysis lists the trickplay folder with the files to move",
          any(m["from"] == "Heat.1995.trickplay" for m in props[heat]["move"]))
    changes = [ReorganizeChange(item_id=heat, apply_folder=True),
               ReorganizeChange(item_id=dune, apply_folder=True, apply_file=True)]
    before = listing(dl)
    dry = reorganize.apply_changes(changes, True)
    check("reorganize dry run touches nothing", listing(dl) == before and all(r["ok"] for r in dry))
    check("reorganize dry run moves the trickplay folder",
          any((o["from"] or "").endswith("Heat.1995.trickplay") and o["kind"] == "folder" for o in dry[0]["operations"]))
    real = reorganize.apply_changes(changes, False)
    check("reorganize executed", all(r["ok"] for r in real))
    check("trickplay folder inside the new movie folder",
          (dl / "Heat (1995)" / "Heat.1995.trickplay" / "320 - 10x10" / "0.jpg").is_file()
          and not (dl / "Heat.1995.trickplay").exists())
    check("trickplay folder follows the renamed video",
          (dl / "Dune (2021)" / "Dune (2021).mkv").is_file()
          and (dl / "Dune (2021)" / "Dune (2021).trickplay" / "320 - 10x10" / "1.jpg").is_file())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_tmp_trickplay_", dir=Path(__file__).resolve().parent))
    saved = {k: _state.get(k) for k in ("library", "downloads", "transcoded")}
    saved_nd, saved_fo, saved_ro = duplicates.NOT_DUP_PATH, fileops.load_config, reorganize.load_config
    duplicates.NOT_DUP_PATH = tmp / "not-duplicates.json"
    _state["library"], _state["transcoded"] = [], []
    try:
        test_move(tmp)
        test_delete(tmp)
        test_reorganize(tmp)
    finally:
        _state.update(saved)
        duplicates.NOT_DUP_PATH = saved_nd
        fileops.load_config, reorganize.load_config = saved_fo, saved_ro
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
