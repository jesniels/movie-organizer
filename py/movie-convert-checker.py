from __future__ import annotations
import argparse
import sys
import logging
import itertools
import time
import os
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional
from difflib import SequenceMatcher

try:
    from win32com.shell import shell, shellcon
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


'''
move-convert-checker

Command-line tool to compare an iTunes library Movies folder against
converted movie folders and an external movie library, reporting
which iTunes movie folders still need conversion.
'''


'''
Normalize a movie/folder name for comparison.

Parameters
----------
name: str
    Original movie or folder name.

Returns
-------
str
    Normalized string (lowercase, alphanumeric only, years removed).
'''
def normalize_name(name: str) -> str:
    normalized: str = name
    # Remove common year patterns like " (2019)" or " 2019"
    import re
    normalized = re.sub(r"\((19|20)\d{2}\)", "", normalized)
    normalized = re.sub(r"\b(19|20)\d{2}\b", "", normalized)
    # Remove quality indicators like "(1080p HD)", "1080p", or "(1080)"
    # We use \b to ensure we match whole words and don't strip numbers from titles
    normalized = re.sub(r"\(?\d+p\s*hd?\)?", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\(?\b(1080|720|480)\b\)?", "", normalized, flags=re.IGNORECASE)
    # Remove leading sequence numbers like "01 ", "02 ", "03 " (zero-padded or high numbers like 10+)
    # Only match 2-digit numbers (01-99) at the very start
    normalized = re.sub(r"^(\d{2})\s+", "", normalized)
    # Now convert to lowercase
    normalized = normalized.lower()
    # Keep only alphanumeric characters and spaces
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    # Collapse whitespace
    normalized = " ".join(normalized.split())
    return normalized


'''
Find a directory named 'Movies' (case-insensitive) under the given root.

Parameters
----------
itunes_root: Path
    Top-level path to search within.

Returns
-------
Optional[Path]
    First matching Movies directory, or None if not found.
'''
def find_movies_dir(itunes_root: Path) -> Optional[Path]:
    movies_dir: Optional[Path] = None
    if not itunes_root.exists() or not itunes_root.is_dir():
        return None
    # Walk the directory tree; prefer shallower results
    for p in itunes_root.rglob("*"):
        try:
            is_dir: bool = p.is_dir()
        except Exception:
            is_dir = False
        if is_dir and p.name.lower() == "movies":
            movies_dir = p
            break
    return movies_dir


'''
Produce a list of iTunes movie entries from the given Movies folder.

Each entry is a tuple of (folder_name, [file_names...]). If an item
in `movies_dir` is a file (e.g., Movie.m4v), the folder_name will be
the file stem and the files list will contain the filename.

Parameters
----------
movies_dir: Path
    Path pointing to the Movies directory.

Returns
-------
List[Tuple[str, List[str]]]
    A list of (folder_name, files) tuples.
'''
def list_itunes_movies(movies_dir: Path) -> List[Tuple[str, List[str]]]:
    entries: List[Tuple[str, List[str]]] = []
    if not movies_dir.exists() or not movies_dir.is_dir():
        return entries
    for child in sorted(movies_dir.iterdir(), key=lambda x: x.name.lower()):
        name: str = child.name
        files: List[str] = []
        if child.is_dir():
            # gather files directly under the movie folder (not deep)
            for f in sorted(child.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file():
                    files.append(f.name)
        elif child.is_file():
            files.append(child.name)
        entries.append((name, files))
    return entries


'''
Load a set of normalized names from a folder representing converted movies.

This collects directory names and file stems recursively.

Parameters
----------
folder: Path
    Directory to scan for converted movie names.

Returns
-------
Tuple[Set[str], Set[str]]
    A tuple of (normalized_names, raw_names_lower) sets.
'''
def load_library_names(folder: Path) -> Tuple[Set[str], Set[str]]:
    logger: logging.Logger = logging.getLogger(__name__)
    names: Set[str] = set()
    raw_names_lower: Set[str] = set()
    if not folder.exists() or not folder.is_dir():
        return names, raw_names_lower
    
    logger.debug(f"Loading library names from: {folder}")
    
    # Use rglob to recursively find all files and directories
    try:
        for child in folder.rglob("*"):
            try:
                if child.is_dir():
                    n: str = normalize_name(child.name)
                    if n:  # Skip empty normalized names
                        names.add(n)
                        raw_names_lower.add(child.name.strip().lower())
                        logger.debug(f"  Added dir: '{child.name}' -> '{n}'")
                elif child.is_file():
                    n: str = normalize_name(child.stem)
                    if n:  # Skip empty normalized names
                        names.add(n)
                        raw_names_lower.add(child.stem.strip().lower())
                        logger.debug(f"  Added file stem: '{child.stem}' -> '{n}'")
            except Exception as e:
                logger.debug(f"  Skipped {child}: {e}")
                continue
    except Exception as e:
        logger.warning(f"Error scanning {folder}: {e}")
    
    logger.debug(f"Loaded {len(raw_names_lower)} raw names, {len(names)} normalized names")
    
    return names, raw_names_lower


'''
Determine whether an iTunes entry name matches any name in converted sets.

Simple 3-step matching:
1. Exact match (case-insensitive, ignoring extensions)
2. Exact normalized match (only if step 1 fails)
3. Fuzzy normalized match (only if step 2 fails)

Parameters
----------
itunes_entry_name: str
    Original folder or file name from iTunes.
converted_sets: List[Set[str]]
    List of sets containing normalized converted names (from other folders).
converted_raw_sets_lower: List[Set[str]]
    List of sets containing lowercased raw un-normalized names for exact matching.
threshold: float
    Similarity threshold for fuzzy matching (0..1).

Returns
-------
Tuple[bool, str]
    (True if a match is found, match type: "exact", "normalized", "fuzzy", or "")
'''
def is_converted(itunes_entry_name: str, converted_sets: List[Set[str]], converted_raw_sets_lower: List[Set[str]] = None, threshold: float = 0.88) -> Tuple[bool, str]:
    import re
    logger: logging.Logger = logging.getLogger(__name__)
    
    # Get the base name without extension for iTunes entry
    # CRITICAL: We only want to strip real movie extensions.
    # Blindly using Path(name).stem mangles names like "R.I.P.D. 2" into "R.I.P.D"
    p = Path(itunes_entry_name)
    movie_extensions = {'.m4v', '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.mpg', '.mpeg'}
    if p.suffix.lower() in movie_extensions:
        itunes_base = p.stem
    else:
        itunes_base = itunes_entry_name
    
    itunes_lower: str = itunes_base.strip().lower()
    
    # STEP 1: Exact match - case insensitive, ignoring extension only
    if converted_raw_sets_lower:
        for raw_set_lower in converted_raw_sets_lower:
            if itunes_lower in raw_set_lower:
                logger.debug(f"  EXACT match: '{itunes_base}'")
                return True, "exact"
    
    # STEP 2: Normalize and try exact normalized match
    normalized_itunes: str = normalize_name(itunes_base)
    
    for cset in converted_sets:
        if normalized_itunes in cset:
            logger.debug(f"  NORMALIZED match: '{itunes_entry_name}' -> '{normalized_itunes}'")
            return True, "normalized"
    
    # STEP 3: Fuzzy matching (only if normalized string is long enough)
    if len(normalized_itunes) < 10:
        return False, ""
    
    # Extract all numbers from both strings to check for sequel mismatches
    itunes_numbers = re.findall(r'\d+', normalized_itunes)
    
    for cset in converted_sets:
        for candidate in cset:
            # Skip if too different in length
            len_diff: float = abs(len(normalized_itunes) - len(candidate)) / max(len(normalized_itunes), len(candidate))
            if len_diff > 0.3:
                continue
            
            ratio: float = SequenceMatcher(None, normalized_itunes, candidate).ratio()
            if ratio >= threshold:
                # Check for sequel number mismatches anywhere in the string
                candidate_numbers = re.findall(r'\d+', candidate)
                
                # If number sets are different, we check if it's a sequel mismatch
                if set(itunes_numbers) != set(candidate_numbers):
                    # If one has "2", "3", etc. and the other doesn't, or they have different numbers, 
                    # it's a sequel mismatch. We allow "1" to match nothing (e.g. "Movie" matches "Movie 1")
                    itunes_sequels = [n for n in itunes_numbers if n != '1']
                    candidate_sequels = [n for n in candidate_numbers if n != '1']
                    if itunes_sequels != candidate_sequels:
                        continue
                
                logger.debug(f"  FUZZY match ({ratio:.2f}): '{itunes_entry_name}' -> '{candidate}'")
                return True, "fuzzy"
    
    return False, ""


'''
From a list of iTunes entries, categorize as converted (with match type) or unconverted.

Parameters
----------
itunes_entries: List[Tuple[str, List[str]]]
    Output from `list_itunes_movies`.
converted_sets: List[Set[str]]
    Converted name sets to compare against.
converted_raw_sets_lower: List[Set[str]]
    Lowercased raw un-normalized name sets for simple filename matching.

Returns
-------
Tuple[List[str], Dict[str, List[str]]]
    (unconverted list, dict with match types as keys and movie lists as values)
'''
def find_unconverted(itunes_entries: List[Tuple[str, List[str]]], converted_sets: List[Set[str]], converted_raw_sets_lower: List[Set[str]]) -> Tuple[List[str], Dict[str, List[str]]]:
    logger: logging.Logger = logging.getLogger(__name__)
    need_convert: List[str] = []
    converted_by_type: Dict[str, List[str]] = {"exact": [], "normalized": [], "fuzzy": []}
    spinner: itertools.cycle = itertools.cycle(['|', '/', '-', '\\'])
    
    priority = {"exact": 3, "normalized": 2, "fuzzy": 1, "": 0}
    
    for idx, (folder_name, files) in enumerate(itunes_entries):
        # Show spinner progress
        if idx % 10 == 0:
            print(f"\rScanning iTunes movies... {next(spinner)}", end='', flush=True)
        
        try:
            logger.debug(f"Checking: {folder_name}")
            best_match: str = ""
            
            # Check both folder name and all file stems, keeping the best match
            candidates = [folder_name]
            for f in files:
                # Add file stem if it's different from folder name
                stem = Path(f).stem
                if stem.lower() != folder_name.lower():
                    candidates.append(stem)
            
            for cand in candidates:
                # Break early if we already found an exact match
                if best_match == "exact":
                    break
                    
                flag, mtype = is_converted(cand, converted_sets, converted_raw_sets_lower)
                if flag:
                    if priority[mtype] > priority[best_match]:
                        best_match = mtype
            
            if best_match:
                logger.debug(f"  Already converted ({best_match}): {folder_name}")
                if best_match in converted_by_type:
                    converted_by_type[best_match].append(folder_name)
            else:
                logger.debug(f"  NOT CONVERTED: {folder_name}")
                need_convert.append(folder_name)
        except Exception as e:
            logger.warning(f"Error processing {folder_name}: {e}")
            # on error, conservatively add to need_convert
            need_convert.append(folder_name)
    
    print("\rScanning iTunes movies... done" + " " * 10)
    return need_convert, converted_by_type


'''
Opens a Windows Explorer window and selects the specified items.
'''
def open_explorer_and_select(parent_path: str, item_names: List[str]) -> None:
    if not HAS_WIN32:
        print("Selection in Explorer is only supported on Windows with pywin32 installed.")
        return

    try:
        # 1. Convert the parent path to a PIDL (Pointer to an Item ID List)
        parent_pidl = shell.SHILCreateFromPath(os.path.abspath(parent_path), 0)[0]
        
        # 2. Get the desktop shell folder to bind to the parent directory
        desktop = shell.SHGetDesktopFolder()
        parent_shell_folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
        
        # 3. Create a mapping of item names to their relative PIDLs
        to_select = []
        # Convert names set for O(1) lookup
        names_to_find = set(item_names)
        
        for item in parent_shell_folder:
            # Get the display name of the current item in the folder
            name = parent_shell_folder.GetDisplayNameOf(item, shellcon.SHGDN_INFOLDER | shellcon.SHGDN_FORPARSING)
            
            if name in names_to_find:
                to_select.append(item)

        # 4. Open the window and perform the selection
        if to_select:
            shell.SHOpenFolderAndSelectItems(parent_pidl, to_select, 0)
        else:
            print("No matching items found to select in Explorer.")
    except Exception as e:
        print(f"Error opening Explorer: {e}")


'''
Stub for converting movies. This will be implemented later.

Parameters
----------
movies_root: Path
    Full path to the iTunes Movies directory.
movies_to_convert: List[str]
    List of folder names (from iTunes) that need conversion.
'''
def convertMovies(movies_root: Path, movies_to_convert: List[str], select_mode: bool = False) -> None:
    # Print the summary
    print(f"\niTunes Movies folder: {movies_root}")
    if not movies_to_convert:
        print("All movies are already converted!")
        return

    print(f"Movies that need conversion: {len(movies_to_convert)}")
    for m in movies_to_convert:
        print(f" - {m}")
    
    # Open explorer and select the folders if requested
    if select_mode:
        print(f"\nSelecting {len(movies_to_convert)} folders in Explorer...")
        open_explorer_and_select(str(movies_root), movies_to_convert)


'''
Debug version of convertMovies - prints detailed information about movies categorized by match type.

Parameters
----------
movies_root: Path
    Full path to the iTunes Movies directory.
movies_to_convert: List[str]
    List of folder names (from iTunes) that need conversion.
converted_by_type: Dict[str, List[str]]
    Dictionary mapping match types to lists of converted movies.
'''
def debugMovies(movies_root: Path, movies_to_convert: List[str], converted_by_type: Dict[str, List[str]], select_mode: bool = False) -> None:
    print(f"\n{'='*70}")
    print("DEBUG MODE: Movie Conversion Status")
    print(f"{'='*70}")
    print(f"iTunes Movies folder: {movies_root}")
    
    # Show converted movies by type
    print(f"\n{'='*70}")
    print("ALREADY CONVERTED - Exact Match (case-insensitive, same filename):")
    print(f"{'='*70}")
    exact_matches = converted_by_type.get("exact", [])
    print(f"Total: {len(exact_matches)} movies")
    for idx, m in enumerate(exact_matches, 1):
        print(f" {idx:3d}. {m}")
    
    print(f"\n{'='*70}")
    print("ALREADY CONVERTED - Normalized Match (after removing years/prefixes):")
    print(f"{'='*70}")
    normalized_matches = converted_by_type.get("normalized", [])
    print(f"Total: {len(normalized_matches)} movies")
    for idx, m in enumerate(normalized_matches, 1):
        print(f" {idx:3d}. {m}")
    
    print(f"\n{'='*70}")
    print("ALREADY CONVERTED - Fuzzy Match (similarity-based):")
    print(f"{'='*70}")
    fuzzy_matches = converted_by_type.get("fuzzy", [])
    print(f"Total: {len(fuzzy_matches)} movies")
    for idx, m in enumerate(fuzzy_matches, 1):
        print(f" {idx:3d}. {m}")
    
    print(f"\n{'='*70}")
    print("NEED CONVERSION:")
    print(f"{'='*70}")
    print(f"Total: {len(movies_to_convert)} movies")
    for idx, m in enumerate(movies_to_convert, 1):
        print(f" {idx:3d}. {m}")
    
    print(f"\n{'='*70}")
    print("SUMMARY:")
    print(f"{'='*70}")
    total_converted = len(exact_matches) + len(normalized_matches) + len(fuzzy_matches)
    total_all = total_converted + len(movies_to_convert)
    print(f"  Exact matches:      {len(exact_matches):4d}")
    print(f"  Normalized matches: {len(normalized_matches):4d}")
    print(f"  Fuzzy matches:      {len(fuzzy_matches):4d}")
    print(f"  ------------------------")
    print(f"  Total converted:    {total_converted:4d}")
    print(f"  Need conversion:    {len(movies_to_convert):4d}")
    print(f"  ------------------------")
    print(f"  Total movies:       {total_all:4d}")
    print(f"{'='*70}\n")

    if select_mode and movies_to_convert:
        print(f"Opening Explorer to select {len(movies_to_convert)} unconverted movies...")
        open_explorer_and_select(str(movies_root), movies_to_convert)


'''
Parse and validate CLI arguments.

Parameters
----------
argv: List[str]
    Argument vector (typically sys.argv[1:]).

Returns
-------
Tuple[Path, Path, Path, bool, bool]
    (itunes_root, converted_folder, movie_library_folder, debug_mode, select_mode)
'''
def parse_args(argv: List[str]) -> Tuple[Path, Path, Path, bool, bool]:
    parser = argparse.ArgumentParser(description="Check which iTunes movies need conversion")
    parser.add_argument("itunes_root", help="Path to iTunes library root")
    parser.add_argument("converted_folder", help="Path to folder containing converted movies")
    parser.add_argument("movie_library_folder", help="Path to external movie library folder")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--select", action="store_true", help="Open Explorer and select unconverted movies")
    args = parser.parse_args(argv)

    itunes_root: Path = Path(args.itunes_root).expanduser().resolve()
    converted_folder: Path = Path(args.converted_folder).expanduser().resolve()
    movie_library_folder: Path = Path(args.movie_library_folder).expanduser().resolve()

    if not itunes_root.exists() or not itunes_root.is_dir():
        raise SystemExit(f"itunes_root not found or not a directory: {itunes_root}")
    if not converted_folder.exists() or not converted_folder.is_dir():
        raise SystemExit(f"converted_folder not found or not a directory: {converted_folder}")
    if not movie_library_folder.exists() or not movie_library_folder.is_dir():
        raise SystemExit(f"movie_library_folder not found or not a directory: {movie_library_folder}")

    return itunes_root, converted_folder, movie_library_folder, args.debug, args.select


'''
Main entry point for the checker.

Steps:
1. Parse args and validate paths.
2. Locate the iTunes Movies directory.
3. List iTunes movie entries.
4. Load converted names from the two provided folders.
5. Determine which iTunes entries still need conversion.
6. Call `convertMovies` with the list.

Parameters
----------
argv: List[str]
    Argument vector (typically sys.argv[1:]).

Returns
-------
int
    Exit code (0 on success).
'''
def main(argv: List[str]) -> int:
    itunes_root: Path
    converted_folder: Path
    movie_library_folder: Path
    debug_mode: bool
    select_mode: bool
    itunes_root, converted_folder, movie_library_folder, debug_mode, select_mode = parse_args(argv)
    
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if debug_mode else logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    logger: logging.Logger = logging.getLogger(__name__)

    logger.info("Starting movie conversion checker...")
    logger.info(f"iTunes root: {itunes_root}")
    logger.info(f"Converted folder: {converted_folder}")
    logger.info(f"Movie library: {movie_library_folder}")
    
    movies_dir: Optional[Path] = find_movies_dir(itunes_root)
    if movies_dir is None:
        logger.error("Could not find a 'Movies' directory under the provided iTunes root.")
        return 1
    
    logger.info(f"Found iTunes Movies folder: {movies_dir}")

    logger.info("Scanning iTunes Movies folder...")
    itunes_entries: List[Tuple[str, List[str]]] = list_itunes_movies(movies_dir)
    logger.info(f"Found {len(itunes_entries)} movies in iTunes")

    logger.info("Loading converted movie names...")
    converted_set_1, raw_set_1_lower = load_library_names(converted_folder)
    logger.info(f"Found {len(converted_set_1)} entries in converted folder")
    
    converted_set_2, raw_set_2_lower = load_library_names(movie_library_folder)
    logger.info(f"Found {len(converted_set_2)} entries in movie library folder")
    
    converted_sets: List[Set[str]] = [converted_set_1, converted_set_2]
    raw_sets_lower: List[Set[str]] = [raw_set_1_lower, raw_set_2_lower]

    logger.info("Comparing iTunes movies against converted libraries...")
    need_convert: List[str]
    converted_by_type: Dict[str, List[str]]
    need_convert, converted_by_type = find_unconverted(itunes_entries, converted_sets, raw_sets_lower)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Summary: {len(need_convert)} movies need conversion")
    logger.info(f"{'='*60}\n")

    # In debug mode, call debugMovies; otherwise call convertMovies
    if debug_mode:
        debugMovies(movies_dir, need_convert, converted_by_type, select_mode)
    else:
        convertMovies(movies_dir, need_convert, select_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
