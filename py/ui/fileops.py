"""File-level operations on library items: info, play, move, rename, delete."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .duplicates import _update_not_duplicates_paths
from .settings import VIDEO_EXTS, load_config, map_to_local, safe_transcoded_root, valid_component
from .state import resolve_item


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
    item = resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
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
    item = resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
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


def move_items(item_ids: List[str], target_base: str, dry_run: bool) -> List[Dict[str, Any]]:
    """Move items into ``target_base``; dry run and real run perform the same checks.

    Checks per call: destination folder exists and is writable (real write probe).
    Checks per item: source exists, its parent folder is writable (the source is
    removed after a move), destination does not exist (also not twice within this
    batch), not already in that folder, and enough free space for a cross-drive
    move (which copies the data).
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
        dst = target / src.name
        if not src.exists():
            results.append({"id": item_id, "ok": False, "error": f"Source no longer exists (rescan): {src}"})
            continue
        # shutil.move silently overwrites an existing file (copy fallback on Windows)
        if dst.exists():
            results.append({"id": item_id, "ok": False, "error": f"Destination already exists: {dst}"})
            continue
        if _same_path(src.parent, target):
            results.append({"id": item_id, "ok": False, "error": "Item is already in that folder"})
            continue
        dst_key = os.path.normcase(os.path.normpath(str(dst)))
        if dst_key in planned:
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
        if src.is_file() and not os.access(str(src), os.W_OK):
            results.append({"id": item_id, "ok": False, "error": f"Source file is read-only: {src}"})
            continue
        cross_drive = src.stat().st_dev != target_dev
        size = _tree_size(src) if cross_drive else 0
        if cross_drive and needed + size > free:
            results.append({"id": item_id, "ok": False,
                            "error": f"Not enough free space on destination: needs {_fmt_size(size)}, "
                                     f"{_fmt_size(max(free - needed, 0))} left"})
            continue
        needed += size
        planned.add(dst_key)
        if dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "from": str(src), "to": str(dst),
                            "cross_drive": cross_drive, "size": size})
        else:
            try:
                shutil.move(str(src), str(dst))
                _update_not_duplicates_paths(str(src), str(dst))
                results.append({"id": item_id, "ok": True,  "from": str(src), "to": str(dst)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
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
    item = resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")
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


def delete_items(item_ids: List[str], dry_run: bool) -> List[Dict[str, Any]]:
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
        if dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "path": str(path)})
        else:
            try:
                if path.is_dir():
                    shutil.rmtree(str(path))
                else:
                    path.unlink()
                results.append({"id": item_id, "ok": True,  "path": str(path)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
    return results
