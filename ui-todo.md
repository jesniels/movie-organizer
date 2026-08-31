# Web UI Development Plan: Movie Organizer Web

This document outlines the tasks required to transform the current CLI-based Movie Organizer into a modern web application using Python.

## Architectural Overview
- **Backend**: Python HTTP Application (FastAPI or Flask recommended).
- **Frontend**: Responsive HTML/JS (Bootstrap or TailWind for UI).
- **Configuration**: YAML file for persistence and settings.
- **Scanning**: Background worker/thread for library updates.

---

## 1. Environment & Project Setup
- [x] **Configure Virtual Environment**: Ensure `venv/` is active and used for all development.
- [x] **Confirm Requirements**: Request approval for additional libraries:
  - `fastapi` & `uvicorn` (or `flask`) for the HTTP server.
  - `PyYAML` for configuration management.
  - `jinja2` for templating functionality.
  - `pydantic` for data validation.
  - `ffmpeg-python` for transcoding integration.
- [x] **Create Project Structure**:
  - `py/movie-organizer-ui.py`: Main entry point.
  - `web/config.yaml`: Configuration and library cache.
  - `web/templates/`: HTML templates.
  - `web/static/`: CSS/JS assets.
  - `web/cache/`: Application cache.

## 2. Configuration & Data Layer
- [ ] **YAML Schema Definition**:
  - `locations`: List of NAS movie/series paths.
  - `downloads`: List of download directories.
  - `library_cache`: Store previously scanned data (filenames, folder names, NFO metadata).
- [ ] **Config Utility**: 
  - Create functions to read the YAML config
  - Functionality to maintain the library cache for web app in `web/cache/`

## 3. Library Scanner (Background)
- [ ] **Unified Scanner**:
  - Implement recursive scanning for download folders.
  - Implement recursive scanning for library locations.
  - Logic to parse Jellyfin `.nfo` files for metadata (Title, Year, movie names, etc.).
- [ ] **Background Execution**:
  - Run scan on startup.
  - Detect changes compared to `library_cache` in YAML.
  - Implement "Notify and Update" mechanism in the UI when changes are found.
  - Support manual "Rescan" trigger from the UI.
- [ ] **Rename assistant**
  - Settings window where we can configure directory and file name structyre (year, name, format, etc)
  - Rename support that shows files that could be renamed based on the naming convention in settings
  - these files showed under libray and "show only" as "Rename assist"
  - Allow for bulk renaming (making multiple files)
  - Rename button should show proposal based on NFO information and more - IF AVAILABLE; if not, show nothing and let user decide
- [ ] **NFO details**
  - SHows text
  - Shows pictures below - more pictures, show them in a panel, left to right. Let us scroll through them if there are many.
    Keep pictures at small height.
  - When opening a series, it shows seasons as icons below.. SUPER. Clicking on a season should show the files below
  - clicking on an eposide showed then clicking on a season, should updatre info at the top - for that episode.
  - It should have marking next to each episode for "NFO" or "crossed out NFO" (if incomplete)
  
## 4. Web UI Features (movie-organizer-ui.py)
- [ ] **Dashboard Layout**:
  - **Top Bar**: Global actions (Rescan, Transcode Queue, Settings info).
  - **Filters**: Checkboxes for "Library", "Download", "Movies", "Series".
  - **Search Bar**: Quick search across Filenames, Folders, and NFO content.
- [ ] **Movie/Series List**:
  - Multi-select support for bulk actions.
  - Labels indicating "In Downloads" vs "In Library".
  - **Detail View**: 
    - Movies: Show status, path, NFO metadata.
    - Series: Group by Name/Season with episode number (start, end, missing in red/orange).
- [ ] **Status View**: 
  - Number of movies and series in library and download
  - Number of episodes in series
  - Potential duplicates in movies or series - click here should show which
  - Potential missing series episodes - click here should show which
- [ ] **Transcoding Module (New) - LAST IMPLEMENTATION - MORE REQUIREMENTS ADDED LATER**:
  - Detection logic for movies needing transcoding (based on metadata or user-defined rules).
  - UI visibility for "Can be transcoded".
  - Confirmation prompt for "Needs transcoding?".

## 5. Operations & Actions
FOR ALL CHANGING ACTIONS - possibility to "dry run" where it will show in the UI what it will do - so it can be "tested" BEFORE actually doing it 
- [ ] **Move Action**: Logic to move files from Download to Library relative paths.
- [ ] **Rename Action**: Integrated renaming logic from current CLI.
- [ ] **Delete Action**: 
  - Mandatory confirmation dialog.
  - Display summary of selected items (Count, Names).
- [ ] **Transcode Execution**: Integration with tools like FFmpeg to process files.

## 6. Integration & Testing
- [ ] **Port CLI Logic**: Reuse logic from `movie-organizer.py` for file movements and NFO parsing.
- [ ] **Web Validation**: Ensure the HTTP application handles concurrent scans and file operations safely.
- [ ] **UI Refresh**: Ensure UI updates smoothly when background scans complete.
