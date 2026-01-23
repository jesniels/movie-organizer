import os
import shutil
import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path

def get_movie_year(nfo_path):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        year = root.findtext('year')
        return year if year else "Unknown"
    except Exception:
        return "Unknown"

def main():
    parser = argparse.ArgumentParser(description="Organize movies into folders based on Jellyfin NFO metadata.")
    parser.add_argument("input", help="Folder containing the loose movie files")
    parser.add_argument("output", nargs="?", help="Destination folder (defaults to input folder)")
    parser.add_argument("--dryrun", action="store_true", help="Show what would happen without moving files")
    parser.add_argument("--verbose", action="store_true", help="Show details of every file moved/renamed")

    args = parser.parse_args()
    
    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve() if args.output else input_dir
    
    if not input_dir.is_dir():
        print(f"Error: {input_dir} is not a valid directory.")
        return

    mkv_files = list(input_dir.glob("*.mkv"))
    no_nfo_files = []

    print(f"{'--- DRY RUN ENABLED ---' if args.dryrun else '--- STARTING ORGANIZATION ---'}")

    for mkv_path in mkv_files:
        base_name = mkv_path.stem  # e.g., "Angel Has Fallen" or "Angel Has Fallen (2019)"
        nfo_path = mkv_path.with_suffix(".nfo")

        # If filename already ends with " (YYYY)", use that year and skip reading the .nfo
        m = re.search(r" \((\d{4})\)$", base_name)
        if m:
            year = m.group(1)
            new_folder_name = base_name
        else:
            if not nfo_path.exists():
                no_nfo_files.append(mkv_path.name)
                continue
            # Extract year from NFO
            year = get_movie_year(nfo_path)
            new_folder_name = f"{base_name} ({year})"

        target_folder = output_dir / new_folder_name

        print(f"Processing: '{base_name}' -> '{new_folder_name}'")

        # Find all related files (mkv, nfo, jpg, png, etc.)
        # This matches anything starting with the same filename
        related_files = [f for f in input_dir.iterdir() if f.name.startswith(base_name) and f.is_file()]

        if not args.dryrun:
            target_folder.mkdir(parents=True, exist_ok=True)

        for file in related_files:
            ext = file.suffix.lower()

            # Delete image files (JPG/PNG) — Jellyfin will recreate them
            if ext in ('.jpg', '.jpeg', '.png'):
                if args.verbose:
                    print(f"  [Delete] {file.name}")
                if not args.dryrun:
                    try:
                        file.unlink()
                    except Exception as e:
                        print(f"    Error deleting {file}: {e}")
                continue

            # Move and rename the .nfo to movie.nfo inside the folder
            if ext == '.nfo':
                dest_path = target_folder / 'movie.nfo'
                if args.verbose:
                    print(f"  [NFO] {file.name} -> movie.nfo")
                if not args.dryrun:
                    shutil.move(str(file), str(dest_path))
                continue

            # Default behavior: move other files, prefixing folder name into filename
            suffix_part = file.name[len(base_name):]
            new_file_name = f"{base_name} ({year}){suffix_part}"
            dest_path = target_folder / new_file_name

            if args.verbose:
                print(f"  [File] {file.name} -> {new_file_name}")

            if not args.dryrun:
                shutil.move(str(file), str(dest_path))

    if no_nfo_files:
        print("\n--- SKIPPED (No .nfo file found) ---")
        for missed in no_nfo_files:
            print(f"MISSING NFO: {missed}")

    print("\nDone.")

if __name__ == "__main__":
    main()

    