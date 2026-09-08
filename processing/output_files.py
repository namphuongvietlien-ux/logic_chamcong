"""Overwrite monthly Excel outputs instead of appending to last month's files."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

LogFn = Optional[Callable[[str], None]]


def replace_existing(path: str | Path, log: LogFn = None) -> Path:
    """Delete an existing workbook so this month fully replaces the previous file."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        try:
            dest.unlink()
            if log:
                log(f"Ghi đè file cũ: {dest.name}")
        except OSError as exc:
            raise PermissionError(
                f"Không ghi đè được {dest.name}. Hãy đóng file Excel rồi chạy lại.\n{exc}"
            ) from exc
    return dest
