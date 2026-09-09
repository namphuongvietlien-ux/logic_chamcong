"""Resolve bundled data files for development and for PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> str:
    """Folder that contains app.py and the Excel templates (unfrozen)."""
    return str(Path(__file__).resolve().parent.parent)


def resource_roots() -> list[Path]:
    """Folders that may contain bundled Excel templates / EasyOCR models."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(str(meipass)))
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir)
        roots.append(exe_dir / "_internal")
    roots.append(Path(_project_root()))
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller.

    When PyInstaller bundles the app it unpacks data files into a folder
    stored on ``sys._MEIPASS`` (onefile temp dir, or onedir ``_internal``).
    Also checks the folder next to the .exe so a copied template still works.
    """
    rel = relative_path.replace("/", os.sep).lstrip("\\/")
    roots = resource_roots()
    for base_path in roots:
        candidate = Path(base_path) / rel
        if candidate.exists():
            return os.path.normpath(str(candidate))
    base = roots[0] if roots else _project_root()
    return os.path.normpath(os.path.join(str(base), rel))


def bundled_file(*relative_parts: str) -> Path | None:
    """Return the path if that bundled relative file exists."""
    path = Path(resource_path(os.path.join(*relative_parts)))
    return path if path.is_file() else None


def persistent_root() -> Path:
    """Folder that survives an exe update: next to AttendanceApp.exe, never ``_MEIPASS``.

    PyInstaller unpacks bundled read-only files into ``sys._MEIPASS``
    (onedir: ``_internal``, onefile: a temp dir wiped on exit). Writable
    data must live beside the .exe so replacing the exe / ``_internal``
    does not delete HR records.
    """
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(_project_root())
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            if root.resolve() == Path(str(meipass)).resolve():
                root = Path(sys.executable).resolve().parent
        except OSError:
            pass
    return root


def is_ephemeral_bundle_path(path: Path) -> bool:
    """True when *path* sits inside PyInstaller's extract folder (must not hold the DB)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass or not getattr(sys, "frozen", False):
        return False
    try:
        Path(path).resolve().relative_to(Path(str(meipass)).resolve())
        return True
    except (ValueError, OSError):
        return False


def get_db_path(db_name: str = "hr_system.db") -> Path:
    """Absolute SQLite path next to the .exe (or project root), never ``_MEIPASS``.

    Reuses an existing database so a version upgrade does not start empty:
    ``data/tas.db`` (older builds), then ``tas.db`` / ``hr_system.db`` beside the exe.
    New installs create ``hr_system.db`` next to ``AttendanceApp.exe``.
    """
    root = persistent_root()
    candidates = (
        root / "data" / "tas.db",
        root / "tas.db",
        root / "hr_system.db",
        root / db_name,
    )
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if (
            candidate.is_file()
            and candidate.stat().st_size > 0
            and not is_ephemeral_bundle_path(resolved)
        ):
            return resolved
    dest = (root / db_name).resolve()
    if is_ephemeral_bundle_path(dest):
        dest = (Path(sys.executable).resolve().parent / db_name).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def writable_dir() -> Path:
    """Persistent folder next to the .exe (frozen) or the project (dev).

    Never write the SQLite database into ``_MEIPASS`` — that folder is
    temporary / read-only in a bundled build.
    """
    dest = persistent_root() / "data"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def database_path() -> Path:
    return get_db_path()
