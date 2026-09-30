import os
import shutil
import argparse
from pathlib import Path

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

    input_files = [*input_dir.glob("*.mkv"), *input_dir.glob("*.mp4")]
    no_nfo_files = []
    skipped_existing = []

    print(f"{'--- DRY RUN ENABLED ---' if args.dryrun else '--- STARTING ORGANIZATION ---'}")

    for mkv_path in input_files:
        base_name = mkv_path.stem  # e.g., "Angel Has Fallen (2019)" or "xXx_ Return of Xander Cage (1080p HD)"
        nfo_path = mkv_path.with_suffix(".nfo")

        # Require .nfo file to exist - skip if missing
        if not nfo_path.exists():
            no_nfo_files.append(mkv_path.name)
            continue

        # Use the filename exactly as-is for the folder name
        new_folder_name = base_name

        target_folder = output_dir / new_folder_name

        # Check if target folder already exists
        if target_folder.exists():
            print(f"Skipping: '{base_name}' - folder already exists")
            skipped_existing.append(base_name)
            continue

        print(f"Processing: '{base_name}' -> '{new_folder_name}'")

        # Related files: exact stem, or stem followed by a sidecar separator ("Movie.en.srt", "Movie-poster.jpg").
        # A plain prefix match would also grab "Aliens.mkv" when organizing "Alien.mkv".
        related_files = [f for f in input_dir.iterdir() if f.is_file() and (
            f.stem == base_name or f.name.startswith(base_name + ".") or f.name.startswith(base_name + "-"))]

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

            # Default behavior: move files with their ORIGINAL names - don't rename them
            dest_path = target_folder / file.name

            if args.verbose:
                print(f"  [File] {file.name} -> {file.name}")

            if not args.dryrun:
                shutil.move(str(file), str(dest_path))

    if no_nfo_files:
        print("\n--- SKIPPED (No .nfo file found) ---")
        for missed in no_nfo_files:
            print(f"MISSING NFO: {missed}")

    if skipped_existing:
        print("\n--- SKIPPED (Folder already exists) ---")
        for skipped in skipped_existing:
            print(f"ALREADY EXISTS: {skipped}")

    print("\nDone.")

if __name__ == "__main__":
    main()

    