#!/usr/bin/env python3
"""
Delete Empty Movie Folders - Find and delete folders without movie files.

Scans a directory tree and identifies folders that don't contain any movie files.
Only deletes when --doit is specified.
"""

import os
import argparse
from pathlib import Path
from typing import List


MOVIE_EXTENSIONS = {'.mp4', '.m4v', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.mpeg', '.mpg'}


def is_safe_to_delete(folder: Path) -> bool:
    """
    Check if folder is safe to delete with strict rules:
    - Must have 0 files (only subfolders), OR
    - Must have exactly 1 file and it must be an .nfo file
    - Any other case: NOT SAFE
    """
    try:
        # Get all files directly in this folder (not subfolders)
        files = [f for f in folder.iterdir() if f.is_file()]
        
        # No files at all - safe if no movie files in subfolders
        if len(files) == 0:
            # Check subfolders for movie files
            for item in folder.rglob('*'):
                if item.is_file() and item.suffix.lower() in MOVIE_EXTENSIONS:
                    return False
            return True
        
        # Exactly 1 file and it's an .nfo file - safe
        if len(files) == 1 and files[0].suffix.lower() == '.nfo':
            # Still check subfolders for movie files
            for item in folder.rglob('*'):
                if item.is_file() and item.suffix.lower() in MOVIE_EXTENSIONS:
                    return False
            return True
        
        # More than 1 file or the file is not .nfo - NOT SAFE
        return False
        
    except (PermissionError, OSError):
        return False


def find_empty_folders(base_path: str) -> List[str]:
    """
    Find all folders that are safe to delete (no movie files, 0 files or only 1 .nfo file).
    Returns list of folder paths (sorted).
    """
    empty_folders = []
    base = Path(base_path)
    
    if not base.exists():
        print(f"Error: Path does not exist: {base_path}")
        return empty_folders
    
    # Get all directories
    all_dirs = [d for d in base.rglob('*') if d.is_dir()]
    
    # Check each directory from deepest to shallowest
    # This ensures we check leaf directories first
    all_dirs.sort(key=lambda p: len(p.parts), reverse=True)
    
    for folder in all_dirs:
        if is_safe_to_delete(folder):
            empty_folders.append(str(folder))
    
    return sorted(empty_folders)


def delete_folders(folders: List[str], dry_run: bool = True):
    """
    Delete the specified folders.
    If dry_run is True, just show what would be deleted.
    """
    print("\n" + "="*80)
    if dry_run:
        print("DRY RUN - No folders will be deleted")
        print("SAFETY RULES: Only folders with 0 files OR 1 .nfo file (no movie files)")
    else:
        print("DELETING EMPTY FOLDERS")
    print("="*80 + "\n")
    
    if len(folders) == 0:
        print("No empty folders found!")
        return
    
    deleted_count = 0
    error_count = 0
    
    for folder in folders:
        print(folder)
        
        if not dry_run:
            try:
                folder_path = Path(folder)
                
                # EXTRA SAFETY: Double-check before deleting
                files = [f for f in folder_path.iterdir() if f.is_file()]
                if len(files) > 1 or (len(files) == 1 and files[0].suffix.lower() != '.nfo'):
                    print(f"  SKIPPED: Folder has {len(files)} files (safety check)")
                    error_count += 1
                    continue
                
                # Delete .nfo file if it exists
                if len(files) == 1:
                    files[0].unlink()
                
                # Delete the folder
                folder_path.rmdir()
                deleted_count += 1
            except OSError as e:
                print(f"  ERROR: {e}")
                error_count += 1
    
    print("\n" + "="*80)
    if dry_run:
        print(f"Total empty folders found: {len(folders)}")
        print("To delete these folders, run with --doit")
    else:
        print(f"Deleted: {deleted_count} folders")
        if error_count > 0:
            print(f"Errors: {error_count}")
    print("="*80)


def main():
    parser = argparse.ArgumentParser(
        description="Find and delete folders without movie files"
    )
    parser.add_argument(
        '--doit',
        action='store_true',
        help='Actually delete the folders (otherwise just show what would be deleted)'
    )
    parser.add_argument(
        '--path',
        default=r'M:\movies\iTunes',
        help='Path to scan for empty folders (default: M:\\movies\\iTunes)'
    )
    
    args = parser.parse_args()
    
    print(f"Scanning for empty folders in: {args.path}")
    empty_folders = find_empty_folders(args.path)
    
    delete_folders(empty_folders, dry_run=not args.doit)


if __name__ == '__main__':
    main()
