#!/usr/bin/env python3
"""
Movie Organizer Web UI — entry point.
Run from project root:  python py/movie-organizer-ui.py
Application code lives in the py/ui/ package.
"""
import uvicorn

from ui.app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8998)
