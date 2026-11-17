"""File-level operations on library items: info, play, move, rename, delete, and bundle moves."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from .bundles import convert_proposal_records, movie_key, strip_part, update_not_related_paths
from .duplicates import _update_not_duplicates_paths, recompute_duplicates
from .leftovers import leftover_paths_after
from .reorganize import IMAGE_EXTS, _root_of, _title_year, related_files, trickplay_folder
from .settings import (
    VIDEO_EXTS,
    enabled_paths,
    expand_naming,
    load_config,
    log,
    map_to_local,
    safe_transcoded_root,
    valid_component,
)
from .state import _lock, _state, all_items, forget_items, require_item, resolve_item


def _path_under_base(path: Path, bases: List[str]) -> bool:
    """Return True if path is a subdirectory of at least one configured base."""
    for base in bases:
        if not base:
            continue
        try:
            path.relative_to(Path(base))
            return True
        except ValueError:
            continue
    return False


def file_info(item_id: str) -> List[Dict[str, Any]]:
    item = require_item(item_id)
    out = []
    for f in item["files"]:
        p = Path(f)
        try:
            st = p.stat()
            out.append({
                "path":  f,
                "name":  p.name,
                "size":  st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except OSError:
            out.append({"path": f, "name": p.name, "size": None, "mtime": None})
    return out


def play_item(item_id: str, file: Optional[str] = None) -> Dict[str, Any]:
    """Launch a video file in the OS default player."""
    item = require_item(item_id)
    if file:
        if file not in item["files"]:
            raise HTTPException(400, "File does not belong to this item")
        target = file
    elif item["files"]:
        target = item["files"][0]
    else:
        raise HTTPException(400, "Item has no video files")
    if not Path(target).is_file():
        raise HTTPException(404, f"File not found on disk: {target}")
    config = load_config()
    if config.get("server_mode"):
        # Server can't open a player on the user's machine — return the local-share
        # path so the frontend can hand it to the local player.
        local = map_to_local(config, target)
        if not local:
            raise HTTPException(400, "No local share is configured for this location — "
                                     "playing is disabled in server mode (Settings → Paths)")
        return {"ok": True, "file": target, "local_file": local, "server_mode": True}
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as exc:
        raise HTTPException(500, f"Could not launch player: {exc}")
    return {"ok": True, "file": target}


def _item_target(item: Dict[str, Any]) -> Path:
    """What move/delete act on: the file for loose-file items (id is the file path), else the folder."""
    return Path(item["id"]) if item["id"] != item["path"] else Path(item["path"])


def _write_probe(folder: Path) -> Optional[str]:
    """Check that a file can really be created and removed in ``folder``.

    ``os.access`` is unreliable on Windows (ignores ACLs and share permissions),
    so an empty temp file is created and deleted. Returns an error message, or
    None when the folder is writable.

    Not ``tempfile.mkstemp``: on Windows it retries "forever" on PermissionError
    when ``os.access`` claims the folder is writable, hanging the request.
    """
    probe = folder / f".movie-organizer-write-test-{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(str(probe), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        os.unlink(str(probe))
        return None
    except OSError as exc:
        return f"Cannot write in {folder}: {exc.strerror or exc}"


def _tree_size(path: Path) -> int:
    """Total size in bytes of a file, or of all files below a folder."""
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt_size(n: int) -> str:
    """Human-readable size, e.g. ``4.2 GB``."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _move_roots(config: Dict[str, Any]) -> List[str]:
    """Location roots for the common-folder check (transcoded items live below output\\Movies and \\Series)."""
    t = safe_transcoded_root(config)
    extra = [os.path.join(t, "Movies"), os.path.join(t, "Series")] if t else []
    return [p for p in config.get("locations", []) + config.get("downloads", []) if p] + extra


def _common_folder(item: Dict[str, Any], roots: List[str]) -> Optional[str]:
    """The folder(s) between the item's location root and the item (e.g. 'James Bond'), or None."""
    parent = str(_item_target(item).parent)
    root = _root_of(parent, roots)
    return os.path.relpath(parent, root) if root else None


def move_groups(item_ids: List[str]) -> List[Dict[str, Any]]:
    """The selected items that sit in a common folder below their location root, per folder.

    Each entry: ``folder`` (full path), ``group`` (path below the root), ``ids`` (selected
    items in it) and ``total`` (scanned items in it, selected or not).
    """
    roots = _move_roots(load_config())
    groups: Dict[str, Dict[str, Any]] = {}
    for item_id in item_ids:
        item = resolve_item(item_id)
        rel = _common_folder(item, roots) if item else None
        if not rel:
            continue
        folder = str(_item_target(item).parent)
        g = groups.setdefault(os.path.normcase(folder), {"folder": folder, "group": rel, "ids": [], "total": 0})
        g["ids"].append(item_id)
    with _lock:
        items = list(all_items())
    for item in items:
        g = groups.get(os.path.normcase(str(_item_target(item).parent)))
        if g:
            g["total"] += 1
    return sorted(groups.values(), key=lambda g: g["folder"].lower())


def move_items(item_ids: List[str], target_base: str, dry_run: bool,
               keep_groups: bool = False) -> List[Dict[str, Any]]:
    """Move items into ``target_base``; dry run and real run perform the same checks.

    A loose file takes its own exact-stem sidecars (NFO, images, subtitles) and its
    ``<stem>.trickplay`` folder along (R-26, R-30).
    With ``keep_groups`` an item in a common folder (``move_groups``) goes into the same
    folder below the destination, which is created when missing; a source common folder
    left empty is removed (R-27).
    Checks per call: destination folder exists and is writable (real write probe).
    Checks per item: source exists, its parent folder is writable (the source is
    removed after a move), destination does not exist (also not twice within this
    batch), not already in that folder, and enough free space for a cross-drive
    move (which copies the data). Sidecars get the same checks as the video.
    A real move reports the leftover folders it left behind (``leftovers``).
    """
    if not target_base.strip():
        raise HTTPException(400, "target_base must not be empty")
    target = Path(target_base)
    if not target.is_dir():
        raise HTTPException(400, f"Destination folder does not exist: {target_base}")
    err = _write_probe(target)
    if err:
        raise HTTPException(400, err)

    target_dev = target.stat().st_dev
    free = shutil.disk_usage(str(target)).free
    config = load_config()
    roots = _move_roots(config) if keep_groups else []
    needed = 0                       # bytes a cross-drive move in this batch will copy
    planned: set = set()             # destinations already claimed in this batch
    probed: Dict[str, Optional[str]] = {}

    results = []
    for item_id in item_ids:
        item = resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        src = _item_target(item)
        rel = _common_folder(item, roots) if keep_groups else None
        dst_dir = target / rel if rel else target
        dst = dst_dir / src.name
        if not src.exists():
            results.append({"id": item_id, "ok": False, "error": f"Source no longer exists (rescan): {src}"})
            continue
        sidecars = _loose_followers(src) if src.is_file() else []
        # shutil.move silently overwrites an existing file (copy fallback on Windows)
        taken, replace = _taken_destination([src] + sidecars, dst_dir)
        if taken:
            results.append({"id": item_id, "ok": False, "error": f"Destination already exists: {taken}"})
            continue
        if _same_path(src.parent, dst_dir):
            results.append({"id": item_id, "ok": False, "error": "Item is already in that folder"})
            continue
        dst_keys = [os.path.normcase(os.path.normpath(str(dst_dir / p.name))) for p in [src] + sidecars]
        if any(k in planned for k in dst_keys):
            results.append({"id": item_id, "ok": False,
                            "error": f"Another selected item has the same name: {src.name}"})
            continue
        parent_key = os.path.normcase(str(src.parent))
        if parent_key not in probed:
            probed[parent_key] = _write_probe(src.parent)
        if probed[parent_key]:
            results.append({"id": item_id, "ok": False,
                            "error": f"{probed[parent_key]} (needed to remove the source after moving)"})
            continue
        read_only = next((p for p in [src] + sidecars if p.is_file() and not os.access(str(p), os.W_OK)), None)
        if read_only:
            results.append({"id": item_id, "ok": False, "error": f"Source file is read-only: {read_only}"})
            continue
        cross_drive = src.stat().st_dev != target_dev
        size = sum(_tree_size(p) for p in [src] + sidecars) if cross_drive else 0
        if cross_drive and needed + size > free:
            results.append({"id": item_id, "ok": False,
                            "error": f"Not enough free space on destination: needs {_fmt_size(size)}, "
                                     f"{_fmt_size(max(free - needed, 0))} left"})
            continue
        needed += size
        planned.update(dst_keys)
        also = [str(s) for s in sidecars]
        if dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "from": str(src), "to": str(dst),
                            "cross_drive": cross_drive, "size": size, "also": also,
                            "replaces": [str(r) for r in replace]})
            continue
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            _update_not_duplicates_paths(str(src), str(dst))
            update_not_related_paths(str(src), str(dst))
        except Exception as exc:
            results.append({"id": item_id, "ok": False, "error": str(exc)})
            continue
        # Sidecars only follow once the video itself has arrived
        moved, errors = _move_followers(sidecars, dst_dir, replace)
        result = {"id": item_id, "ok": True, "from": str(src), "to": str(dst), "also": moved,
                  "replaces": [str(r) for r in replace]}
        if errors:
            result["warning"] = "Some related files could not be moved (left in the source folder): " + "; ".join(errors)
        if rel:
            # rmdir only removes empty folders, so nothing still in the common folder is touched
            removed, p = [], src.parent
            for _ in Path(rel).parts:
                try:
                    p.rmdir()
                except OSError:
                    break
                removed.append(str(p))
                p = p.parent
            result["removed_folders"] = removed
        result["leftovers"] = leftover_paths_after([str(src)], config)
        results.append(result)
    return results


def _check_name(name: str) -> None:
    err = valid_component(name)
    if err:
        raise HTTPException(400, err)


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def rename_item(item_id: str, new_name: str, new_folder_name: Optional[str],
                rename_target: str, dry_run: bool) -> Dict[str, Any]:
    if not new_name.strip():
        raise HTTPException(400, "new_name must not be empty")
    if rename_target not in ("folder", "files", "both"):
        raise HTTPException(400, "rename_target must be folder, files, or both")
    item = require_item(item_id)
    _check_name(new_name)
    if new_folder_name:
        _check_name(new_folder_name)
    if item["id"] != item["path"] and rename_target != "files":
        raise HTTPException(400, "A loose file has no folder of its own — only the file can be renamed")
    if item["type"] == "series" and rename_target != "folder":
        raise HTTPException(400, "Renaming episode files is not supported — rename the series folder only")

    # Plan everything first so a conflict is found before anything is renamed
    folder_move: Optional[tuple] = None
    if rename_target in ("folder", "both"):
        src = Path(item["path"])
        folder_name = (new_folder_name or new_name) if rename_target == "both" else new_name
        folder_move = (src, src.parent / folder_name)

    video_files = sorted(
        [Path(f) for f in item["files"] if Path(f).suffix.lower() in VIDEO_EXTS],
        key=lambda f: f.name.lower(),
    ) if rename_target in ("files", "both") else []
    file_moves = []
    for i, vf in enumerate(video_files, 1):
        stem = new_name if len(video_files) == 1 else f"{new_name} - Part {i}"
        file_moves.append((vf, vf.parent / (stem + vf.suffix)))

    if folder_move and not folder_move[0].is_dir():
        raise HTTPException(404, f"Folder not found on disk: {folder_move[0]} — rescan and try again")
    for s, _ in file_moves:
        if not s.is_file():
            raise HTTPException(404, f"File not found on disk: {s} — rescan and try again")
    if folder_move and folder_move[1].exists() and not _same_path(*folder_move):
        raise HTTPException(400, f"Target folder already exists: {folder_move[1]}")
    sources = {os.path.normcase(str(s)) for s, _ in file_moves}
    for s, d in file_moves:
        if d.exists() and os.path.normcase(str(d)) not in sources:
            raise HTTPException(400, f"Target file already exists: {d}")

    changes: List[Dict[str, str]] = []
    if folder_move:
        changes.append({"type": "folder", "from": str(folder_move[0]), "to": str(folder_move[1])})
    for s, d in file_moves:
        # Files are renamed after the folder, so show their final location
        if folder_move:
            old, new = folder_move
            s, d = new / s.relative_to(old), new / d.relative_to(old)
        changes.append({"type": "file", "from": str(s), "to": str(d)})
    if dry_run:
        return {"ok": True, "dry_run": True, "changes": changes}

    if folder_move:
        try:
            folder_move[0].rename(folder_move[1])
        except Exception as exc:
            raise HTTPException(500, f"Folder rename failed: {exc}")
    for c in changes:
        if c["type"] != "file":
            continue
        try:
            Path(c["from"]).rename(c["to"])
        except Exception as exc:
            raise HTTPException(500, f"File rename failed: {exc}")
    return {"ok": True, "dry_run": False, "changes": changes}


def _own_sidecars(video: Path) -> List[Path]:
    """Exact-stem sidecars (NFO, images, subtitles) of a loose video that no other video in its folder shares."""
    try:
        siblings = [e.name for e in os.scandir(str(video.parent)) if e.is_file()]
    except OSError:
        return []
    shared: set = set()
    for other in siblings:
        if os.path.splitext(other)[1].lower() in VIDEO_EXTS and other.lower() != video.name.lower():
            shared.update(n.lower() for n in related_files(other, siblings))
    return [video.parent / n for n in related_files(video.name, siblings) if n.lower() not in shared]


def _own_trickplay(video: Path) -> Optional[Path]:
    """The loose video's Jellyfin ``<stem>.trickplay`` folder, unless another video in its folder has the same stem (R-30)."""
    try:
        entries = list(os.scandir(str(video.parent)))
    except OSError:
        return None
    files = [e.name for e in entries if e.is_file()]
    dirs = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
    name = trickplay_folder(video.name, files, dirs)
    return video.parent / name if name else None


def _loose_followers(video: Path) -> List[Path]:
    """Everything that moves with a loose video: its own sidecars, then its trickplay folder (R-26, R-30)."""
    trickplay = _own_trickplay(video)
    return _own_sidecars(video) + ([trickplay] if trickplay else [])


def _taken_destination(moving: List[Path], dst_dir: Path) -> Tuple[Optional[Path], List[Path]]:
    """The first destination that exists and must not be replaced (or None), and the folders that will be replaced.

    Nothing is ever overwritten (R-03), with one exception: a ``<stem>.trickplay`` folder at the destination
    that holds only images is replaced by the moving one (Jellyfin regenerates it anyway, R-30).
    """
    replace: List[Path] = []
    for p in moving:
        d = dst_dir / p.name
        if not d.exists():
            continue
        if p.is_dir() and p.name.lower().endswith(".trickplay") and d.is_dir() and not _image_tree(d)[2]:
            replace.append(d)
            continue
        return d, replace
    return None, replace


def _move_followers(followers: List[Path], dst_dir: Path, replace: List[Path]) -> Tuple[List[str], List[str]]:
    """Move a loose video's followers after it; each destination is checked again right before (R-03).

    Only a folder listed in ``replace`` (checked before, see ``_taken_destination``) is deleted first —
    files one by one, then folders with rmdir. Returns (moved sources, error texts).
    """
    moved: List[str] = []
    errors: List[str] = []
    for s in followers:
        d = dst_dir / s.name
        try:
            if d.exists():
                if not any(_same_path(d, r) for r in replace):
                    raise FileExistsError("destination already exists")
                err = _delete_image_tree(d)
                if err:
                    raise OSError(err)
            shutil.move(str(s), str(d))
            moved.append(str(s))
        except Exception as exc:
            errors.append(f"{s.name}: {getattr(exc, 'strerror', None) or exc}")
    return moved, errors


def _image_tree(folder: Path) -> Tuple[List[Path], List[Path], Optional[str]]:
    """Files and sub-folders below ``folder`` (deepest folders first), and an error when it holds anything but images."""
    files: List[Path] = []
    dirs: List[Path] = []
    for d, subdirs, names in os.walk(str(folder)):
        dirs.extend(Path(d) / s for s in subdirs)
        files.extend(Path(d) / n for n in names)
    other = next((f for f in files if f.suffix.lower() not in IMAGE_EXTS), None)
    err = f"{folder.name} holds other files than images (e.g. {other.name}) — left in place" if other else None
    return files, sorted(dirs, key=lambda p: len(p.parts), reverse=True), err


def _delete_image_tree(folder: Path) -> Optional[str]:
    """Delete a folder holding only images: files one by one, then folders with rmdir (no rmtree). Error text or None."""
    files, dirs, err = _image_tree(folder)
    if err:
        return err
    try:
        for f in files:
            f.unlink()
        for d in dirs + [folder]:
            d.rmdir()
    except OSError as exc:
        return f"{folder.name}: {exc.strerror or exc}"
    return None


def delete_items(item_ids: List[str], dry_run: bool) -> List[Dict[str, Any]]:
    """Delete items: a folder item with all its contents, a loose file with its own sidecars (R-01, R-25).

    A loose file's ``<stem>.trickplay`` folder goes too, but only when it holds nothing but images (R-30).
    A real delete reports the leftover folders it left behind (``leftovers``).
    """
    config = load_config()
    all_bases = config.get("locations", []) + config.get("downloads", []) + [safe_transcoded_root(config)]

    results = []
    for item_id in item_ids:
        item = resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        path = _item_target(item)
        # Safety: only delete paths that originated from a configured location
        if not _path_under_base(path, all_bases):
            results.append({"id": item_id, "ok": False, "error": "Path is outside configured locations"})
            continue
        if any(b and os.path.normcase(os.path.normpath(b)) == os.path.normcase(os.path.normpath(str(path)))
               for b in all_bases):
            results.append({"id": item_id, "ok": False, "error": "Refusing to delete a configured location itself"})
            continue
        # Guard against a stale cache: the folder must not hold videos this item doesn't know about
        if path.is_dir():
            known = {os.path.normcase(f) for f in item["files"]}
            unknown = [f for f in path.rglob("*")
                       if f.is_file() and f.suffix.lower() in VIDEO_EXTS and os.path.normcase(str(f)) not in known]
            if unknown:
                results.append({"id": item_id, "ok": False,
                                "error": f"Folder contains {len(unknown)} video file(s) not known from the last scan "
                                         f"(e.g. {unknown[0].name}) — rescan first"})
                continue
        sidecars = _own_sidecars(path) if path.is_file() else []
        trickplay = _own_trickplay(path) if path.is_file() else None
        tp_error = _image_tree(trickplay)[2] if trickplay else None
        also = [str(s) for s in sidecars] + ([str(trickplay)] if trickplay and not tp_error else [])
        if dry_run:
            result = {"id": item_id, "ok": True,  "dry_run": True, "path": str(path), "also": also}
            if tp_error:
                result["warning"] = tp_error
            results.append(result)
            continue
        try:
            if path.is_dir():
                shutil.rmtree(str(path))
            else:
                path.unlink()
        except Exception as exc:
            results.append({"id": item_id, "ok": False, "error": str(exc)})
            continue
        # Sidecars only go once the video itself is gone
        removed, errors = [], []
        for s in sidecars:
            try:
                s.unlink()
                removed.append(str(s))
            except OSError as exc:
                errors.append(f"{s.name}: {exc.strerror or exc}")
        if trickplay:
            tp_error = _delete_image_tree(trickplay)
            if tp_error:
                errors.append(tp_error)
            else:
                removed.append(str(trickplay))
        result = {"id": item_id, "ok": True, "path": str(path), "also": removed,
                  "leftovers": leftover_paths_after([str(path)], config)}
        if errors:
            result["warning"] = "Some related files could not be deleted: " + "; ".join(errors)
        results.append(result)
    deleted = [r["id"] for r in results if r["ok"] and not r.get("dry_run")]
    if deleted:
        # A copy left alone in its group is no longer a duplicate — no need to wait for the rescan
        forget_items(deleted)
        recompute_duplicates()
    return results


# ── Bundle moves (R-28) ────────────────────────────────────────────────────────

def _require_idle() -> None:
    """409 while a scan or a transcode runs — items and files may change under a bundle move."""
    with _lock:
        if _state["scanning"]:
            raise HTTPException(409, "A scan is running — wait until it has finished")
        job = _state["transcode"]
        if job and job.get("running"):
            raise HTTPException(409, "A transcoding job is running — wait until it has finished")


def _library_location(config: Dict[str, Any], path: str) -> Optional[str]:
    """The enabled Library Location that is ``path``, or None."""
    if not path.strip():
        return None
    return next((loc for loc in enabled_paths(config, "locations") if _same_path(Path(loc), Path(path))), None)


def _bundle_folder(config: Dict[str, Any], path: str) -> Path:
    """``path`` as an existing bundle folder: directly in an enabled Library Location, never a configured folder."""
    p = Path(path)
    configured = config.get("locations", []) + config.get("downloads", [])
    if (not path.strip() or not _library_location(config, str(p.parent)) or not p.is_dir()
            or any(c and _same_path(p, Path(c)) for c in configured)):
        raise HTTPException(400, f"Not a bundle folder directly in a Library Location: {path}")
    return p


def _within(path: Path, folder: Path) -> bool:
    """True when ``path`` is ``folder`` or below it."""
    p, f = os.path.normcase(os.path.normpath(str(path))), os.path.normcase(os.path.normpath(str(folder)))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def _copy_groups() -> Dict[str, List[str]]:
    """Item id → the ids of its duplicate group (the movie groups of the Duplicates filter)."""
    with _lock:
        groups = list((_state["status"] or {}).get("duplicate_movies") or [])
    out: Dict[str, List[str]] = {}
    for g in groups:
        ids = [m["id"] for m in g["items"]]
        for i in ids:
            out[i] = ids
    return out


def _relocate(jobs: List[Tuple[str, Optional[Path]]], dry_run: bool, create: Optional[Path] = None,
              out: bool = False) -> List[Dict[str, Any]]:
    """The bundle-move engine: move movies into a bundle (or, with ``out``, out of their bundle) — R-28.

    ``jobs``: (item id, target folder). A movie folder moves as a whole into the target; a loose file
    gets its own Naming-format folder there (``{title} ({year})``) and takes its exact-stem sidecars and
    trickplay folder along (R-26, R-30). Loose parts of one movie (``CD1``/``CD2``) in one request share
    that folder. ``create``: a new bundle folder, made before the first move. Every Move check applies:
    nothing is overwritten (checked again right before each move), write probes, free space, no two
    items with one destination. A bundle folder left empty by a move is removed with ``rmdir`` only;
    a real move reports the leftover folders it left behind (``leftovers``).
    """
    config = load_config()
    naming = config["naming"]
    copies = _copy_groups()
    items = {i: resolve_item(i) for i, _ in jobs}
    # Loose parts of one movie (same folder, same title + year) share one new folder
    group_of: Dict[str, Tuple] = {}
    for i, item in items.items():
        if item and item["id"] != item["path"]:
            group_of[i] = (os.path.normcase(item["path"]), movie_key(item))
    group_size = {g: sum(1 for x in group_of.values() if x == g) for g in set(group_of.values())}

    planned: Dict[str, Tuple] = {}            # normcase destination → movie that claimed it
    probed: Dict[str, Optional[str]] = {}
    needed: Dict[int, int] = {}               # device → bytes the cross-drive moves copy
    made: List[Path] = []                     # folders this call created
    results: List[Dict[str, Any]] = []
    for item_id, target in jobs:
        res: Dict[str, Any] = {"id": item_id, "ok": False}
        results.append(res)
        item = items[item_id]
        if not item:
            res["error"] = "Not found — rescan first"
            continue
        if item["type"] != "movie" or item.get("location") not in ("library", "download"):
            res["error"] = "Only movies in a Library or Download Location can be moved into or out of a bundle"
            continue
        bundle = item.get("bundle") or ""
        if out and (not bundle or target is None):
            res["error"] = "Not in a bundle"
            continue
        if not out:
            if bundle and _same_path(Path(bundle), target):
                res.update(skipped=True, error="Already in this bundle")
                continue
            copy = next((c for c in copies.get(item_id, []) if c != item_id
                         and ((resolve_item(c) or {}).get("bundle") or "")
                         and _same_path(Path(resolve_item(c)["bundle"]), target)), None)
            if copy:
                res.update(skipped=True, error=f"A copy is already in this bundle ({copy}) — see Duplicates")
                continue
        src = _item_target(item)
        if not src.exists():
            res["error"] = f"Source no longer exists (rescan): {src}"
            continue
        loose = item_id in group_of
        if loose:
            stem = os.path.splitext(src.name)[0]
            title, year, _ = _title_year(item, [stem])
            folder = expand_naming(naming["movie_folder"],
                                   strip_part(title) if group_size[group_of[item_id]] > 1 else title, year)
            if not folder:
                res["error"] = "No title or year known (NFO or name) — use Reorganize first"
                continue
            dst_dir, group = target / folder, group_of[item_id]
            moving = [src] + _loose_followers(src)
        else:
            dst_dir, group, moving = target, (item_id,), [src]
        dst = dst_dir / src.name
        dir_key = os.path.normcase(os.path.normpath(str(dst_dir)))
        if _same_path(src.parent, dst_dir):
            res["error"] = "Item is already in that folder"
            continue
        if loose and dir_key in planned and planned[dir_key] != group:
            res["error"] = f"Another selected movie gets the same folder: {dst_dir}"
            continue
        if loose and dir_key not in planned and dst_dir.exists():
            res["error"] = f"Destination already exists: {dst_dir}"
            continue
        keys = [os.path.normcase(os.path.normpath(str(dst_dir / p.name))) for p in moving]
        # shutil.move silently overwrites an existing file (copy fallback on Windows) — R-03
        taken, replace = _taken_destination(moving, dst_dir)
        taken = taken or next((dst_dir / p.name for p, k in zip(moving, keys) if k in planned), None)
        if taken:
            res["error"] = f"Destination already exists: {taken}"
            continue
        probe_dir = target if target.exists() else target.parent
        for d, why in ((probe_dir, ""), (src.parent, " (needed to remove the source after moving)")):
            k = os.path.normcase(str(d))
            if k not in probed:
                probed[k] = _write_probe(d)
            if probed[k]:
                res["error"] = probed[k] + why
                break
        if res.get("error"):
            continue
        read_only = next((p for p in moving if p.is_file() and not os.access(str(p), os.W_OK)), None)
        if read_only:
            res["error"] = f"Source file is read-only: {read_only}"
            continue
        dev = probe_dir.stat().st_dev
        cross = src.stat().st_dev != dev
        size = sum(_tree_size(p) for p in moving) if cross else 0
        free = shutil.disk_usage(str(probe_dir)).free
        if cross and needed.get(dev, 0) + size > free:
            res["error"] = (f"Not enough free space on destination: needs {_fmt_size(size)}, "
                            f"{_fmt_size(max(free - needed.get(dev, 0), 0))} left")
            continue
        needed[dev] = needed.get(dev, 0) + size
        if loose:
            planned[dir_key] = group
        planned.update((k, group) for k in keys)
        res.update({"ok": True, "from": str(src), "to": str(dst), "folder": str(dst_dir) if loose else None,
                    "also": [str(p) for p in moving[1:]], "cross_drive": cross, "size": size,
                    "replaces": [str(r) for r in replace]})
        if create is not None:
            res["created"] = str(create)
        if dry_run:
            res["dry_run"] = True
            continue

        try:
            for d in ([create] if create is not None else []) + ([dst_dir] if loose else []):
                if not any(_same_path(d, m) for m in made):
                    d.mkdir()   # raises when it appeared in the meantime — never merge into a foreign folder
                    made.append(d)
            if dst.exists():
                raise FileExistsError(f"Destination already exists: {dst}")
            shutil.move(str(src), str(dst))
        except Exception as exc:
            res.update(ok=False, error=str(exc))
            continue
        # A single loose file becomes a movie folder after the rescan; parts stay files in it
        track = str(dst_dir) if loose and group_size[group] == 1 else str(dst)
        _update_not_duplicates_paths(str(src), track)
        update_not_related_paths(str(src), track)
        moved, errors = _move_followers(moving[1:], dst_dir, replace)
        res["also"] = moved
        if errors:
            res["warning"] = "Some related files could not be moved (left in the source folder): " + "; ".join(errors)
        if bundle:
            # rmdir only removes empty folders, so nothing still in the bundle is touched
            removed, p = [], src.parent
            while _within(p, Path(bundle)):
                try:
                    p.rmdir()
                except OSError:
                    break
                removed.append(str(p))
                p = p.parent
            res["removed_folders"] = removed
        res["leftovers"] = leftover_paths_after([str(src)], config)
        log.info("Bundle move: %s → %s", src, dst)
    for d in reversed(made):
        try:
            d.rmdir()   # only when nothing ended up in it (a failed move)
        except OSError:
            pass
    return results


def bundle_move_items(item_ids: List[str], bundle: Optional[str], location: Optional[str], name: Optional[str],
                      dry_run: bool, proposal_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """*Create bundle* / *Add to bundle* / *Bundle…*: move movies into ``bundle``, or a new ``location``\\``name``.

    A new bundle folder is created only directly in an enabled Library Location, with a valid name that
    does not exist yet. After a real *Create bundle*, the proposal's not-related records (``proposal_key``)
    name the new folder.
    """
    _require_idle()
    config = load_config()
    create: Optional[Path] = None
    if bundle:
        target = _bundle_folder(config, bundle)
    else:
        loc = _library_location(config, location or "")
        if not loc:
            raise HTTPException(400, f"Not a Library Location: {location}")
        _check_name(name or "")
        target = Path(loc) / (name or "")
        if os.path.lexists(str(target)):
            raise HTTPException(400, f"{target} already exists — use Add to bundle when it is a bundle")
        create = target
    results = _relocate([(i, target) for i in item_ids], dry_run, create=create)
    if not dry_run and proposal_key and proposal_key.startswith(("collection:", "title:")) \
            and any(r["ok"] for r in results):
        convert_proposal_records(proposal_key, str(target))
    return results


def bundle_out_items(item_ids: List[str], dry_run: bool) -> List[Dict[str, Any]]:
    """*Out of bundle*: each movie to the root of its bundle's Library Location (no keep-together, R-27)."""
    _require_idle()
    config = load_config()
    jobs: List[Tuple[str, Optional[Path]]] = []
    for i in item_ids:
        b = ((resolve_item(i) or {}).get("bundle") or "")
        root = Path(b).parent if b and _library_location(config, str(Path(b).parent)) else None
        jobs.append((i, root))
    return _relocate(jobs, dry_run, out=True)


def bundle_merge(source: str, target: str, item_ids: Optional[List[str]], dry_run: bool) -> List[Dict[str, Any]]:
    """*Merge bundles*: the movies of ``source`` (all, or ``item_ids``) into ``target``, as *Add to bundle*."""
    _require_idle()
    config = load_config()
    src, tgt = _bundle_folder(config, source), _bundle_folder(config, target)
    if _same_path(src.parent, tgt.parent):
        raise HTTPException(400, "Both bundles are in the same Library Location — rename one instead")
    with _lock:
        members = [i["id"] for i in all_items() if i.get("bundle") and _same_path(Path(i["bundle"]), src)]
    ids = members if item_ids is None else item_ids
    moved = {r["id"]: r for r in _relocate([(i, tgt) for i in ids if i in members], dry_run)}
    return [moved.get(i) or {"id": i, "ok": False, "error": f"Not in the bundle {src}"} for i in ids]


def bundle_rename(path: str, new_name: str, dry_run: bool) -> Dict[str, Any]:
    """Rename a bundle folder (Bundles overview) with the Rename rules; a case-only change is allowed."""
    _require_idle()
    folder = _bundle_folder(load_config(), path)
    _check_name(new_name)
    dst = folder.parent / new_name
    if folder.name == new_name:
        raise HTTPException(400, "The bundle already has that name")
    if dst.exists() and not _same_path(folder, dst):
        raise HTTPException(400, f"Target already exists: {dst}")
    result = {"ok": True, "dry_run": dry_run, "from": str(folder), "to": str(dst)}
    if dry_run:
        return result
    try:
        folder.rename(dst)
    except OSError as exc:
        raise HTTPException(500, f"Bundle rename failed: {exc}")
    _update_not_duplicates_paths(str(folder), str(dst))
    update_not_related_paths(str(folder), str(dst))
    log.info("Bundle renamed: %s → %s", folder, dst)
    return result
