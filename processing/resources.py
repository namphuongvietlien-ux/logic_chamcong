"""Resolve bundled data files for development and for PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> str:
    """Folder that contains app.py and the Excel templates (unfrozen)."""
    return str(Path(__file__).resolve().parent.parent)


def resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller.

    When PyInstaller bundles the app it unpacks data files into a folder
    stored on ``sys._MEIPASS`` (onefile temp dir, or onedir ``_internal``).
    """
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        base_path = _project_root()
    return os.path.normpath(os.path.join(str(base_path), relative_path))


def bundled_file(*relative_parts: str) -> Path | None:
    """Return the path if that bundled relative file exists."""
    path = Path(resource_path(os.path.join(*relative_parts)))
    return path if path.is_file() else None


def writable_dir() -> Path:
    """Persistent folder next to the .exe (frozen) or the project (dev).

    Never write the SQLite database into ``_MEIPASS`` — that folder is
    temporary / read-only in a bundled build.
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(_project_root())
    dest = root / "data"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def database_path() -> Path:
    return writable_dir() / "tas.db"
