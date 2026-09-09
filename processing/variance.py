"""Month-over-month workday (công) variance for the HR dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from processing.database import connect, init_db, previous_month_key
from processing.excel_locale import excel_formula  # SUM(range) is one arg; IF uses ';' below
from processing.utils import employee_id_sort_key

NOTE_NEW = "Nhân sự mới / Đi làm lại"
NOTE_GONE = "Nghỉ việc / Không phát sinh công"


def _month_cong(month_year: str) -> pd.DataFrame:
    """Aggregate standardized_workday per employee for one month."""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                a.name_key AS name_key,
                COALESCE(MAX(e.employee_id), '') AS employee_id,
                COALESCE(MAX(e.employee_name), MAX(a.employee_name), a.name_key) AS employee_name,
                COALESCE(SUM(a.standardized_workday), 0) AS workdays
            FROM attendance_days a
            LEFT JOIN employees e ON e.name_key = a.name_key
            WHERE a.month_year = ?
            GROUP BY a.name_key
            """,
            (month_year,),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["name_key", "employee_id", "employee_name", "workdays"])
    frame = pd.DataFrame([dict(row) for row in rows])
    frame["workdays"] = frame["workdays"].astype(float)
    return frame


def mom_variance(target_month: str, previous_month: Optional[str] = None) -> pd.DataFrame:
    """Join this month vs previous month on name_key; expose Employee_ID for the grid."""
    prev_key = previous_month or previous_month_key(target_month)
    current = _month_cong(target_month).rename(columns={"workdays": "cong_now"})
    previous = _month_cong(prev_key).rename(columns={"workdays": "cong_prev"})
    if current.empty and previous.empty:
        return pd.DataFrame(
            columns=[
                "employee_id",
                "employee_name",
                "name_key",
                "cong_prev",
                "cong_now",
                "variance",
                "note",
                "target_month",
                "previous_month",
            ]
        )
    merged = current.merge(previous, on="name_key", how="outer", suffixes=("_now", "_prev"))
    merged["employee_id"] = merged["employee_id_now"].fillna("").replace("", pd.NA)
    merged["employee_id"] = merged["employee_id"].fillna(merged["employee_id_prev"].fillna(""))
    merged["employee_name"] = merged["employee_name_now"].fillna("").replace("", pd.NA)
    merged["employee_name"] = merged["employee_name"].fillna(merged["employee_name_prev"].fillna(""))
    merged["cong_now"] = merged["cong_now"].fillna(0.0).astype(float)
    merged["cong_prev"] = merged["cong_prev"].fillna(0.0).astype(float)
    merged["variance"] = (merged["cong_now"] - merged["cong_prev"]).round(2)

    def _note(row) -> str:
        now = float(row["cong_now"])
        prev = float(row["cong_prev"])
        if now > 0 and prev == 0:
            return NOTE_NEW
        if prev > 0 and now == 0:
            return NOTE_GONE
        return ""

    merged["note"] = merged.apply(_note, axis=1)
    merged["target_month"] = target_month
    merged["previous_month"] = prev_key
    out = merged[
        ["employee_id", "employee_name", "name_key", "cong_prev", "cong_now", "variance", "note", "target_month", "previous_month"]
    ].copy()
    out["_eid_key"] = [
        employee_id_sort_key(rec.employee_id, rec.employee_name) for rec in out.itertuples(index=False)
    ]
    return out.sort_values(["variance", "_eid_key"], ascending=[True, True], kind="mergesort").drop(
        columns="_eid_key"
    ).reset_index(drop=True)


def export_mom_excel(rows: pd.DataFrame, dest: str | Path, target_month: str, previous_month: str) -> Path:
    """Write MoM grid. Any Excel functions use ';' (VN/EU locale)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "MoM"
    ws["A1"] = f"Phân tích biến động công: {previous_month} → {target_month}"
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:F1")
    headers = ("Mã NV", "Tên NV", "Công tháng trước", "Công tháng này", "Chênh lệch", "Ghi chú")
    for col, title in enumerate(headers, start=1):
        cell = ws.cell(2, col, title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2C3E50")
        cell.alignment = Alignment(horizontal="center")
    data = rows if rows is not None else pd.DataFrame()
    start_row = 3
    for offset, rec in enumerate(data.itertuples(index=False), start=0):
        r = start_row + offset
        ws.cell(r, 1, rec.employee_id)
        ws.cell(r, 2, rec.employee_name)
        ws.cell(r, 3, float(rec.cong_prev))
        ws.cell(r, 4, float(rec.cong_now))
        ws.cell(r, 5, f"=D{r}-C{r}")
        ws.cell(
            r,
            6,
            f'=IF(AND(C{r}=0;D{r}>0);"{NOTE_NEW}";'
            f'IF(AND(C{r}>0;D{r}=0);"{NOTE_GONE}";'
            f'IF(E{r}>0;"Tăng";IF(E{r}<0;"Giảm";""))))',
        )
        ws.cell(r, 3).number_format = "0.00"
        ws.cell(r, 4).number_format = "0.00"
        ws.cell(r, 5).number_format = "0.00"
    last = start_row + max(len(data) - 1, 0) if len(data) else start_row
    if len(data):
        total_row = last + 1
        ws.cell(total_row, 2, "Tổng")
        ws.cell(total_row, 3, excel_formula("SUM", f"C{start_row}:C{last}"))
        ws.cell(total_row, 4, excel_formula("SUM", f"D{start_row}:D{last}"))
        ws.cell(total_row, 5, excel_formula("SUM", f"E{start_row}:E{last}"))
        for col in (2, 3, 4, 5):
            ws.cell(total_row, col).font = Font(bold=True)
    widths = (14, 28, 18, 18, 14, 36)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + idx)].width = width
    wb.save(dest)
    return dest
