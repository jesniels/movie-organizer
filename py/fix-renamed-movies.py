#!/usr/bin/env python3
"""
Fix Renamed Movies - Match m4v files with mp4 files and rename them correctly.

This program:
1. Scans for .m4v files in iTunes Media folder
2. Scans for .mp4 files in Downloaded folder
3. Matches them up by movie title
4. Shows what renames are needed
5. Only renames when --doit is specified
"""

import os
import re
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def normalize_title(filename: str) -> str:
    """
    Normalize a movie title for matching.
    Removes year, file extension, and special characters.
    """
    # Remove file extension
    name = Path(filename).stem
    
    # Remove year in parenthesis like (2020)
    name = re.sub(r'\s*\(\d{4}\)\s*', ' ', name)
    
    # Remove common separators and normalize
    name = name.replace('.', ' ')
    name = name.replace('_', ' ')
    name = name.replace('-', ' ')
    
    # Remove extra whitespace and lowercase
    name = ' '.join(name.split()).lower()
    
    return name


def extract_year(filename: str) -> Optional[str]:
    """Extract year from filename if present, returns None if not found."""
    match = re.search(r'\((\d{4})\)', filename)
    return match.group(1) if match else None


def find_m4v_files(base_path: str) -> List[Tuple[str, str, Optional[str]]]:
    """
    Find all .m4v files recursively.
    Returns list of (full_path, relative_path, year_or_none)
    """
    m4v_files = []
    base = Path(base_path)
    
    if not base.exists():
        print(f"Warning: Path does not exist: {base_path}")
        return m4v_files
    
    for m4v_file in base.rglob('*.m4v'):
        relative = m4v_file.relative_to(base)
        year = extract_year(m4v_file.name)
        m4v_files.append((str(m4v_file), str(relative), year))
    
    return m4v_files


def find_mp4_files(base_path: str) -> List[str]:
    """Find all .mp4 files recursively. Returns list of full paths."""
    mp4_files = []
    base = Path(base_path)
    
    if not base.exists():
        print(f"Warning: Path does not exist: {base_path}")
        return mp4_files
    
    for mp4_file in base.rglob('*.mp4'):
        mp4_files.append(str(mp4_file))
    
    return mp4_files


def match_files(m4v_files: List[Tuple[str, str, Optional[str]]], 
                mp4_files: List[str],
                mp4_base: str) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """
    Match mp4 files to m4v files based on normalized titles.
    Returns (matches, unmatched) where:
      matches: list of (mp4_path, target_path, display_line)
      unmatched: list of mp4 paths that had no match
    """
    matches = []
    unmatched = []
    mp4_base_path = Path(mp4_base)
    
    # Create a mapping of normalized titles to m4v info
    m4v_map: Dict[str, Tuple[str, str, Optional[str]]] = {}
    for full_path, relative_path, year in m4v_files:
        normalized = normalize_title(Path(full_path).name)
        m4v_map[normalized] = (full_path, relative_path, year)
    
    # Try to match each mp4 file
    for mp4_path in mp4_files:
        mp4_normalized = normalize_title(Path(mp4_path).name)
        
        if mp4_normalized in m4v_map:
            m4v_full, m4v_relative, m4v_year = m4v_map[mp4_normalized]
            
            # Get the target path (replace .m4v with .mp4)
            target_relative = Path(m4v_relative).with_suffix('.mp4')
            
            # If m4v has no year, remove year from mp4 target too
            if m4v_year is None:
                # Remove year from folder name if present
                parts = list(target_relative.parts)
                if len(parts) > 0:
                    # Check if parent folder has year
                    folder = parts[0] if len(parts) > 1 else ''
                    folder_clean = re.sub(r'\s*\(\d{4}\)\s*', '', folder).strip()
                    if folder != folder_clean and len(parts) > 1:
                        parts[0] = folder_clean
                        target_relative = Path(*parts)
            
            # Get relative path of source mp4
            mp4_relative = Path(mp4_path).relative_to(mp4_base_path)
            
            # Format with forward slashes for display
            display_line = f"{str(mp4_relative).replace(chr(92), '/')} -> {str(target_relative).replace(chr(92), '/')}"
            matches.append((mp4_path, str(target_relative), display_line))
        else:
            unmatched.append(mp4_path)
    
    return matches, unmatched


def perform_renames(matches: List[Tuple[str, str, str]], 
                   target_base: str, 
                   dry_run: bool = True):
    """
    Perform the actual renames.
    If dry_run is True, just show what would be done.
    """
    target_base_path = Path(target_base)
    
    print("\n" + "="*80)
    if dry_run:
        print("DRY RUN - No files will be renamed")
    else:
        print("PERFORMING RENAMES")
    print("="*80 + "\n")
    
    renamed_count = 0
    error_count = 0
    
    for mp4_path, target_relative, display_line in matches:
        print(display_line)
        
        if not dry_run:
            target_full = target_base_path / target_relative
            
            # Create target directory if needed
            target_full.parent.mkdir(parents=True, exist_ok=True)
            
            # Rename the file
            try:
                Path(mp4_path).rename(target_full)
                renamed_count += 1
            except Exception as e:
                print(f"  ERROR: {e}")
                error_count += 1
    
    print("\n" + "="*80)
    if dry_run:
        print(f"Total matches found: {len(matches)}")
        print("To perform the renames, run with --doit")
    else:
        print(f"Renamed: {renamed_count} files")
        if error_count > 0:
            print(f"Errors: {error_count}")
    print("="*80)


def main():
    parser = argparse.ArgumentParser(
        description="Match and rename mp4 files based on m4v reference files"
    )
    parser.add_argument(
        '--doit',
        action='store_true',
        help='Actually perform the renames (otherwise just show what would be done)'
    )
    parser.add_argument(
        '--m4v-path',
        default=r'M:\iTunes\iTunes Media\Movies',
        help='Path to m4v files (default: M:\\iTunes\\iTunes Media\\Movies)'
    )
    parser.add_argument(
        '--mp4-path',
        default=r'M:\Downloaded\iTunes2',
        help='Path to mp4 files to rename (default: M:\\Downloaded\\iTunes2)'
    )
    
    args = parser.parse_args()
    
    print("Scanning for m4v files (reference)...")
    m4v_files = find_m4v_files(args.m4v_path)
    print(f"Found {len(m4v_files)} m4v files")
    
    print("\nScanning for mp4 files (to rename)...")
    mp4_files = find_mp4_files(args.mp4_path)
    print(f"Found {len(mp4_files)} mp4 files")
    
    print("\nMatching files...")
    matches, unmatched = match_files(m4v_files, mp4_files, args.mp4_path)
    
    print(f"\nMatched: {len(matches)} files")
    print(f"Unmatched: {len(unmatched)} files")
    
    if len(unmatched) > 0:
        print("\nUnmatched mp4 files:")
        for mp4_path in unmatched:
            print(f"  {Path(mp4_path).name}")
    
    if len(matches) == 0:
        print("\nNo matches found!")
        return
    
    perform_renames(matches, args.mp4_path, dry_run=not args.doit)


if __name__ == '__main__':
    main()
