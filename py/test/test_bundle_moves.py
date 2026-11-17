"""Bundle moves (R-28): into a new or existing bundle, out of a bundle, merge, rename — against real files.

Builds two Library Locations and a Download Location in a temporary folder inside py/test (removed
afterwards), scans them with the real scanner and calls fileops directly — the real library,
web/config.yaml, not-duplicates.json and not-related.json are never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_bundle_moves.py
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

from py.ui import bundles, duplicates, fileops, scanner  # noqa: E402
from py.ui.settings import DEFAULT_NAMING  # noqa: E402
from py.ui.state import _state  # noqa: E402

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def refused(label: str, call, status: int = 400) -> None:
    """Check that call() raises an HTTPException with the given status."""
    try:
        call()
        check(label, False)
    except HTTPException as e:
        check(label, e.status_code == status)


class Tree:
    """Two Library Locations (lib, lib2) and a Download Location (dl) below a temporary folder."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="_tmp_bundlemove_", dir=Path(__file__).resolve().parent))
        self.lib, self.lib2, self.dl = self.tmp / "lib", self.tmp / "lib2", self.tmp / "dl"
        for p in (self.lib, self.lib2, self.dl):
            p.mkdir()
        self.config = {"locations": [str(self.lib), str(self.lib2)], "downloads": [str(self.dl)],
                       "disabled_paths": [], "naming": dict(DEFAULT_NAMING), "transcode": {}}

    def mk(self, rel: str, text: str = "x") -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def rescan(self) -> None:
        """Scan into the in-memory state and compute the duplicate groups, like a Scan."""
        lib = scanner.scan_path(self.lib, "library") + scanner.scan_path(self.lib2, "library")
        dl = scanner.scan_path(self.dl, "download")
        _state["library"], _state["downloads"], _state["transcoded"] = lib, dl, []
        _state["status"] = scanner._compute_status(lib, dl, [])

    def listing(self) -> List[str]:
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*"))

    def has(self, rel: str) -> bool:
        return (self.tmp / rel).exists()


def by_id(results: List[Dict]) -> Dict[str, Dict]:
    return {r["id"]: r for r in results}


def run(t: Tree) -> None:
    s = os.sep
    lib, lib2, dl = str(t.lib), str(t.lib2), str(t.dl)
    t.mk("lib/James Bond/GoldenEye (1995)/GoldenEye (1995).mkv")
    t.mk("lib/James Bond/Skyfall (2012)/Skyfall (2012).mkv")
    t.mk("lib/James Bond/Dune (2021)/Dune (2021).mkv")
    t.mk("lib/James Bond/Thunderball.1965.mkv")
    t.mk("lib/Rocky (1976)/Rocky (1976).mkv")
    t.mk("lib/Solo/Collateral (2004)/Collateral (2004).mkv")
    t.mk("lib/Mann/Thief (1981)/Thief (1981).mkv")
    t.mk("lib/Mann/folder.jpg")
    t.mk("lib2/James Bond/Dr. No (1962)/Dr. No (1962).mkv")
    t.mk("lib2/James Bond/GoldenEye (1995)/GoldenEye (1995).mkv")
    t.mk("dl/No.Time.To.Die.2021.mkv")
    t.mk("dl/No.Time.To.Die.2021.nfo", "<movie><title>No Time to Die</title><year>2021</year></movie>")
    t.mk("dl/No.Time.To.Die.2021-poster.jpg")
    t.mk("dl/No.Time.To.Die.2021.trickplay/320 - 10x10/0.jpg")
    t.mk("dl/Skyfall.2012.mkv")
    t.mk("dl/Skyfall.2012.nfo", "<movie><title>Skyfall</title><year>2012</year></movie>")
    t.mk("dl/Alien.1979.mkv")
    t.mk("dl/Aliens.1986.mkv")
    t.mk("dl/Aliens.1986.en.srt")
    t.mk("dl/Dune.2021.mkv")
    t.mk("dl/Untitled.mkv")
    t.mk("dl/Show/Show S01E01.mkv")
    t.mk("dl/Heat (1995)/Heat (1995) - CD1.mkv")
    t.mk("dl/Heat (1995)/Heat (1995) - CD2.mkv")
    t.rescan()
    bond, bond2 = os.path.join(lib, "James Bond"), os.path.join(lib2, "James Bond")
    nttd, sky_copy = os.path.join(dl, "No.Time.To.Die.2021.mkv"), os.path.join(dl, "Skyfall.2012.mkv")
    golden = os.path.join(bond, "GoldenEye (1995)")

    # ── Guards on the target ──────────────────────────────────────────────────────
    refused("new bundle only in a Library Location", lambda: fileops.bundle_move_items([nttd], None, dl, "X", True))
    refused("new bundle: invalid name refused", lambda: fileops.bundle_move_items([nttd], None, lib, "a\\b", True))
    refused("new bundle: existing name refused", lambda: fileops.bundle_move_items([nttd], None, lib, "James Bond", True))
    refused("existing bundle must be directly in a Library Location",
            lambda: fileops.bundle_move_items([nttd], golden, None, None, True))
    refused("a Library Location itself is no bundle", lambda: fileops.bundle_move_items([nttd], lib, None, None, True))
    _state["scanning"] = True
    refused("409 while scanning", lambda: fileops.bundle_move_items([nttd], bond, None, None, True), 409)
    _state["scanning"] = False

    # ── Add to bundle: dry run, then real ─────────────────────────────────────────
    ids = [nttd, sky_copy, golden, os.path.join(dl, "Dune.2021.mkv"), os.path.join(dl, "Untitled.mkv"),
           os.path.join(dl, "Show")]
    before = t.listing()
    dry = by_id(fileops.bundle_move_items(ids, bond, None, None, True))
    check("dry run touches nothing", t.listing() == before)
    check("dry run: loose file gets its own folder, sidecars and trickplay listed",
          dry[nttd]["ok"] and dry[nttd]["folder"] == os.path.join(bond, "No Time to Die (2021)")
          and sorted(os.path.basename(a) for a in dry[nttd]["also"])
          == ["No.Time.To.Die.2021-poster.jpg", "No.Time.To.Die.2021.nfo", "No.Time.To.Die.2021.trickplay"])
    check("copy of a bundle member skipped", not dry[sky_copy]["ok"] and dry[sky_copy].get("skipped")
          and "copy" in dry[sky_copy]["error"])
    check("movie already in the bundle skipped", not dry[golden]["ok"] and "Already" in dry[golden]["error"])
    check("existing destination refused (no (1))",
          not dry[os.path.join(dl, "Dune.2021.mkv")]["ok"] and "exists" in dry[os.path.join(dl, "Dune.2021.mkv")]["error"])
    check("loose file without a year refused", "Reorganize" in dry[os.path.join(dl, "Untitled.mkv")]["error"])
    check("series refused", not dry[os.path.join(dl, "Show")]["ok"])

    duplicates._save_not_duplicates([sorted([nttd, os.path.join(lib, "Rocky (1976)")])])
    real = by_id(fileops.bundle_move_items([nttd], bond, None, None, False))
    check("real run: did what the dry run said",
          real[nttd]["ok"] and real[nttd]["to"] == dry[nttd]["to"] and len(real[nttd]["also"]) == 3)
    check("loose file foldered in the bundle with its sidecars and trickplay",
          t.has(f"lib{s}James Bond{s}No Time to Die (2021){s}No.Time.To.Die.2021.mkv")
          and t.has(f"lib{s}James Bond{s}No Time to Die (2021){s}No.Time.To.Die.2021.nfo")
          and t.has(f"lib{s}James Bond{s}No Time to Die (2021){s}No.Time.To.Die.2021.trickplay{s}320 - 10x10{s}0.jpg")
          and not t.has(f"dl{s}No.Time.To.Die.2021.mkv"))
    check("not-a-duplicate mark follows to the new movie folder",
          duplicates._load_not_duplicates()[0] == sorted([os.path.join(bond, "No Time to Die (2021)"),
                                                          os.path.join(lib, "Rocky (1976)")]))

    # ── Create bundle ─────────────────────────────────────────────────────────────
    alien, aliens = os.path.join(dl, "Alien.1979.mkv"), os.path.join(dl, "Aliens.1986.mkv")
    bundles._save_not_related([{"path": os.path.join(dl, "Other.mkv"), "key": "title:alien"}])
    before = t.listing()
    dry = fileops.bundle_move_items([alien, aliens], None, lib, "Alien", True, "title:alien")
    check("create dry run touches nothing and plans the new folder",
          t.listing() == before and all(r["ok"] and r["created"] == os.path.join(lib, "Alien") for r in dry))
    real = fileops.bundle_move_items([alien, aliens], None, lib, "Alien", False, "title:alien")
    check("bundle created; Alien and Aliens kept apart",
          all(r["ok"] for r in real)
          and sorted(os.listdir(os.path.join(lib, "Alien"))) == ["Alien (1979)", "Aliens (1986)"]
          and sorted(os.listdir(os.path.join(lib, "Alien", "Alien (1979)"))) == ["Alien.1979.mkv"]
          and sorted(os.listdir(os.path.join(lib, "Alien", "Aliens (1986)"))) == ["Aliens.1986.en.srt", "Aliens.1986.mkv"])
    check("proposal records now name the new bundle",
          bundles.load_not_related()[0]["key"] == "bundle:" + os.path.join(lib, "Alien"))

    # ── Parts of one movie share their new folder ─────────────────────────────────
    cd1, cd2 = os.path.join(dl, "Heat (1995)", "Heat (1995) - CD1.mkv"), os.path.join(dl, "Heat (1995)", "Heat (1995) - CD2.mkv")
    real = fileops.bundle_move_items([cd1, cd2], os.path.join(lib, "Alien"), None, None, False)
    check("CD1/CD2 into one Heat (1995) folder",
          all(r["ok"] for r in real)
          and sorted(os.listdir(os.path.join(lib, "Alien", "Heat (1995)"))) == ["Heat (1995) - CD1.mkv", "Heat (1995) - CD2.mkv"])

    # ── Merge bundles ─────────────────────────────────────────────────────────────
    t.rescan()
    gold2 = os.path.join(bond2, "GoldenEye (1995)")
    duplicates._save_not_duplicates([sorted([golden, gold2])])
    t.rescan()
    refused("merge within one Library Location refused",
            lambda: fileops.bundle_merge(bond, os.path.join(lib, "Alien"), None, True))
    before = t.listing()
    dry = by_id(fileops.bundle_merge(bond2, bond, None, True))
    check("merge dry run touches nothing", t.listing() == before)
    check("merge: a folder already in the target is refused",
          not dry[gold2]["ok"] and "exists" in dry[gold2]["error"] and dry[os.path.join(bond2, "Dr. No (1962)")]["ok"])
    real = by_id(fileops.bundle_merge(bond2, bond, None, False))
    check("merge moved the other movie; source kept while it holds one",
          real[os.path.join(bond2, "Dr. No (1962)")]["ok"] and t.has(f"lib{s}James Bond{s}Dr. No (1962)")
          and t.has(f"lib2{s}James Bond{s}GoldenEye (1995)"))
    check("merge: an item not in the source refused",
          not fileops.bundle_merge(bond2, bond, [golden], True)[0]["ok"])

    # ── Out of bundle ─────────────────────────────────────────────────────────────
    t.rescan()
    thunder = os.path.join(bond, "Thunderball.1965.mkv")
    collateral = os.path.join(lib, "Solo", "Collateral (2004)")
    thief = os.path.join(lib, "Mann", "Thief (1981)")
    rocky = os.path.join(lib, "Rocky (1976)")
    before = t.listing()
    dry = by_id(fileops.bundle_out_items([golden, thunder, collateral, thief, rocky], True))
    check("out dry run touches nothing", t.listing() == before)
    check("out: movie not in a bundle refused", not dry[rocky]["ok"] and "Not in a bundle" in dry[rocky]["error"])
    real = by_id(fileops.bundle_out_items([golden, thunder, collateral, thief], False))
    check("out: movie folder to the Library Location root", real[golden]["ok"] and t.has(f"lib{s}GoldenEye (1995)"))
    check("out: loose file gets its own folder at the root",
          real[thunder]["ok"] and t.has(f"lib{s}Thunderball (1965){s}Thunderball.1965.mkv"))
    check("emptied bundle removed with rmdir",
          not t.has(f"lib{s}Solo") and real[collateral]["removed_folders"] == [os.path.join(lib, "Solo")])
    check("bundle with Jellyfin data left stays", t.has(f"lib{s}Mann{s}folder.jpg") and t.has(f"lib{s}Thief (1981)"))

    # ── Rename ────────────────────────────────────────────────────────────────────
    t.rescan()
    bundles._save_not_related([{"path": os.path.join(dl, "Dune.2021.mkv"), "key": "bundle:" + bond}])
    duplicates._save_not_duplicates([sorted([os.path.join(bond, "Skyfall (2012)"), sky_copy])])
    refused("rename onto an existing folder refused", lambda: fileops.bundle_rename(bond, "Alien", True))
    refused("rename to an invalid name refused", lambda: fileops.bundle_rename(bond, "Bond:", True))
    before = t.listing()
    dry = fileops.bundle_rename(bond, "james bond", True)
    check("rename dry run touches nothing", t.listing() == before and dry["to"] == os.path.join(lib, "james bond"))
    check("case-only rename allowed", fileops.bundle_rename(bond, "james bond", False)["ok"]
          and "james bond" in os.listdir(lib))
    new = os.path.join(lib, "007")
    fileops.bundle_rename(os.path.join(lib, "james bond"), "007", False)
    check("renamed on disk", t.has(f"lib{s}007{s}Skyfall (2012)") and not t.has(f"lib{s}james bond"))
    check("not-related records follow the rename", bundles.load_not_related()[0]["key"] == "bundle:" + new)
    check("not-a-duplicate marks follow the rename",
          duplicates._load_not_duplicates()[0] == sorted([os.path.join(new, "Skyfall (2012)"), sky_copy]))


def main() -> int:
    t = Tree()
    saved = {k: _state.get(k) for k in ("library", "downloads", "transcoded", "status", "scanning", "transcode")}
    saved_paths = duplicates.NOT_DUP_PATH, bundles.NOT_RELATED_PATH, fileops.load_config
    duplicates.NOT_DUP_PATH = t.tmp / "not-duplicates.json"
    bundles.NOT_RELATED_PATH = t.tmp / "not-related.json"
    fileops.load_config = lambda: t.config
    _state["transcode"] = None
    try:
        run(t)
    finally:
        _state.update(saved)
        duplicates.NOT_DUP_PATH, bundles.NOT_RELATED_PATH, fileops.load_config = saved_paths
        shutil.rmtree(t.tmp, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
