"""Orchestrate fingerprint Excel + photo OCR + merge + Excel export."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from processing.excel_processor import load_fingerprint_data
from processing.folders import delete_employee_images
from processing.master_data import load_master_data
from processing.merger import merge_attendance
from processing.ocr_processor import load_photo_attendance
from processing.reports import export_reports
from processing.validation import validate_against_master

LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int, str], None]]
MissingFn = Optional[Callable[[list[str], str], None]]


def parse_work_start(value: str) -> time:
    text = (value or "08:00").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError("Giờ vào chuẩn phải dạng HH:MM, ví dụ 08:00")


def run_pipeline(
    excel_path: str | Path,
    images_folder: str | Path | None,
    output_dir: str | Path,
    work_start: time | str = time(8, 0),
    skip_ocr: bool = False,
    master_path: str | Path | None = None,
    log: LogFn = None,
    progress: ProgressFn = None,
    delete_images: bool = False,
    on_missing_master: MissingFn = None,
) -> dict:
    if isinstance(work_start, str):
        work_start = parse_work_start(work_start)
    if log:
        log("=== Bắt đầu phân tích chấm công ===")
    from processing.holidays import ensure_holiday_years

    ensure_holiday_years()
    master = load_master_data(master_path, log=log)
    fingerprint_df = load_fingerprint_data(excel_path, log=log)
    from processing.database import MonthLockedError, is_month_locked, month_key, persist_attendance_from_merged
    from processing.period import detect_report_period

    year, month = detect_report_period(excel_path, fingerprint_df)
    key = month_key(year, month)
    if is_month_locked(key):
        message = f"Kỳ {key} đã chốt công. Không chạy phân tích — dữ liệu tháng này được khóa."
        if log:
            log(message)
        raise MonthLockedError(message)
    if log:
        log(f"Kỳ chấm công: {key}")
    validate_against_master(
        fingerprint_df,
        images_folder,
        master,
        log=log,
        on_missing=on_missing_master,
    )

    photo_df = pd.DataFrame(columns=["employee_name", "date", "photo_in", "photo_out"])
    ocr_log = pd.DataFrame()
    if skip_ocr:
        if log:
            log("Bỏ qua OCR theo tùy chọn.")
    elif images_folder and Path(images_folder).exists():
        photo_df, ocr_log = load_photo_attendance(images_folder, log=log, progress=progress)
    else:
        if log:
            log("Không có thư mục ảnh — chỉ dùng dữ liệu vân tay.")

    merged = merge_attendance(
        fingerprint_df, photo_df, work_start=work_start, master=master, log=log
    )
    persist_attendance_from_merged(merged, year, month)
    if log:
        log(f"Đã ghi CSDL chấm công kỳ {key} ({len(merged)} dòng).")
    paths = export_reports(
        merged,
        output_dir,
        work_start=work_start,
        ocr_log=ocr_log,
        log=log,
        source_excel=excel_path,
        fingerprint_df=fingerprint_df,
    )
    if delete_images:
        delete_employee_images(images_folder, log=log)
    if log:
        log("=== Hoàn tất ===")
    return {
        "paths": paths,
        "merged": merged,
        "fingerprint": fingerprint_df,
        "photo": photo_df,
        "master": master,
    }
