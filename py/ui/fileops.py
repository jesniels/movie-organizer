"""File-level operations on library items: info, play, move, rename, delete."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from .duplicates import _update_not_duplicates_paths
from .settings import VIDEO_EXTS, load_config
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


def move_items(item_ids: List[str], target_base: str, dry_run: bool) -> List[Dict[str, Any]]:
    if not target_base.strip():
        raise HTTPException(400, "target_base must not be empty")

    results = []
    for item_id in item_ids:
        item = resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        src = Path(item["path"])
        dst = Path(target_base) / src.name
        if dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "from": str(src), "to": str(dst)})
        else:
            try:
                shutil.move(str(src), str(dst))
                _update_not_duplicates_paths(str(src), str(dst))
                results.append({"id": item_id, "ok": True,  "from": str(src), "to": str(dst)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
    return results


def rename_item(item_id: str, new_name: str, new_folder_name: Optional[str],
                rename_target: str, dry_run: bool) -> Dict[str, Any]:
    if not new_name.strip():
        raise HTTPException(400, "new_name must not be empty")
    if rename_target not in ("folder", "files", "both"):
        raise HTTPException(400, "rename_target must be folder, files, or both")
    item = resolve_item(item_id)
    if not item:
        raise HTTPException(404, "Not found")

    changes: List[Dict[str, str]] = []
    folder_dst: Optional[Path] = None

    # —— folder rename ——————————————————————————————————————
    if rename_target in ("folder", "both"):
        src = Path(item["path"])
        folder_name = (new_folder_name or new_name) if rename_target == "both" else new_name
        dst = src.parent / folder_name
        changes.append({"type": "folder", "from": str(src), "to": str(dst)})
        if not dry_run:
            try:
                src.rename(dst)
                folder_dst = dst
            except Exception as exc:
                raise HTTPException(500, f"Folder rename failed: {exc}")

    # —— file rename ——————————————————————————————————————
    if rename_target in ("files", "both"):
        # If folder was already renamed, files are now under the new folder path
        video_files = sorted(
            [Path(f) for f in item["files"] if Path(f).suffix.lower() in VIDEO_EXTS],
            key=lambda f: f.name.lower(),
        )
        if folder_dst is not None:
            video_files = [folder_dst / f.name for f in video_files]

        if len(video_files) == 1:
            vf = video_files[0]
            new_file = vf.parent / (new_name + vf.suffix)
            changes.append({"type": "file", "from": str(vf), "to": str(new_file)})
            if not dry_run:
                try:
                    vf.rename(new_file)
                except Exception as exc:
                    raise HTTPException(500, f"File rename failed: {exc}")
        else:
            for i, vf in enumerate(video_files, 1):
                new_file = vf.parent / f"{new_name} - Part {i}{vf.suffix}"
                changes.append({"type": "file", "from": str(vf), "to": str(new_file)})
                if not dry_run:
                    try:
                        vf.rename(new_file)
                    except Exception as exc:
                        raise HTTPException(500, f"File rename failed: {exc}")

    return {"ok": True, "dry_run": dry_run, "changes": changes}


def delete_items(item_ids: List[str], dry_run: bool) -> List[Dict[str, Any]]:
    config = load_config()
    all_bases = config.get("locations", []) + config.get("downloads", [])

    results = []
    for item_id in item_ids:
        item = resolve_item(item_id)
        if not item:
            results.append({"id": item_id, "ok": False, "error": "Not found"})
            continue
        path = Path(item["path"])
        # Safety: only delete paths that originated from a configured location
        if not _path_under_base(path, all_bases):
            results.append({"id": item_id, "ok": False, "error": "Path is outside configured locations"})
            continue
        if dry_run:
            results.append({"id": item_id, "ok": True,  "dry_run": True, "path": str(path)})
        else:
            try:
                shutil.rmtree(str(path))
                results.append({"id": item_id, "ok": True,  "path": str(path)})
            except Exception as exc:
                results.append({"id": item_id, "ok": False, "error": str(exc)})
    return results
