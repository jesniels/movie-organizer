# Jellyfin Movie Organizer

A lightweight Python utility to organize loose movie files into individual folders based on existing Jellyfin `.nfo` metadata. 

It specifically solves the problem of having a flat directory full of `.mkv`, `.nfo`, and image files by grouping them into standard `Movie Title (Year)` folders and ensuring movie files are organized for Jellyfin.

## Features

*   **Metadata Integration:** Reads the `<year>` tag directly from your Jellyfin `.nfo` files.
*   **Intelligent Renaming & Cleanup:** Updates and moves associated files to their movie folder. The script now renames any moved `.nfo` to `movie.nfo` (Jellyfin convention) and will delete image files (`.jpg`, `.jpeg`, `.png`) in the source directory since Jellyfin will recreate them.
*   **Safe Execution:** Includes a `--dryrun` mode to preview changes before they are made.
*   **Skip Logic:** Automatically ignores movie files that lack a corresponding `.nfo` file to prevent incorrect folder creation.
*   **Non-Recursive:** Only processes the root of the input folder to prevent accidental modification of already organized subfolders.

## Example Transformation

**Before:**
```text
/Movies
  ├── Angel Has Fallen.mkv
  ├── Angel Has Fallen.nfo
  ├── Angel Has Fallen-poster.jpg
  └── Angel Has Fallen-fanart.jpg
```

**After Running Script:**
```text
/Movies
  └── Angel Has Fallen (2019)/
      ├── Angel Has Fallen (2019).mkv
      └── movie.nfo
      
    Note: poster/fanart image files are removed from the source during organization (JPG/PNG). Jellyfin will recreate thumbnails/posters when it scans the library.
```

## Usage

### Prerequisites
*   Python 3.6 or higher.
*   No external libraries required (uses standard Python library).

### Command Syntax
```bash
python py/movie-organizer.py "path/to/input" ["path/to/output"] [options]
```

### Arguments
| Argument | Description |
| :--- | :--- |
| `input` | The directory containing your loose movie files. |
| `output` | *(Optional)* The directory where folders should be created. Defaults to the input folder. |

### Options
| Flag | Description |
| :--- | :--- |
| `--dryrun` | Shows exactly what would happen without moving or renaming any files. |
| `--verbose` | Shows a detailed breakdown of every single file being processed. |

## Practical Examples

**Test your setup (Highly Recommended):**
```bash
python organize_movies.py "C:\Media\Unorganized" --dryrun
```

**Run the organization on the current folder:**
```bash
python organize_movies.py "C:\Media\Unorganized"
```

**Move and organize from one drive to another with detailed output:**
```bash
python organize_movies.py "D:\Downloads" "E:\Movies" --verbose
```

## How it handles filenames
The script uses the `.mkv` file as the "anchor." It looks for every other file in the directory that starts with the same name as the movie file and moves them into the new `Movie (Year)` folder. Behavior summary:

- `.nfo` files: moved into the movie folder and renamed to `movie.nfo`.
- Image files (`.jpg`, `.jpeg`, `.png`): deleted from the source (Jellyfin recreates them on scan).
- Other files: moved and renamed to include the year in the filename (e.g., `-trailer`, `-poster` suffixes preserved).

The script runs non-recursively on the given input folder by design.

## Web UI

A FastAPI-based web interface for browsing and managing the library:

```bash
python py/movie-organizer-ui.py
```

Then open `http://localhost:8998`. Library and download locations are configured in `web/config.yaml` (editable in the UI under Settings).

Key features:

*   **Library browser** with search, location/type filters, and clickable sidebar counts and status-dialog stat cards that filter the list.
*   **Move / Rename / Delete** with dry-run preview by default.
*   **Duplicate handling:** double-clicking a flagged duplicate opens a side-by-side comparison (metadata, NFO status, file sizes) with the option to copy NFO metadata between copies, or mark the pair as **Not a duplicate** (persisted in `web/not-duplicates.json` and kept up to date when items are moved).
*   **Play button** on every item — launches the file in the system's default video player.
*   **Missing episode detection** for series, with a sidebar filter.
