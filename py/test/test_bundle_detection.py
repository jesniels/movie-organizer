"""Bundle detection (py/ui/bundles.py): title normalisation and find_bundle_proposals — pure, no files.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_bundle_detection.py
Exit code 0 = all checks passed.
"""
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from py.ui import bundles  # noqa: E402
from py.ui.duplicates import _find_duplicates  # noqa: E402

M, N, D = "M:\\Movies", "N:\\Movies", "D:\\Downloaded"
LOCATIONS = [M, N]

failures = 0


def check(label: str, cond: bool) -> None:
    """Print one check result and count failures."""
    global failures
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures += 1


def movie(rel: str, base: str = M, title: Optional[str] = None, year: str = "", set_name: str = "",
          set_id: str = "", loose: bool = False) -> Dict:
    """A movie item as the scanner builds it; `rel` is the folder (or the file, when loose) below `base`."""
    path = os.path.join(base, rel)
    nfo = {"title": title, "year": year, "set": set_name, "set_id": set_id} if title else {}
    name = os.path.basename(path)
    shown = title or os.path.splitext(name)[0]
    loc = "download" if base == D else "library"
    parent = os.path.dirname(path)
    bundle = parent if loc == "library" and os.path.dirname(parent) == base else ""
    if loose:
        return {"id": path, "path": parent, "folder": os.path.basename(parent), "files": [path], "type": "movie",
                "location": loc, "title": shown, "year": year, "nfo": nfo, "bundle": bundle}
    return {"id": path, "path": path, "folder": name, "files": [os.path.join(path, name + ".mkv")], "type": "movie",
            "location": loc, "title": shown, "year": year, "nfo": nfo, "bundle": bundle}


def detect(items: List[Dict], not_related: Optional[List[Dict]] = None) -> Dict:
    """find_bundle_proposals with the duplicate groups the Duplicates filter would show."""
    return bundles.find_bundle_proposals(items, _find_duplicates(items), not_related or [], LOCATIONS)


def proposals(items: List[Dict], not_related: Optional[List[Dict]] = None) -> List[Dict]:
    return detect(items, not_related)["proposals"]


def names(p: Dict) -> List[str]:
    """The proposed items' last path component, sorted."""
    return sorted(os.path.basename(i) for i in p["items"])


def test_normalisation() -> None:
    table = [
        ("Back to the Future Part II", True, "Back to the Future", True),
        ("Alien: Covenant", True, "Alien", True),
        ("Alien - Covenant", False, "Alien", True),
        ("Rocky IV", True, "Rocky", True),
        ("Blade Runner 2049", True, "Blade Runner", True),
        ("Kill Bill: Vol. 1", True, "Kill Bill", True),
        ("Kill Bill Vol. 2", True, "Kill Bill", True),
        ("Dune: Part Two", True, "Dune", True),
        ("The Godfather Part III", True, "The Godfather", True),
        ("Harry Potter Chapter Seven", True, "Harry Potter", True),
        ("Toy Story", True, "Toy Story", False),
        ("1917", True, "1917", False),
        ("9", True, "9", False),
        ("Malcolm X", True, "Malcolm", True),
        ("Rocky I", True, "Rocky I", False),
    ]
    for title, from_nfo, base, marker in table:
        check(f"split {title!r} → {base!r}, marker={marker}", bundles.split_marker(title, from_nfo) == (base, marker))

    info = lambda **kw: bundles.title_info(movie("x", **kw))
    check("NFO title wins; year and quality tags stripped",
          info(title="Alien (1979) 1080p") == ("Alien", "alien", False))
    check("folder name parsed when there is no NFO title",
          bundles.title_info(movie("Alien - Covenant (2017)")) == ("Alien", "alien", True))
    check("scene name parsed", bundles.title_info(movie("Blade.Runner.2049.2017.1080p.mkv", base=D, loose=True))[1:]
          == ("blade runner", True))
    check("1917 is not grouped by title (only digits)", info(title="1917")[1] == "")
    check("base shorter than 3 characters is not grouped", info(title="Up 2")[1] == "")
    check("compare form: & → and, apostrophes removed",
          info(title="Fast & Furious")[1] == "fast and furious" and info(title="Ocean’s Eleven")[1] == "oceans eleven")
    check("leading article kept", info(title="The Matrix")[1] == "the matrix")


def test_new_bundles() -> None:
    bond = [movie("Dr. No (1962)", base=D, title="Dr. No", year="1962", set_name="James Bond Collection", set_id="645"),
            movie("Skyfall (2012)", title="Skyfall", year="2012", set_name="James Bond Collection", set_id="645")]
    p = proposals(bond)
    check("same collection, unrelated titles → one new proposal named after the collection",
          len(p) == 1 and p[0]["type"] == "new" and p[0]["name"] == "James Bond" and p[0]["key"] == "collection:645"
          and p[0]["reason"] == "Same collection: James Bond Collection")
    check("location pre-selected: the Library Location holding a member", p[0]["location"] == M)
    p = proposals([movie("A (2000)", base=D, title="A Film", set_name="X Collection", set_id="7"),
                   movie("B (2001)", base=D, title="B Film", set_name="Another name", set_id="7")])
    check("same collectionnumber, different names → grouped", len(p) == 1 and p[0]["key"] == "collection:7")
    check("all in Downloads → first Library Location", p[0]["location"] == M)
    p = proposals([movie("A (2000)", base=D, title="A Film", set_name="Saga"),
                   movie("B (2001)", title="B Film", set_name="saga collection")])
    check("collection names without ID compared case-insensitively, ' Collection' ignored",
          len(p) == 1 and p[0]["key"] == "collection:saga")

    rocky = [movie("Rocky (1976)", title="Rocky", year="1976"), movie("Rocky II (1979)", base=D, title="Rocky II")]
    p = proposals(rocky)
    check("original + sequel → proposal from similar titles",
          len(p) == 1 and p[0]["key"] == "title:rocky" and p[0]["name"] == "Rocky" and p[0]["reason"] == "Similar titles")
    check("movies from different locations are grouped", names(p[0]) == ["Rocky (1976)", "Rocky II (1979)"])
    check("sequels without the original → nothing",
          proposals([movie("Toy Story 2 (1999)", title="Toy Story 2"), movie("Toy Story 3 (2010)", title="Toy Story 3")]) == [])
    check("Apollo 13 + Apollo 18 → nothing",
          proposals([movie("Apollo 13 (1995)", title="Apollo 13"), movie("Apollo 18 (2011)", title="Apollo 18")]) == [])
    check("colon subtitle counts as a marker",
          len(proposals([movie("Alien (1979)", title="Alien"), movie("Alien Covenant (2017)", title="Alien: Covenant")])) == 1)
    check("folder names: ' - ' subtitle counts as a marker",
          len(proposals([movie("Alien (1979)"), movie("Alien - Covenant (2017)", base=D)])) == 1)
    check("Roman numeral sequel", len(proposals([movie("Rocky (1976)"), movie("Rocky IV (1985)")])) == 1)
    check("1917 alone → nothing", proposals([movie("1917 (2019)", title="1917")]) == [])
    check("single movie → nothing", proposals([movie("Heat (1995)", title="Heat")]) == [])
    check("single-movie collection → nothing",
          proposals([movie("Heat (1995)", title="Heat", set_name="Heat Collection", set_id="9")]) == [])
    check("two copies of one movie → nothing",
          proposals([movie("Heat (1995)", title="Heat", set_name="Heat Collection", set_id="9"),
                     movie("Heat (1995)", base=D, title="Heat", set_name="Heat Collection", set_id="9")]) == [])
    check("copies count once: original + its copy + nothing else → nothing",
          proposals([movie("Rocky (1976)", title="Rocky"), movie("Rocky (1976)", base=D, title="Rocky")]) == [])
    check("remakes without a marker → nothing",
          proposals([movie("The Thing (1982)", title="The Thing", year="1982"),
                     movie("The Thing (2011)", base=D, title="The Thing", year="2011")]) == [])
    check("different collections with similar titles → not grouped",
          proposals([movie("Scream (1996)", title="Scream", set_name="Scream Collection", set_id="1"),
                     movie("Scream 2 (2015)", title="Scream 2", set_name="Other Collection", set_id="2")]) == [])
    parts = [movie("Heat (1995)\\Heat - CD1.mkv", base=D, loose=True),
             movie("Heat (1995)\\Heat - CD2.mkv", base=D, loose=True)]
    check("parts of one movie (CD1/CD2) are one movie", proposals(parts) == [])
    p = proposals(parts + [movie("Heat 2 (2026)", title="Heat 2")])
    check("the parts of a movie are proposed together", len(p) == 1 and len(p[0]["movies"]) == 2
          and any(len(m["ids"]) == 2 for m in p[0]["movies"]))

    raw, keys = bundles._collection_keys([
        movie("A", title="A", set_name="Batman Collection", set_id="1"),
        movie("B", title="B", set_name="Batman Collection", set_id="2"),
        movie("C", title="C", set_name="Batman Collection"),
        movie("D", title="D", set_name="Bond", set_id="645"),
        movie("E", title="E", set_name="Bond")])
    check("name used with two IDs is ambiguous → no collection key", keys[os.path.join(M, "C")] is None)
    check("name without ID joins the one ID it is used with", keys[os.path.join(M, "E")] == "collection:645")


def test_existing_bundles() -> None:
    items = [movie("James Bond\\GoldenEye (1995)", title="GoldenEye", set_name="James Bond Collection", set_id="645"),
             movie("James Bond\\Skyfall (2012)", title="Skyfall", set_name="James Bond Collection", set_id="645"),
             movie("No Time to Die (2021)", base=D, title="No Time to Die", set_name="James Bond Collection", set_id="645")]
    result = detect(items)
    p = result["proposals"]
    check("movie with a member's collection → add to bundle",
          len(p) == 1 and p[0]["type"] == "add" and p[0]["bundle"] == os.path.join(M, "James Bond")
          and names(p[0]) == ["No Time to Die (2021)"] and len(p[0]["context"]) == 2)
    b = result["bundles"]
    check("existing bundles listed with location and movie count",
          len(b) == 1 and b[0]["name"] == "James Bond" and b[0]["location"] == M and b[0]["count"] == 2
          and not b[0]["nested"])
    check("copy of a bundled movie is never proposed",
          proposals(items[:2] + [movie("Skyfall (2012)", base=D, title="Skyfall", set_name="James Bond Collection",
                                       set_id="645")]) == [])
    p = proposals([movie("Rocky Films\\Rocky (1976)", title="Rocky"), movie("Rocky II (1979)", base=N, title="Rocky II"),
                   movie("Other\\Rocky III (1982)", title="Rocky III")])
    check("movie in a bundle is not proposed elsewhere",
          len(p) == 1 and p[0]["type"] == "add" and names(p[0]) == ["Rocky II (1979)"])

    p = proposals([movie("Back to the Future\\Back to the Future Part II (1989)", title="Back to the Future Part II"),
                   movie("BTTF3 (1990)", base=D, title="Back to the Future Part III")])
    check("base title equal to the bundle folder name → add", len(p) == 1 and p[0]["type"] == "add")
    p = proposals([movie("Boxing\\Rocky (1976)", title="Rocky"), movie("Rocky II (1979)", base=D, title="Rocky II"),
                   movie("Rocky III (1982)", base=D, title="Rocky III")])
    check("base title equal to a member's whole title → add (before new)",
          len(p) == 1 and p[0]["type"] == "add" and names(p[0]) == ["Rocky II (1979)", "Rocky III (1982)"])
    check("a member's sequel alone is not enough",
          proposals([movie("Pixar\\Toy Story 2 (1999)", title="Toy Story 2"),
                     movie("Toy Story 3 (2010)", base=D, title="Toy Story 3")]) == [])
    check("nested movie folder hint",
          detect([movie("Heat (1995)\\Heat (1995)", title="Heat")])["bundles"][0]["nested"])

    two = [movie("James Bond\\GoldenEye (1995)", title="GoldenEye", set_name="James Bond Collection", set_id="645"),
           movie("James Bond\\Skyfall (2012)", title="Skyfall", set_name="James Bond Collection", set_id="645"),
           movie("James Bond\\Dr. No (1962)", base=N, title="Dr. No", set_name="James Bond Collection", set_id="645")]
    p = proposals(two)
    check("same-name bundles in two Library Locations → merge proposal",
          len(p) == 1 and p[0]["type"] == "merge" and p[0]["target"] == os.path.join(M, "James Bond")
          and p[0]["source"] == os.path.join(N, "James Bond") and p[0]["reason"] == "Same name")
    mark = [{"path": os.path.join(N, "James Bond"), "key": "bundle:" + os.path.join(M, "James Bond")}]
    check("merge marked not related → gone", proposals(two, mark) == [])
    mark = [{"path": os.path.join(M, "James Bond"), "key": "bundle:" + os.path.join(N, "James Bond")}]
    check("merge marks are checked both ways", proposals(two, mark) == [])

    nm = movie("No Time to Die (2021)", base=N, title="No Time to Die", set_name="James Bond Collection", set_id="645")
    p = [x for x in proposals(two + [nm]) if x["type"] == "add"]
    check("several matching bundles: the one in the movie's own Library Location",
          len(p) == 1 and p[0]["bundle"] == os.path.join(N, "James Bond"))
    dl = movie("No Time to Die (2021)", base=D, title="No Time to Die", set_name="James Bond Collection", set_id="645")
    p = [x for x in proposals(two + [dl]) if x["type"] == "add"]
    check("from Downloads: the first bundle by config order", len(p) == 1 and p[0]["bundle"] == os.path.join(M, "James Bond"))
    mark = [{"path": dl["id"], "key": "bundle:" + os.path.join(M, "James Bond")}]
    p = [x for x in proposals(two + [dl], mark) if x["type"] == "add"]
    check("marked for the winning bundle → next-best bundle", len(p) == 1 and p[0]["bundle"] == os.path.join(N, "James Bond"))

    both = [movie("Rocky\\Rocky (1976)", title="Rocky"),
            movie("Stallone\\Cop Land (1997)", title="Cop Land", set_name="Stallone Collection", set_id="5"),
            movie("Rocky Balboa (2006)", base=D, title="Rocky: Balboa", set_name="Stallone Collection", set_id="5")]
    p = proposals(both)
    check("same collection before similar titles",
          len(p) == 1 and p[0]["bundle"] == os.path.join(M, "Stallone") and p[0]["reason"].startswith("Same collection"))


def test_not_related() -> None:
    rocky = [movie("Rocky (1976)", title="Rocky"), movie("Rocky II (1979)", base=D, title="Rocky II"),
             movie("Rocky III (1982)", base=D, title="Rocky III")]
    mark = [{"path": rocky[1]["id"], "key": "title:rocky"}]
    p = proposals(rocky, mark)
    check("movie marked Outside drops out, the proposal stays", len(p) == 1 and len(p[0]["items"]) == 2)
    mark.append({"path": rocky[2]["id"], "key": "title:rocky"})
    check("proposal dropped below two unmarked movies", proposals(rocky, mark) == [])
    mark = [{"path": rocky[0]["id"].upper(), "key": "title:rocky"}]
    check("original marked → no title group left (it never counts)", proposals(rocky, mark) == [])
    bond = [movie("Dr. No (1962)", title="Dr. No", set_name="James Bond Collection", set_id="645"),
            movie("Skyfall (2012)", title="Skyfall", set_name="James Bond Collection", set_id="645")]
    check("collection proposal respects its key",
          proposals(bond, [{"path": bond[0]["id"], "key": "collection:645"}]) == [])


def main() -> int:
    sys.stdout.reconfigure(errors="replace")   # labels use → (a cp1252 console cannot print it)
    test_normalisation()
    test_new_bundles()
    test_existing_bundles()
    test_not_related()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
