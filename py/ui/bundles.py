"""Bundles: which movies belong together, and the user's "not related" answers.

Detection (``find_bundle_proposals``) only reads the in-memory items — it never touches the disk.
The not-related registry lives in ``web/not-related.json``. Bundle moves are in fileops.
Design: docs/organization/bundles.md.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import HTTPException

from .reorganize import _root_of, _title_year, parse_name
from .settings import NOT_RELATED_PATH, QUALITY_STRIP, enabled_paths, load_config, log, sanitize_component
from .state import _lock, _state, all_items

# Jellyfin stacking: the parts of one movie (`Heat - CD1.mkv`, `Heat.part2.mkv`) are one movie
_PART_SUFFIX = re.compile(r"[\s._\-\[(]*(?:cd|dvd|part|pt|disc|disk)[\s._\-]*\d+[\])]?$", re.IGNORECASE)

_ROMAN = ["II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV",
          "XVI", "XVII", "XVIII", "XIX", "XX"]
_NUMBER_WORDS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
_NUMBER = rf"(?:\d+|I|{'|'.join(_ROMAN)}|{'|'.join(_NUMBER_WORDS)})"
_PART_MARKER = re.compile(
    rf"^(?P<base>.*?\w.*?)[\s,.:\-]+(?:part|pt\.?|chapter|chap\.|vol\.?|volume)\s*{_NUMBER}$", re.IGNORECASE)
_TRAILING_NUMBER = re.compile(r"^(?P<base>.*?\w.*?)\s+\d{1,4}$")
_YEAR_ANYWHERE = re.compile(r"\s*[(\[]\s*\d{4}\s*[)\]]")
_KEY_PREFIXES = ("bundle:", "collection:", "title:")


def _nk(path: str) -> str:
    """Comparison form of a path (case and separators as Windows compares them)."""
    return os.path.normcase(os.path.normpath(path))


# ── Movie identity and titles ──────────────────────────────────────────────────

def strip_part(title: str) -> str:
    """A title without a trailing part marker (``Heat - CD1`` → ``Heat``)."""
    return _PART_SUFFIX.sub("", title.strip()).strip()


def movie_key(item: Dict[str, Any]) -> Tuple[str, str]:
    """(title, year) identifying one movie: NFO values first, else the parsed name, without a part marker.

    Two loose files with the same key in one folder are parts or versions of one movie (rule 4).
    """
    title, year, _ = _title_year(item, _names(item))
    return re.sub(r"\s+", " ", strip_part(title)).lower(), year


def _names(item: Dict[str, Any]) -> List[str]:
    """Names to parse a title from: the video stem of a loose file; the folder, then the first video, of a folder."""
    if item["id"] != item["path"]:
        return [os.path.splitext(os.path.basename(item["id"]))[0]]
    files = item.get("files") or []
    return [item.get("folder") or os.path.basename(item["path"])] + (
        [os.path.splitext(os.path.basename(files[0]))[0]] if files else [])


def _compare_form(text: str) -> str:
    """Case-insensitive comparison form: '&' → 'and', apostrophes removed, other punctuation → space."""
    s = text.lower().replace("&", " and ")
    s = re.sub(r"['’`´]", "", s)
    return re.sub(r"[\W_]+", " ", s).strip()


def split_marker(title: str, from_nfo: bool) -> Tuple[str, bool]:
    """Split one sequel marker off the end of a title: (base title, marker found).

    Tried in order: a subtitle (after the first ': ' in NFO titles, ' - ' in names), a part word +
    number, a trailing 1–4 digit number, a trailing Roman numeral II–XX. At least one word must stay.
    """
    t = re.sub(r"\s+", " ", title).strip()
    i = t.find(": " if from_nfo else " - ")
    if i > 0 and re.search(r"\w", t[:i]):
        return t[:i].strip(), True
    m = _PART_MARKER.match(t) or _TRAILING_NUMBER.match(t)
    if m:
        return m.group("base").strip(" ,.:-"), True
    words = t.split(" ")
    if len(words) > 1 and words[-1] in _ROMAN:
        return " ".join(words[:-1]), True
    return t, False


def title_info(item: Dict[str, Any], parts: bool = False) -> Tuple[str, str, bool]:
    """(display base, comparison base, has marker) of an item's title (bundles.md → Title normalisation).

    ``parts``: the item is one part of a multi-part movie — its ``CD1``/``Part 1`` marker is no sequel.
    The comparison base is "" when it is shorter than 3 characters or only digits: such a movie is
    not grouped by title.
    """
    nfo_title = ((item.get("nfo") or {}).get("title") or "").strip()
    if nfo_title:
        title = QUALITY_STRIP.sub("", _YEAR_ANYWHERE.sub(" ", nfo_title))
    else:
        title = next((t for t in (parse_name(n)[0] for n in _names(item)) if t), "")
    if parts:
        title = strip_part(title)
    base, marker = split_marker(title, bool(nfo_title))
    cmp = _compare_form(base)
    if len(cmp) < 3 or cmp.replace(" ", "").isdigit():
        cmp = ""
    return base, cmp, marker


def _collection_norm(name: str) -> str:
    """Comparison form of a collection name, a trailing ' Collection' ignored."""
    return re.sub(r"\s+collection$", "", _compare_form(name))


def collection_display(name: str) -> str:
    """A collection name as a bundle name: without a trailing ' Collection'."""
    return re.sub(r"\s+collection\s*$", "", name.strip(), flags=re.IGNORECASE).strip()


# ── Detection ──────────────────────────────────────────────────────────────────

@dataclass
class _Movie:
    """One movie for detection: copies (one duplicate group) and the parts of one movie together."""
    ids: List[str]                        # the items a proposal moves (one copy, with all its parts)
    all_ids: List[str]                    # every copy and part
    location: str                         # 'library' or 'download' of the proposed copy
    root: Optional[str]                   # Library Location of the proposed copy
    ckey: Optional[str] = None            # collection group key
    cid: str = ""                         # collection ID (for rule 3)
    cname: str = ""                       # collection name, normalised (for rule 3)
    cdisplay: str = ""                    # collection name as written in the NFO
    display: str = ""                     # base title as written
    base: str = ""                        # base title, comparison form ("" → not grouped by title)
    marker: bool = False
    in_bundles: Dict[str, List[str]] = field(default_factory=dict)   # bundle path → its copies/parts in it


def _conflict(a: _Movie, b: _Movie) -> bool:
    """Rule 3: both name a collection and they differ (IDs compared when both have one)."""
    if not (a.cid or a.cname) or not (b.cid or b.cname):
        return False
    if a.cid and b.cid:
        return a.cid != b.cid
    return a.cname != b.cname


def _collection_keys(movies: List[Dict[str, Any]]) -> Tuple[Dict[str, Tuple[str, str, str]], Dict[str, Optional[str]]]:
    """Per item: (ID, normalised name, name as written), and its group key (rule 1).

    Key: ``collection:<id>``; without an ID the ID its name is used with, or ``collection:<name>`` when
    the name has no ID anywhere. A name used with two different IDs joins neither (None).
    """
    raw: Dict[str, Tuple[str, str, str]] = {}
    name_ids: Dict[str, Set[str]] = {}
    for i in movies:
        nfo = i.get("nfo") or {}
        cid, cdisplay = (nfo.get("set_id") or "").strip(), (nfo.get("set") or "").strip()
        cname = _collection_norm(cdisplay) if cdisplay else ""
        raw[i["id"]] = (cid, cname, cdisplay)
        if cid and cname:
            name_ids.setdefault(cname, set()).add(cid)
    keys: Dict[str, Optional[str]] = {}
    for iid, (cid, cname, _) in raw.items():
        ids = name_ids.get(cname, set())
        if cid:
            keys[iid] = "collection:" + cid
        elif cname and len(ids) <= 1:
            keys[iid] = "collection:" + (next(iter(ids)) if ids else cname)
        else:
            keys[iid] = None
    return raw, keys


def _movies(movies: List[Dict[str, Any]], duplicate_groups: List[Dict[str, Any]],
            locations: List[str]) -> List[_Movie]:
    """Group items into movies: one duplicate group, and loose parts of one movie in one folder, are one movie."""
    uf = {i["id"]: i["id"] for i in movies}

    def find(x: str) -> str:
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    for g in duplicate_groups:
        ids = [m["id"] for m in g.get("items", []) if m["id"] in uf]
        for other in ids[1:]:
            uf[find(other)] = find(ids[0])
    parts_key: Dict[str, Tuple] = {}
    first_part: Dict[Tuple, str] = {}
    for i in movies:
        pk = (_nk(i["path"]),) + movie_key(i) if i["id"] != i["path"] else (_nk(i["id"]),)
        parts_key[i["id"]] = pk
        if pk in first_part:
            uf[find(i["id"])] = find(first_part[pk])
        else:
            first_part[pk] = i["id"]

    raw, ckeys = _collection_keys(movies)
    by_id = {i["id"]: i for i in movies}
    units: Dict[str, List[str]] = {}
    for i in movies:
        units.setdefault(find(i["id"]), []).append(i["id"])
    out: List[_Movie] = []
    for ids in units.values():
        sets: Dict[Tuple, List[str]] = {}
        for iid in ids:
            sets.setdefault(parts_key[iid], []).append(iid)
        # The copy to propose: one in a Library Location first, then by path
        chosen = sorted(min(sets.values(), key=lambda s: (by_id[s[0]]["location"] != "library", s[0].lower())),
                        key=str.lower)
        rep = by_id[chosen[0]]
        display, base, marker = title_info(rep, parts=len(chosen) > 1)
        m = _Movie(ids=chosen, all_ids=ids, location=rep["location"],
                   root=_root_of(rep["path"], locations) if rep["location"] == "library" else None,
                   display=display, base=base, marker=marker)
        for x in sorted(ids, key=str.lower):
            if by_id[x].get("bundle") and by_id[x]["location"] == "library":
                m.in_bundles.setdefault(by_id[x]["bundle"], []).append(x)
        source = next((x for x in chosen + ids if ckeys[x]), None) or next((x for x in chosen + ids if raw[x][1]), None)
        if source:
            m.ckey = ckeys[source]
            m.cid, m.cname, m.cdisplay = raw[source]
        out.append(m)
    return out


def _norm_key(key: str) -> str:
    return "bundle:" + _nk(key[len("bundle:"):]) if key.startswith("bundle:") else key


def _marks(records: List[Dict[str, str]]) -> Set[Tuple[str, str]]:
    """The not-related records as (path, key) in comparison form."""
    return {(_nk(r["path"]), _norm_key(r["key"])) for r in records if r.get("path") and r.get("key")}


def _location_pick(movies: List[_Movie], locations: List[str]) -> str:
    """The Library Location holding most of the movies (tie → first in the config); else the first one."""
    counts = Counter(_nk(m.root) for m in movies if m.root)
    ranked = sorted(locations, key=lambda loc: -counts.get(_nk(loc), 0))   # stable: config order on a tie
    return ranked[0] if ranked else ""


def _proposal_movies(movies: List[_Movie], why: Optional[Dict[int, str]] = None) -> List[Dict[str, Any]]:
    """The movies of a proposal: the item ids to move together (one copy with its parts), and why."""
    return [{"ids": m.ids, **({"why": why[id(m)]} if why else {})} for m in movies]


def _new_proposal(key: str, reason: str, name: str, movies: List[_Movie], locations: List[str]) -> Dict[str, Any]:
    return {"type": "new", "key": key, "reason": reason, "name": sanitize_component(name),
            "location": _location_pick(movies, locations), "movies": _proposal_movies(movies),
            "items": [x for m in movies for x in m.ids], "context": []}


def find_bundle_proposals(items: List[Dict[str, Any]], duplicate_groups: List[Dict[str, Any]],
                          not_related: List[Dict[str, str]], locations: List[str]) -> Dict[str, Any]:
    """Bundle proposals and the existing bundles, from the items in memory (pure — no disk access).

    ``items``: all items (only movies in Library/Download Locations count); ``duplicate_groups``: the
    movie groups of the Duplicates filter (``duplicates._find_duplicates``); ``not_related``: the
    records of ``not-related.json``; ``locations``: the enabled Library Locations in config order.
    Returns ``{"proposals": [...], "bundles": [...]}`` (format: solution-design.md → Bundle API).
    """
    rows = [i for i in items if i.get("type") == "movie" and i.get("location") in ("library", "download")]
    movies = _movies(rows, duplicate_groups, locations)
    marks = _marks(not_related)
    loc_order = {_nk(loc): n for n, loc in enumerate(locations)}

    def marked(m: _Movie, key: str) -> bool:
        k = _norm_key(key)
        return any((_nk(x), k) in marks for x in m.all_ids)

    # ── Existing bundles ──
    members: Dict[str, List[_Movie]] = {}
    member_ids: Dict[str, List[str]] = {}
    bundle_path: Dict[str, str] = {}
    for m in movies:
        for path, ids in m.in_bundles.items():
            bk = _nk(path)
            members.setdefault(bk, []).append(m)
            member_ids.setdefault(bk, []).extend(ids)
            bundle_path.setdefault(bk, path)
    bundle_ckeys = {bk: {m.ckey for m in ms if m.ckey} for bk, ms in members.items()}
    bundle_whole = {bk: {m.base for m in ms if m.base and not m.marker} for bk, ms in members.items()}
    bundle_name = {bk: _collection_norm(os.path.basename(bundle_path[bk])) for bk in members}
    bundle_loc = {bk: os.path.dirname(bundle_path[bk]) for bk in members}

    def bundle_sort(bk: str) -> Tuple[int, str]:
        return loc_order.get(_nk(bundle_loc[bk]), len(loc_order)), bk

    bundles_out = []
    for bk in sorted(members, key=lambda b: (os.path.basename(bundle_path[b]).lower(), bundle_sort(b))):
        ms = members[bk]
        name = os.path.basename(bundle_path[bk])
        # `Heat (1995)\Heat (1995)\` — usually an unpacked download, not a real bundle
        nested = len(member_ids[bk]) == 1 and os.path.basename(member_ids[bk][0]).lower() == name.lower()
        bundles_out.append({"path": bundle_path[bk], "name": name, "location": bundle_loc[bk],
                            "count": len(ms), "nested": nested, "items": sorted(member_ids[bk], key=str.lower)})

    # Rules 5 and 8: movies in a bundle, and copies of them, are never proposed
    candidates = [m for m in movies if not m.in_bundles]
    proposals: List[Dict[str, Any]] = []

    # ── Add to bundle (rule 7: before new bundles; collection before title; own location first) ──
    add_for: Dict[str, List[Tuple[_Movie, str]]] = {}
    taken: Set[int] = set()
    for m in candidates:
        options = []
        for bk, ms in members.items():
            if marked(m, "bundle:" + bundle_path[bk]):
                continue
            own = 0 if m.root and _nk(m.root) == _nk(bundle_loc[bk]) else 1
            if m.ckey and m.ckey in bundle_ckeys[bk]:
                options.append(((0, own) + bundle_sort(bk), bk, f"Same collection: {m.cdisplay}"))
            elif m.base and (m.base == bundle_name[bk] or m.base in bundle_whole[bk]) \
                    and not any(_conflict(m, o) for o in ms):
                options.append(((1, own) + bundle_sort(bk), bk, "Similar titles"))
        if options:
            _, bk, why = min(options)
            add_for.setdefault(bk, []).append((m, why))
            taken.add(id(m))
    for bk, found in add_for.items():
        proposals.append({
            "type": "add", "key": "bundle:" + bundle_path[bk], "bundle": bundle_path[bk],
            "name": os.path.basename(bundle_path[bk]), "location": bundle_loc[bk],
            "reason": " / ".join(sorted({why for _, why in found})),
            "movies": _proposal_movies([m for m, _ in found], {id(m): why for m, why in found}),
            "items": [x for m, _ in found for x in m.ids],
            "context": sorted(member_ids[bk], key=str.lower),
        })

    # ── New bundle: same collection first, then similar titles ──
    rest = [m for m in candidates if id(m) not in taken]
    by_collection: Dict[str, List[_Movie]] = {}
    for m in rest:
        if m.ckey and not marked(m, m.ckey):
            by_collection.setdefault(m.ckey, []).append(m)
    grouped: Set[int] = set()
    for key, ms in by_collection.items():
        if len(ms) < 2:
            continue
        cname = Counter(m.cdisplay for m in ms).most_common(1)[0][0]
        proposals.append(_new_proposal(key, f"Same collection: {cname}", collection_display(cname), ms, locations))
        grouped.update(id(m) for m in ms)

    by_title: Dict[str, List[_Movie]] = {}
    for m in rest:
        if id(m) not in grouped and m.base and not marked(m, "title:" + m.base):
            by_title.setdefault(m.base, []).append(m)
    for base, ms in by_title.items():
        # Title-only grouping needs the original film (title == base) and a sequel
        originals = sorted((m for m in ms if not m.marker), key=lambda m: (m.location != "library", m.ids[0].lower()))
        if not originals:
            continue
        group = [originals[0]]
        for m in ms:
            if m is not originals[0] and not any(_conflict(m, o) for o in group):
                group.append(m)
        if len(group) >= 2 and any(m.marker for m in group):
            proposals.append(_new_proposal("title:" + base, "Similar titles", originals[0].display, group, locations))

    # ── Merge bundles: same name or a shared collection, in different Library Locations ──
    for a, b in combinations(sorted(members, key=bundle_sort), 2):
        if _nk(bundle_loc[a]) == _nk(bundle_loc[b]):
            continue
        shared = bundle_ckeys[a] & bundle_ckeys[b]
        if bundle_name[a] != bundle_name[b] and not shared:
            continue
        pa, pb = bundle_path[a], bundle_path[b]
        if (_nk(pa), _norm_key("bundle:" + pb)) in marks or (_nk(pb), _norm_key("bundle:" + pa)) in marks:
            continue
        # Target: the bundle with more movies (tie → the first Library Location in the config)
        target, source = (a, b) if len(members[a]) >= len(members[b]) else (b, a)
        reason = "Same name" if bundle_name[a] == bundle_name[b] else \
            "Same collection: " + next(m.cdisplay for m in members[a] if m.ckey in shared)
        proposals.append({
            "type": "merge", "key": "bundle:" + bundle_path[target], "source": bundle_path[source],
            "target": bundle_path[target], "bundle": bundle_path[target],
            "name": os.path.basename(bundle_path[target]), "location": bundle_loc[target], "reason": reason,
            "movies": [{"ids": m.in_bundles[bundle_path[source]]} for m in members[source]],
            "items": sorted(member_ids[source], key=str.lower),
            "context": sorted(member_ids[target], key=str.lower),
        })

    order = {"add": 0, "new": 1, "merge": 2}
    proposals.sort(key=lambda p: (order[p["type"]], p["name"].lower()))
    return {"proposals": proposals, "bundles": bundles_out}


def recompute_bundles() -> None:
    """Recompute the proposals from the items and duplicate groups in memory into ``_state["bundles"]``."""
    locations = enabled_paths(load_config(), "locations")
    with _lock:
        items = list(all_items())
        dups = list((_state["status"] or {}).get("duplicate_movies") or [])
    result = find_bundle_proposals(items, dups, load_not_related(), locations)
    with _lock:
        _state["bundles"] = result


def current_bundles() -> Dict[str, Any]:
    """The last detection result (``GET /api/bundles``)."""
    with _lock:
        return dict(_state["bundles"])


# ── Not-related registry (web/not-related.json) ────────────────────────────────

def load_not_related() -> List[Dict[str, str]]:
    """The not-related records ``[{path, key}]``; a missing or broken file counts as none."""
    try:
        if NOT_RELATED_PATH.exists():
            data = json.loads(NOT_RELATED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [{"path": r["path"], "key": r["key"]} for r in data
                        if isinstance(r, dict) and isinstance(r.get("path"), str) and isinstance(r.get("key"), str)]
    except Exception as exc:
        log.warning("Could not load %s: %s", NOT_RELATED_PATH.name, exc)
    return []


def _save_not_related(records: List[Dict[str, str]]) -> None:
    try:
        NOT_RELATED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save %s: %s", NOT_RELATED_PATH.name, exc)


def _check_records(records: List[Dict[str, str]]) -> None:
    for r in records:
        if not r.get("path", "").strip() or not r.get("key", "").startswith(_KEY_PREFIXES):
            raise HTTPException(400, f"Invalid not-related record: {r}")


def list_not_related() -> List[Dict[str, Any]]:
    """Every record with ``found`` — False when its movie or bundle folder no longer exists on disk."""
    out = []
    for r in load_not_related():
        found = os.path.exists(r["path"])
        if found and r["key"].startswith("bundle:"):
            found = os.path.isdir(r["key"][len("bundle:"):])
        out.append({**r, "found": found})
    return out


def mark_not_related(records: List[Dict[str, str]]) -> Dict[str, Any]:
    """Add records (whole proposal, one movie, or a merge pair); proposals are recomputed right away."""
    _check_records(records)
    existing = load_not_related()
    have = _marks(existing)
    added = 0
    for r in records:
        k = (_nk(r["path"]), _norm_key(r["key"]))
        if k not in have:
            existing.append({"path": r["path"], "key": r["key"]})
            have.add(k)
            added += 1
    if added:
        _save_not_related(existing)
        log.info("Marked %d not-related record(s)", added)
    recompute_bundles()
    return {"ok": True, "added": added}


def remove_not_related(records: List[Dict[str, str]]) -> Dict[str, Any]:
    """Remove the given records; the movies count again right away."""
    gone = _marks(records)
    existing = load_not_related()
    remaining = [r for r in existing if (_nk(r["path"]), _norm_key(r["key"])) not in gone]
    removed = len(existing) - len(remaining)
    if removed:
        _save_not_related(remaining)
        log.info("Removed %d not-related record(s)", removed)
    recompute_bundles()
    return {"ok": True, "removed": removed, "remaining": len(remaining)}


def _rebase(path: str, old: str, new: str) -> Optional[str]:
    """``path`` with the prefix ``old`` replaced by ``new``; None when it is neither ``old`` nor below it."""
    p, o = _nk(path), _nk(old).rstrip("\\/")
    if p == o:
        return new
    if p.startswith(o + os.sep):
        return os.path.join(new, os.path.normpath(path)[len(o) + 1:])
    return None


def update_not_related_paths(old: str, new: str) -> None:
    """After a move or rename of ``old`` to ``new``: records of it (or below it) and their bundle keys follow."""
    records = load_not_related()
    changed = False
    for r in records:
        p = _rebase(r["path"], old, new)
        if p is not None:
            r["path"], changed = p, True
        if r["key"].startswith("bundle:"):
            b = _rebase(r["key"][len("bundle:"):], old, new)
            if b is not None:
                r["key"], changed = "bundle:" + b, True
    if changed:
        _save_not_related(records)


def convert_proposal_records(key: str, bundle: str) -> None:
    """After *Create bundle*: records of the proposal key (``collection:…``/``title:…``) now name the new bundle."""
    records = load_not_related()
    changed = False
    for r in records:
        if r["key"] == key:
            r["key"], changed = "bundle:" + bundle, True
    if changed:
        _save_not_related(records)
