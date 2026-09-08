"""Detect employees present in attendance sources but missing from master data."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

import pandas as pd

from processing.folders import list_image_employee_names
from processing.master_data import is_in_master
from processing.utils import name_match_key

LogFn = Optional[Callable[[str], None]]
MissingFn = Optional[Callable[[list[str], str], None]]


def names_from_fingerprint(fingerprint_df: pd.DataFrame | None) -> list[str]:
    if fingerprint_df is None or getattr(fingerprint_df, "empty", True):
        return []
    if "employee_name" not in fingerprint_df.columns:
        return []
    names: list[str] = []
    for raw in fingerprint_df["employee_name"].dropna().unique():
        name = str(raw).strip()
        if name:
            names.append(name)
    return names


def collect_detected_names(
    fingerprint_df: pd.DataFrame | None,
    images_folder: str | Path | None,
) -> list[str]:
    """Unique employee names from fingerprint Excel + Images subfolder names."""
    collected: list[str] = []
    collected.extend(names_from_fingerprint(fingerprint_df))
    collected.extend(list_image_employee_names(images_folder))
    return _unique_by_key(collected)


def find_missing_employees(
    detected_names: Iterable[str],
    master: pd.DataFrame | None,
) -> list[str]:
    """detected_names − master names (accent/spacing-insensitive via name_match_key)."""
    missing: list[str] = []
    seen: set[str] = set()
    for raw in detected_names:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name_match_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        if is_in_master(master, name):
            continue
        missing.append(name)
    missing.sort(key=lambda n: name_match_key(n) or n.lower())
    return missing


def format_missing_master_alert(missing: list[str]) -> str:
    n = len(missing)
    listed = ", ".join(missing)
    return (
        f"CẢNH BÁO: Phát hiện dữ liệu chấm công của {n} nhân viên nhưng không có "
        f"trong Master Data: {listed}. Vui lòng bổ sung để tính công chính xác!"
    )


def validate_against_master(
    fingerprint_df: pd.DataFrame | None,
    images_folder: str | Path | None,
    master: pd.DataFrame | None,
    log: LogFn = None,
    on_missing: MissingFn = None,
) -> list[str]:
    """Compare fingerprint + folder names to master. Alert before OCR/grouping.

    Missing employees are still processed later with 8h shift and 0 lunch.
    """
    detected = collect_detected_names(fingerprint_df, images_folder)
    if log:
        log(
            f"Đối chiếu master: {len(detected)} tên từ vân tay + thư mục ảnh "
            f"(tên ảnh = tên subfolder, không OCR tên tiếng Việt)."
        )
    missing = find_missing_employees(detected, master)
    if not missing:
        if log:
            log("Mọi nhân viên có dữ liệu chấm công đều có trong Master Data.")
        return []
    message = format_missing_master_alert(missing)
    if log:
        log(message)
        log("Vẫn xử lý dữ liệu thô; ca mặc định 8 giờ, không trừ nghỉ trưa cho các NV này.")
    if on_missing:
        on_missing(missing, message)
    return missing


def _unique_by_key(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name_match_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out
