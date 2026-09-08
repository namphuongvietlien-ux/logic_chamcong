"""Module 4: export detailed daily and monthly payroll Excel reports."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from processing.corrections import save_working_table
from processing.excel_locale import excel_formula
from processing.leave import REMARK_PAID, REMARK_PAID_HALF, REMARK_UNPAID, REMARK_UNPAID_HALF
from processing.output_files import replace_existing
from processing.period import detect_report_period, filter_to_month
from processing.report_cham_cong import export_cham_cong_thang
from processing.report_individual import export_individual_detail
from processing.report_k9 import export_k9_daily
from processing.utils import format_date, format_time

LogFn = Optional[Callable[[str], None]]

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=11)
TITLE_FONT = Font(bold=True, color="1F4E79", name="Calibri", size=16)
SUB_FONT = Font(italic=True, color="5B5B5B", name="Calibri", size=10)
CELL_FONT = Font(name="Calibri", size=11)
ALT_FILL = PatternFill("solid", fgColor="D6EAF8")
LATE_FILL = PatternFill("solid", fgColor="F5B7B1")
MISSING_FILL = PatternFill("solid", fgColor="F9E79F")
THIN = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)


def _autosize(ws, min_width: int = 12, max_width: int = 42) -> None:
    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        length = 0
        for cell in column:
            if cell.value is None:
                continue
            length = max(length, len(str(cell.value)))
        ws.column_dimensions[letter].width = min(max(length + 3, min_width), max_width)


def _style_header(ws, row: int, col_count: int) -> None:
    for col in range(1, col_count + 1):
        cell = ws.cell(row, col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _write_table(ws, start_row: int, headers: list[str], records: list[list]) -> None:
    for col, header in enumerate(headers, start=1):
        ws.cell(start_row, col, header)
    _style_header(ws, start_row, len(headers))
    for ridx, record in enumerate(records, start=start_row + 1):
        for cidx, value in enumerate(record, start=1):
            cell = ws.cell(ridx, cidx, value)
            cell.font = CELL_FONT
            cell.border = THIN
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if ridx % 2 == 0:
                cell.fill = ALT_FILL
        notes = str(record[-1] if record else "")
        if "Late" in notes:
            ws.cell(ridx, len(headers)).fill = LATE_FILL
        elif "Missing" in notes:
            ws.cell(ridx, len(headers)).fill = MISSING_FILL
    last_row = start_row + len(records)
    last_col = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A{start_row}:{last_col}{max(last_row, start_row)}"
    ws.freeze_panes = f"A{start_row + 1}"
    ws.row_dimensions[start_row].height = 28
    _autosize(ws)
    return last_row


def _write_leave_totals(ws, header_row: int, last_data: int, remarks_col: int, cong_col: int) -> None:
    """COUNTIF / SUMIF footers with ';' so Excel VN/EU parses them correctly."""
    if last_data <= header_row:
        return
    remarks = f"{get_column_letter(remarks_col)}{header_row + 1}:{get_column_letter(remarks_col)}{last_data}"
    cong = f"{get_column_letter(cong_col)}{header_row + 1}:{get_column_letter(cong_col)}{last_data}"
    start = last_data + 2
    lines = (
        (f'Ngày nghỉ không lương', excel_formula("COUNTIF", remarks, f'"{REMARK_UNPAID}"')),
        (f'Ngày phép năm', excel_formula("COUNTIF", remarks, f'"{REMARK_PAID}"')),
        (f'Nửa ngày phép (số dòng)', excel_formula("COUNTIF", remarks, f'"{REMARK_PAID_HALF}"')),
        (f'Nửa ngày KL (số dòng)', excel_formula("COUNTIF", remarks, f'"{REMARK_UNPAID_HALF}"')),
        (f'Tổng công nửa ngày phép', excel_formula("SUMIF", remarks, f'"{REMARK_PAID_HALF}"', cong)),
        (f'Tổng công nửa ngày KL', excel_formula("SUMIF", remarks, f'"{REMARK_UNPAID_HALF}"', cong)),
    )
    for offset, (label, formula) in enumerate(lines):
        ws.cell(start + offset, 1, label).font = SUB_FONT
        cell = ws.cell(start + offset, 2, formula)
        cell.font = CELL_FONT
        cell.number_format = "0.00"


def _period_label(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    days = sorted(df["date"].dropna().unique())
    if not days:
        return ""
    return f"{format_date(days[0])} → {format_date(days[-1])}"


def build_daily_rows(merged: pd.DataFrame) -> list[list]:
    rows = []
    for _, row in merged.iterrows():
        notes = row.get("notes")
        if notes is None or (isinstance(notes, float) and notes != notes):
            notes = ""
        rows.append(
            [
                format_date(row.get("date")),
                "" if row.get("employee_name") is None else str(row.get("employee_name")),
                row.get("standard_shift_hours") if row.get("standard_shift_hours") is not None else "",
                format_time(row.get("final_check_in")),
                format_time(row.get("final_check_out")),
                row.get("deducted_lunch_hours") if row.get("deducted_lunch_hours") is not None else "",
                row.get("actual_work_hours") if row.get("actual_work_hours") is not None else "",
                row.get("standardized_workday") if row.get("standardized_workday") is not None else "",
                row.get("overtime_hours") if row.get("overtime_hours") is not None else "",
                str(notes),
            ]
        )
    return rows


def build_monthly_summary(merged: pd.DataFrame, work_start: time) -> pd.DataFrame:
    columns = [
        "Employee Name",
        "Total Standardized Workdays",
        "Total Late Arrivals",
        "Total Missing Punches",
        "Remarks",
    ]
    if merged.empty:
        return pd.DataFrame(columns=columns)

    df = merged.copy()

    if "late_minutes" in df.columns:
        df["is_late"] = pd.to_numeric(df["late_minutes"], errors="coerce").fillna(0) > 0
    else:
        def _is_late(row) -> bool:
            t = row.get("final_check_in")
            start = row.get("shift_start")
            if t is None or (isinstance(t, float) and pd.isna(t)):
                return False
            if start is None or (isinstance(start, float) and pd.isna(start)):
                return bool(t > work_start)
            return bool(t > start)

        df["is_late"] = df.apply(_is_late, axis=1)
    df["is_missing"] = df["final_check_in"].isna() ^ df["final_check_out"].isna()
    df["std_days"] = pd.to_numeric(df.get("standardized_workday"), errors="coerce").fillna(0)
    grouped = df.groupby("employee_name", dropna=False)
    summary = pd.DataFrame(
        {
            "Employee Name": grouped["employee_name"].first(),
            "Total Standardized Workdays": grouped["std_days"].sum().round(2),
            "Total Late Arrivals": grouped["is_late"].sum().astype(int),
            "Total Missing Punches": grouped["is_missing"].sum().astype(int),
        }
    ).reset_index(drop=True)

    def remarks(row: pd.Series) -> str:
        parts = []
        if row["Total Late Arrivals"]:
            parts.append(f"{int(row['Total Late Arrivals'])} late arrival(s)")
        if row["Total Missing Punches"]:
            parts.append(f"{int(row['Total Missing Punches'])} missing punch(es)")
        return "; ".join(parts) if parts else "OK"

    summary["Remarks"] = summary.apply(remarks, axis=1)
    return summary[columns].sort_values("Employee Name")


def export_reports(
    merged: pd.DataFrame,
    output_dir: str | Path,
    work_start: time = time(8, 0),
    ocr_log: Optional[pd.DataFrame] = None,
    log: LogFn = None,
    source_excel: str | Path | None = None,
    fingerprint_df: Optional[pd.DataFrame] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if year is None or month is None:
        year, month = detect_report_period(source_excel, fingerprint_df, merged)
    month_data = filter_to_month(merged, year, month)
    skipped = 0 if merged is None else len(merged) - len(month_data)
    if log:
        source_label = Path(source_excel).name if source_excel else "dữ liệu chấm công"
        log(f"Kỳ báo cáo: {month:02d}/{year} (theo file nguồn {source_label}).")
        if skipped:
            log(
                f"Bỏ {skipped} dòng ngoài tháng {month:02d}/{year} "
                "(ảnh OCR hoặc ngày không thuộc file vân tay)."
            )
    if month_data is not None and not month_data.empty:
        merged = month_data
    elif log:
        log(
            f"Không có dòng chấm công trong tháng {month:02d}/{year} — "
            "vẫn đặt tên file theo tháng của file nguồn."
        )

    period = _period_label(merged)
    suffix = f"{year:04d}-{month:02d}"

    daily_headers = [
        "Date",
        "Employee Name",
        "Standard Shift (h)",
        "Final Check-In",
        "Final Check-Out",
        "Deducted Lunch Time (h)",
        "Actual Work Hours",
        "Standardized Workday (Day count)",
        "Overtime Hours (Tăng ca)",
        "Remarks",
    ]
    daily_rows = build_daily_rows(merged)
    summary = build_monthly_summary(merged, work_start)

    daily_path = replace_existing(output / f"Bao_cao_chi_tiet_tung_ngay_{suffix}.xlsx", log=log)
    monthly_path = replace_existing(output / f"Bao_cao_tong_hop_thang_{suffix}.xlsx", log=log)

    daily_wb = Workbook()
    ws = daily_wb.active
    ws.title = "Daily Attendance"
    ws.merge_cells("A1:J1")
    ws["A1"] = "Báo cáo chi tiết từng ngày / Detailed Daily Attendance"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A2:J2")
    ws["A2"] = (
        f"Period: {period}  |  Missing IN/OUT pair → 0 giờ / 0 công  |  "
        "Công = Giờ / ca chuẩn (≥0.85→1, ≥0.45→0.5)  |  Tăng ca = max(0; Giờ − ca chuẩn)"
    )
    ws["A2"].font = SUB_FONT
    last_daily = _write_table(ws, 4, daily_headers, daily_rows)
    _write_leave_totals(ws, 4, last_daily, remarks_col=10, cong_col=8)
    daily_wb.save(daily_path)

    monthly_wb = Workbook()
    ws2 = monthly_wb.active
    ws2.title = "Monthly Summary"
    ws2.merge_cells("A1:E1")
    ws2["A1"] = "Báo cáo tổng hợp tháng để tính lương / Monthly Summary"
    ws2["A1"].font = TITLE_FONT
    ws2.merge_cells("A2:E2")
    ws2["A2"] = (
        f"Period: {period}  |  Late if Final Check-In > Shift Start (master)  "
        "|  Missing punch = only In or only Out"
    )
    ws2["A2"].font = SUB_FONT
    monthly_headers = [
        "Employee Name",
        "Total Standardized Workdays",
        "Total Late Arrivals",
        "Total Missing Punches",
        "Remarks",
    ]
    monthly_rows = summary[monthly_headers].values.tolist() if not summary.empty else []
    _write_table(ws2, 4, monthly_headers, monthly_rows)
    monthly_wb.save(monthly_path)

    extra: dict[str, Path] = {}
    if ocr_log is not None and not ocr_log.empty:
        log_path = replace_existing(output / f"OCR_log_{suffix}.xlsx", log=log)
        ocr_wb = Workbook()
        ws3 = ocr_wb.active
        ws3.title = "OCR Log"
        for r_idx, row in enumerate(dataframe_to_rows(ocr_log, index=False, header=True), start=1):
            for c_idx, value in enumerate(row, start=1):
                cell = ws3.cell(r_idx, c_idx, "" if value is None else str(value))
                if r_idx == 1:
                    cell.fill = HEADER_FILL
                    cell.font = HEADER_FONT
        _autosize(ws3, min_width=14, max_width=70)
        ocr_wb.save(log_path)
        extra["ocr_log"] = log_path

    cham_path = replace_existing(output / f"CHAM_CONG_THANG_{year:04d}-{month:02d}.xlsx", log=log)
    k9_path = replace_existing(output / f"K9_{month:02d}.{year}.xlsx", log=log)
    cham = export_cham_cong_thang(merged, cham_path, log=log, year=year, month=month)
    if cham is not None:
        extra["cham_cong"] = cham
    extra["k9"] = export_k9_daily(merged, k9_path, log=log, year=year, month=month)
    chi_tiet_path = replace_existing(output / f"Chi_Tiet_Cham_Cong_{month:02d}.{year}.xlsx", log=log)
    extra["chi_tiet"] = export_individual_detail(merged, chi_tiet_path, log=log)

    working_path = replace_existing(output / f"Du_lieu_sua_gio_{year:04d}-{month:02d}.xlsx", log=log)
    extra["working"] = save_working_table(merged, working_path, log=log)

    if log:
        log(f"Đã ghi: {daily_path.name}")
        log(f"Đã ghi: {monthly_path.name}")
    return {"daily": daily_path, "monthly": monthly_path, **extra}
