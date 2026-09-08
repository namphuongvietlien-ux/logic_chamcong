"""Create employee photo folders and delete last month's images after a successful run."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Optional

from processing.master_data import load_master_data
from processing.utils import name_match_key

LogFn = Optional[Callable[[str], None]]

DELETE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_folder_name(name: str) -> str:
    cleaned = _INVALID_CHARS.sub(" ", str(name or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "NV"


def list_image_employee_names(images_folder: str | Path | None) -> list[str]:
    """Subfolder names under Images/ are the employee names (not OCR text)."""
    if not images_folder:
        return []
    root = Path(images_folder)
    if not root.exists() or not root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            names.append(child.name)
    return names


def create_employee_folders(
    master_path: str | Path | None,
    images_folder: str | Path | None,
    log: LogFn = None,
) -> dict[str, int]:
    """Ensure Images/<Employee Name>/ exists for every name in the SQLite roster."""
    if not images_folder:
        raise ValueError("Hãy chọn thư mục Images.")
    master = load_master_data(master_path, log=log)
    if master is None or master.empty:
        raise ValueError("Chưa có nhân viên trong CSDL. Mở thẻ Nhân viên để thêm hoặc nhập Excel.")
    root = Path(images_folder)
    os.makedirs(root, exist_ok=True)

    existing: dict[str, Path] = {}
    for child in root.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            existing[name_match_key(child.name)] = child

    created = 0
    skipped = 0
    for raw in master["employee_name"].tolist():
        name = str(raw or "").strip()
        if not name:
            continue
        key = name_match_key(name)
        if key in existing:
            skipped += 1
            continue
        folder = root / _safe_folder_name(name)
        os.makedirs(folder, exist_ok=True)
        existing[key] = folder
        created += 1
        if log:
            log(f"Tạo thư mục: {folder.name}")

    if log:
        log(
            f"Thư mục nhân viên: tạo mới {created}, đã có {skipped}, "
            f"tổng {created + skipped} trong {root}"
        )
    return {"created": created, "existed": skipped, "total": created + skipped}


def delete_employee_images(images_folder: str | Path | None, log: LogFn = None) -> dict[str, int]:
    """Delete .jpg/.png inside employee subfolders. Keep the folders themselves."""
    if not images_folder:
        raise ValueError("Chưa chọn thư mục Images.")
    root = Path(images_folder)
    if not root.exists() or not root.is_dir():
        if log:
            log("Không thấy thư mục ảnh — bỏ qua xóa ảnh.")
        return {"deleted": 0, "failed": 0}
    deleted = 0
    failed = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for file in sorted(child.rglob("*")):
            if not file.is_file() or file.suffix.lower() not in DELETE_SUFFIXES:
                continue
            try:
                os.remove(file)
                deleted += 1
            except OSError as exc:
                failed += 1
                if log:
                    log(f"Không xóa được (file đang mở?): {file.name} — {exc}")
    if log:
        log(f"Đã xóa {deleted} ảnh cũ trong thư mục nhân viên (giữ lại folder rỗng).")
        if failed:
            log(f"Không xóa được {failed} file (có thể đang mở).")
    return {"deleted": deleted, "failed": failed}
