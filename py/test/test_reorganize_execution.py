"""Reorganize execution (Phase 3) against real files.

Builds a small library + download tree in a temporary folder inside py/test (removed afterwards),
patches the config in memory, scans it with the real scanner and calls the reorganize module
directly — the real library and web/config.yaml are never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_reorganize_execution.py
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

from py.ui import duplicates, reorganize, scanner  # noqa: E402
from py.ui.models import ReorganizeChange  # noqa: E402
from py.ui.settings import DEFAULT_NAMING, fs_path  # noqa: E402
from py.ui.state import _state  # noqa: E402

FULL_NFO = "<movie><title>Alien</title><year>1979</year><plot>p</plot><rating>8</rating><genre>g</genre></movie>"
POOR_NFO = "<movie><title>Alien</title></movie>"

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


class Tree:
    """The temporary library/download tree and helpers to build, scan and list it."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="_tmp_reorganize_", dir=Path(__file__).resolve().parent))
        self.lib, self.dl = str(self.tmp / "lib"), str(self.tmp / "dl")
        self.config = {"locations": [self.lib], "downloads": [self.dl],
                       "naming": dict(DEFAULT_NAMING), "transcode": {}}

    def mk(self, rel: str, text: str = "x") -> None:
        """Create a file (and its folders) below the tree; names may end with a space/dot."""
        p = os.path.join(str(self.tmp), rel)
        os.makedirs(fs_path(os.path.dirname(p)), exist_ok=True)
        with open(fs_path(p), "w", encoding="utf-8") as fh:
            fh.write(text)

    def listing(self) -> List[str]:
        """Every folder and file below the tree, relative to it."""
        root = fs_path(str(self.tmp))
        out = []
        for d, dirs, files in os.walk(root):
            out.extend(os.path.join(d, n)[len(root):] for n in dirs + files)
        return sorted(out)

    def rescan(self) -> None:
        """Scan the tree into the in-memory state, like a Basic Scan."""
        _state["library"] = scanner.scan_path(Path(self.lib), "library")
        _state["downloads"] = scanner.scan_path(Path(self.dl), "download")
        _state["transcoded"] = []

    def ids(self) -> List[str]:
        return [i["id"] for i in _state["library"] + _state["downloads"]]

    def remove(self) -> None:
        shutil.rmtree(fs_path(str(self.tmp)), ignore_errors=True)


def full_changes(analysis: Dict) -> List[ReorganizeChange]:
    """Select every suggestion (what 'All' in the dialog would do), conflicts excluded."""
    out = []
    for p in analysis["proposals"]:
        c = ReorganizeChange(item_id=p["item_id"])
        c.apply_folder = bool(p["folder"]) and not p["folder"]["conflict"]
        c.apply_file = bool(p["file"]) and not p["file"]["conflict"]
        c.apply_nfo = bool(p["nfo"]) and not p["nfo"]["conflict"]
        if p["nfos"] and p["nfos"]["best"]:
            c.nfo_action, c.nfo_files = "use_best", [f["name"] for f in p["nfos"]["files"]]
        c.delete_images = bool(p["images"])
        c.sanitize = [s["path"] for s in p["sanitize"] if not s["conflict"]]
        out.append(c)
    if analysis["parent_folders"]:
        out.append(ReorganizeChange(item_id=None, sanitize=[s["path"] for s in analysis["parent_folders"]]))
    return out


def show(results: List[Dict], tmp: Path) -> None:
    """Print the per-item results with their operations."""
    for r in results:
        name = os.path.basename(r["item_id"] or "<Parent folders>")
        print(f"  {'ok ' if r['ok'] else 'ERR'} {name}: {r['error'] or ''}")
        for o in r["operations"]:
            print(f"      {o['op']:6} {o['kind']:6} {os.path.basename(o['from'] or '')!r} -> "
                  f"{os.path.relpath(o['to'], str(tmp)) if o['to'] else ''}")


def run(t: Tree) -> None:
    lib, dl = t.lib, t.dl
    reorganize.load_config = lambda: t.config
    duplicates.NOT_DUP_PATH = t.tmp / "not-duplicates.json"

    # ── Title parsing ──────────────────────────────────────────────────────────
    check("underscore in a title becomes a space",
          reorganize.parse_name("Jurassic World_ Fallen Kingdom (2018)") == ("Jurassic World Fallen Kingdom", "2018"))
    check("scene name still parsed", reorganize.parse_name("Blade.Runner.2049.2017.1080p") == ("Blade Runner 2049", "2017"))

    # ── Tree ───────────────────────────────────────────────────────────────────
    t.mk("lib/Inception.2010.1080p/Inception.2010.1080p.mkv")
    t.mk("lib/Inception.2010.1080p/Inception.2010.1080p.en.srt")
    t.mk("lib/Inception.2010.1080p/Inception.2010.1080p.nfo", "<movie><title>Inception</title><year>2010</year></movie>")
    t.mk("lib/Heat (1995)/Heat (1995).mkv")
    t.mk("lib/Heat (1995)/Heat.1995.1080p.nfo", "<movie><title>Heat</title><year>1995</year></movie>")
    t.mk("lib/Alien (1979)/Alien (1979).mkv")
    t.mk("lib/Alien (1979)/movie.nfo", FULL_NFO)
    t.mk("lib/Alien (1979)/other.nfo", POOR_NFO)
    t.mk("lib/GoldenEye (1995)/GoldenEye (1995).mkv")
    t.mk("lib/goldeneye.1995/goldeneye.1995.mkv")
    t.mk("lib/Marvel /Iron Man (2008)/Iron Man (2008).mkv")
    t.mk("lib/Breaking Bad (2008)/Season 1 /Breaking Bad S01E01 .mkv")
    t.mk("lib/Breaking Bad (2008)/Season 1 /Breaking Bad S01E01 .en.srt")
    t.mk("lib/Breaking Bad (2008)/Season 1 /Breaking Bad S01E02.mkv")
    t.mk("dl/Dune.2021.2160p.mkv")
    t.mk("dl/Dune.2021.2160p.en.srt")
    t.mk("dl/Dune.2021.2160p-poster.jpg")
    t.mk("dl/Alien.1979.mkv")
    t.mk("dl/Aliens.1986.mkv")
    t.mk("dl/Aliens.1986.en.srt")
    t.rescan()
    duplicates._save_not_duplicates([sorted([os.path.join(lib, "Inception.2010.1080p"),
                                             os.path.join(dl, "Dune.2021.2160p.mkv")])])

    analysis = reorganize.analyse(t.ids())
    print(f"analysed {analysis['analysed_count']}, proposals {len(analysis['proposals'])}, "
          f"blocked {len(analysis['blocked'])}, parents {len(analysis['parent_folders'])}")
    changes = full_changes(analysis)

    # ── Guards before anything runs ────────────────────────────────────────────
    inc = os.path.join(lib, "Inception.2010.1080p")
    bad = reorganize.apply_changes([ReorganizeChange(item_id=inc, apply_folder=True, folder_name="a\\b")], True)
    check("separator in edited folder name refused", not bad[0]["ok"] and "not allowed" in bad[0]["error"])
    bad = reorganize.apply_changes([ReorganizeChange(item_id=inc, apply_folder=True, folder_name="Bad: name")], True)
    check("illegal char in edited folder name refused", not bad[0]["ok"])
    bad = reorganize.apply_changes([ReorganizeChange(item_id=inc, apply_file=True, file_name="Trailing ")], True)
    check("trailing space in edited file name refused", not bad[0]["ok"])
    bb = os.path.join(lib, "Breaking Bad (2008)")
    bad = reorganize.apply_changes([ReorganizeChange(item_id=bb, sanitize=[os.path.join(bb, "Season 1", "x.mkv")])], True)
    check("sanitize path not produced by re-analysis refused", not bad[0]["ok"])
    bad = reorganize.apply_changes([ReorganizeChange(item_id=None, sanitize=[os.path.join(lib, "Heat (1995)")])], True)
    check("parent-folder path that is not above an item refused", not bad[0]["ok"])
    alien = os.path.join(lib, "Alien (1979)")
    bad = reorganize.apply_changes([ReorganizeChange(item_id=alien, nfo_action="use_best", nfo_files=["movie.nfo"])], True)
    check("NFO action with a different nfo_files list refused", not bad[0]["ok"] and "changed" in bad[0]["error"])
    bad = reorganize.apply_changes([ReorganizeChange(item_id=bb, apply_file=True)], True)
    check("series file rename refused", not bad[0]["ok"])
    _state["scanning"] = True
    try:
        reorganize.apply_changes(changes, True)
        check("409 while scanning", False)
    except HTTPException as e:
        check("409 while scanning", e.status_code == 409)
    _state["scanning"] = False
    _state["transcode"] = {"running": True}
    try:
        reorganize.apply_changes(changes, True)
        check("409 while transcoding", False)
    except HTTPException as e:
        check("409 while transcoding", e.status_code == 409)
    _state["transcode"] = None
    heat = os.path.join(lib, "Heat (1995)")
    case = reorganize.apply_changes([ReorganizeChange(item_id=heat, apply_folder=True, folder_name="HEAT (1995)")], True)
    check("case-only folder rename allowed (dry run)", case[0]["ok"] and len(case[0]["operations"]) == 1)

    # ── Dry run touches nothing; execute does exactly the dry-run operations ───
    before = t.listing()
    dry = reorganize.apply_changes(changes, True)
    print("DRY RUN")
    show(dry, t.tmp)
    check("dry run touches nothing", t.listing() == before)
    check("all selected changes plan ok in dry run", all(r["ok"] for r in dry))

    real = reorganize.apply_changes(changes, False)
    print("EXECUTE")
    show(real, t.tmp)
    check("all changes executed ok", all(r["ok"] for r in real))
    check("execute did exactly the dry-run operations",
          [r["operations"] for r in dry] == [r["operations"] for r in real])

    after = set(t.listing())
    s = os.sep
    expect = [
        f"{s}lib{s}Inception (2010){s}Inception (2010).mkv",
        f"{s}lib{s}Inception (2010){s}Inception (2010).en.srt",
        f"{s}lib{s}Inception (2010){s}Inception (2010).nfo",
        f"{s}lib{s}Heat (1995){s}Heat (1995).nfo",
        f"{s}lib{s}Alien (1979){s}movie.nfo",
        f"{s}lib{s}Marvel{s}Iron Man (2008){s}Iron Man (2008).mkv",
        f"{s}lib{s}Breaking Bad (2008){s}Season 1{s}Breaking Bad S01E01.mkv",
        f"{s}lib{s}Breaking Bad (2008){s}Season 1{s}Breaking Bad S01E01.en.srt",
        f"{s}lib{s}goldeneye.1995{s}goldeneye (1995).mkv",   # folder conflicts (unticked), file rename doesn't
        f"{s}dl{s}Dune (2021){s}Dune (2021).mkv",
        f"{s}dl{s}Dune (2021){s}Dune (2021).en.srt",
        f"{s}dl{s}Alien (1979){s}Alien (1979).mkv",
        f"{s}dl{s}Aliens (1986){s}Aliens (1986).mkv",
        f"{s}dl{s}Aliens (1986){s}Aliens (1986).en.srt",
    ]
    for e in expect:
        check(f"exists {e}", e in after)
    check("other.nfo deleted (use best keeps movie.nfo)", f"{s}lib{s}Alien (1979){s}other.nfo" not in after)
    check("poster deleted", not any("poster" in p for p in after))
    check("Alien folder did not take the Aliens subtitle",
          f"{s}dl{s}Alien (1979){s}Aliens (1986).en.srt" not in after
          and len([p for p in after if p.startswith(f"{s}dl{s}Alien (1979){s}")]) == 1)
    nd = json.loads(duplicates.NOT_DUP_PATH.read_text(encoding="utf-8"))
    check("not-duplicate marks follow renamed/foldered items",
          sorted(nd[0]) == sorted([os.path.join(lib, "Inception (2010)"), os.path.join(dl, "Dune (2021)")]))

    # ── Rescan → re-analyse: only the deliberate conflict is left ──────────────
    t.rescan()
    again = reorganize.analyse(t.ids())
    left = [os.path.basename(p["item_id"]) for p in again["proposals"]]
    print("left after rescan:", left, "parents:", [e["path"] for e in again["parent_folders"]])
    check("rescan + re-analyse: only the GoldenEye conflict remains",
          left == ["goldeneye.1995"] and not again["parent_folders"])

    # ── Stale cache: format refused, sanitize still allowed ────────────────────
    t.mk("lib/Heat (1995)/Notes .txt")         # sanitize candidate
    t.mk("lib/Heat (1995)/Extra.mkv")          # unknown video → stale
    st = reorganize.apply_changes([ReorganizeChange(item_id=heat, apply_folder=True, folder_name="Heat X (1995)")], True)
    check("stale cache refuses format operations", not st[0]["ok"] and "rescan" in st[0]["error"])
    st = reorganize.apply_changes([ReorganizeChange(item_id=heat, sanitize=[os.path.join(heat, "Notes .txt")])], True)
    check("stale cache still allows sanitize (dry run)", st[0]["ok"])
    t.mk("lib/Heat (1995)/Heat (1995) .nfo")   # sanitizes onto the existing Heat (1995).nfo
    st = reorganize.apply_changes([ReorganizeChange(item_id=heat, sanitize=[os.path.join(heat, "Heat (1995) .nfo")])], True)
    check("sanitize onto an existing file refused", not st[0]["ok"] and "already exists" in st[0]["error"])

    # ── Conflict between two selected changes in one dry run ───────────────────
    t.mk("dl/Up.2009.mkv")
    t.mk("dl/Up.2009.720p.mkv")
    t.rescan()
    a = reorganize.analyse(t.ids())
    ups = [p for p in a["proposals"] if os.path.basename(p["item_id"]).startswith("Up.")]
    two = reorganize.apply_changes([ReorganizeChange(item_id=p["item_id"], apply_folder=True) for p in ups], True)
    check("second change into the same new folder refused in dry run", two[0]["ok"] and not two[1]["ok"])


def main() -> int:
    tree = Tree()
    saved = {k: _state.get(k) for k in ("library", "downloads", "transcoded", "scanning", "transcode")}
    try:
        run(tree)
    finally:
        _state.update(saved)
        tree.remove()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
