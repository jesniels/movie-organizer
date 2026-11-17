"""The not-related registry (py/ui/bundles.py): load/save, mark, remove, following path changes, not found.

Uses a temporary folder inside py/test (removed afterwards) for not-related.json and the
"found" checks — the real web/not-related.json is never touched.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_not_related.py
Exit code 0 = all checks passed.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from py.ui import bundles  # noqa: E402
from py.ui.state import _state  # noqa: E402

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def run(tmp: Path) -> None:
    lib = tmp / "Movies"
    bond = lib / "James Bond"
    (bond / "Skyfall (2012)").mkdir(parents=True)
    heat = tmp / "dl" / "Heat (1995).mkv"
    heat.parent.mkdir()
    heat.write_text("x", encoding="utf-8")

    check("no file -> no records", bundles.load_not_related() == [])
    r_heat = {"path": str(heat), "key": "bundle:" + str(bond)}
    r_rocky = {"path": str(lib / "Rocky II (1979)"), "key": "title:rocky"}
    r_merge = {"path": str(tmp / "N" / "James Bond"), "key": "bundle:" + str(bond)}
    res = bundles.mark_not_related([r_heat, r_rocky, r_merge])
    check("records added", res["added"] == 3 and len(bundles.load_not_related()) == 3)
    res = bundles.mark_not_related([{"path": str(heat).upper(), "key": "bundle:" + str(bond).lower()}])
    check("same record again (other case) is not added twice", res["added"] == 0)
    check("file is a JSON list of {path, key}",
          json.loads(bundles.NOT_RELATED_PATH.read_text(encoding="utf-8"))[0] == r_heat)
    try:
        bundles.mark_not_related([{"path": str(heat), "key": "other:x"}])
        check("unknown key prefix refused", False)
    except HTTPException as e:
        check("unknown key prefix refused", e.status_code == 400)

    found = {r["path"]: r["found"] for r in bundles.list_not_related()}
    check("record with existing movie and bundle is found", found[str(heat)])
    check("record whose movie does not exist is not found", not found[str(lib / "Rocky II (1979)")])
    check("merge record whose bundle folder does not exist is not found", not found[str(tmp / "N" / "James Bond")])

    moved = tmp / "dl2" / "Heat (1995).mkv"
    bundles.update_not_related_paths(str(heat), str(moved))
    recs = bundles.load_not_related()
    check("record follows a move of the movie", recs[0]["path"] == str(moved) and recs[0]["key"] == r_heat["key"])

    renamed = lib / "007"
    bundles.update_not_related_paths(str(bond), str(renamed))
    recs = bundles.load_not_related()
    check("bundle keys follow a bundle rename",
          recs[0]["key"] == "bundle:" + str(renamed) and recs[2]["key"] == "bundle:" + str(renamed))
    check("other records unchanged by the rename", recs[1] == r_rocky)
    bundles.mark_not_related([{"path": str(lib / "James Bond2" / "X (2000)"), "key": "title:x"}])
    bundles.update_not_related_paths(str(lib / "James Bond"), str(lib / "Bond"))
    check("a prefix that is only part of a folder name is not rebased",
          bundles.load_not_related()[3]["path"] == str(lib / "James Bond2" / "X (2000)"))
    bundles.update_not_related_paths(str(lib / "James Bond2"), str(lib / "JB"))
    check("a movie inside a renamed bundle follows",
          bundles.load_not_related()[3]["path"] == str(lib / "JB" / "X (2000)"))

    bundles.convert_proposal_records("title:rocky", str(lib / "Rocky"))
    check("Create bundle converts the proposal's records to the new bundle",
          bundles.load_not_related()[1]["key"] == "bundle:" + str(lib / "Rocky"))

    res = bundles.remove_not_related([{"path": str(moved), "key": "bundle:" + str(renamed)}])
    check("record removed", res["removed"] == 1 and res["remaining"] == 3)
    res = bundles.remove_not_related(bundles.load_not_related())
    check("remove all", res["remaining"] == 0 and bundles.load_not_related() == [])

    bundles.NOT_RELATED_PATH.write_text("not json", encoding="utf-8")
    check("broken file counts as no records", bundles.load_not_related() == [])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="_tmp_notrel_", dir=Path(__file__).resolve().parent))
    saved_path, saved_cfg = bundles.NOT_RELATED_PATH, bundles.load_config
    saved_state = {k: _state.get(k) for k in ("library", "downloads", "transcoded", "bundles")}
    bundles.NOT_RELATED_PATH = tmp / "not-related.json"
    bundles.load_config = lambda: {"locations": [], "disabled_paths": []}
    _state["library"], _state["downloads"], _state["transcoded"] = [], [], []
    try:
        run(tmp)
    finally:
        bundles.NOT_RELATED_PATH, bundles.load_config = saved_path, saved_cfg
        _state.update(saved_state)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
