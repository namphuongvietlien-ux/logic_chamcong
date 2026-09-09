"""Export daily APP timesheets matching K9 08.2026.xlsx (one sheet per day)."""

from __future__ import annotations

import calendar
import re
import shutil
from datetime import date, time
from pathlib import Path
from typing import Callable, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from processing.cong_rules import (
    attendance_index,
    clocks_from_row,
    cong_standard,
    month_from_merged,
    net_hours,
    overtime_hours,
)
from processing.resources import resource_path
from processing.utils import employee_id_sort_key, name_match_key

LogFn = Optional[Callable[[str], None]]

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(resource_path("K9 08.2026.xlsx"))


def _find_template() -> Path:
    if TEMPLATE.is_file():
        return TEMPLATE
    matches = list(ROOT.glob("K9 *.xlsx")) + list(ROOT.glob("K9*.xlsx"))
    matches = [
        p
        for p in matches
        if not p.name.startswith("~$")
        and "output" not in {part.lower() for part in p.parts}
        and not re.match(r"^K9_\d{2}\.\d{4}\.xlsx$", p.name, re.I)
    ]
    if not matches:
        raise FileNotFoundError("Không tìm thấy file mẫu K9 08.2026.xlsx")
    return matches[0]


def _prototype_sheet(wb) -> Worksheet:
    for name in wb.sheetnames:
        if name.lower().startswith("sheet"):
            continue
        return wb[name]
    return wb.worksheets[0]


def _employee_rows(ws: Worksheet) -> list[int]:
    rows = []
    for r in range(5, (ws.max_row or 5) + 1):
        name = ws.cell(r, 3).value
        if name and str(name).strip():
            rows.append(r)
    return rows


def _append_missing_k9(ws: Worksheet, merged) -> list[str]:
    from copy import copy
    from openpyxl.styles import Font, PatternFill

    existing_keys = set()
    for r in _employee_rows(ws):
        name = str(ws.cell(r, 3).value).strip()
        existing_keys.add(name_match_key(name))

    wanted: list[str] = []
    seen = set(existing_keys)
    id_by_key: dict[str, object] = {}
    if merged is not None and not getattr(merged, "empty", True) and "employee_name" in merged.columns:
        has_id = "employee_id" in merged.columns
        for rec in merged.itertuples(index=False):
            raw = getattr(rec, "employee_name", None)
            if raw is None or not str(raw).strip():
                continue
            name = str(raw).strip()
            key = name_match_key(name)
            if not key:
                continue
            if has_id and key not in id_by_key:
                id_by_key[key] = getattr(rec, "employee_id", "")
            if key in seen:
                continue
            seen.add(key)
            wanted.append(name)
    wanted.sort(key=lambda name: employee_id_sort_key(id_by_key.get(name_match_key(name), ""), name))
    if not wanted:
        return []

    emp_rows = _employee_rows(ws)
    proto = emp_rows[-1]
    last = emp_rows[-1]
    ws.insert_rows(last + 1, amount=len(wanted))
    yellow = PatternFill("solid", fgColor="FFFF00")
    added: list[str] = []
    for offset, name in enumerate(wanted):
        dest = last + 1 + offset
        prev = dest - 1
        ws.row_dimensions[dest].height = ws.row_dimensions[proto].height
        for col in range(1, (ws.max_column or 13) + 1):
            src = ws.cell(proto, col)
            dest_cell = ws.cell(dest, col)
            dest_cell.font = copy(src.font)
            dest_cell.fill = copy(src.fill)
            dest_cell.border = copy(src.border)
            dest_cell.alignment = copy(src.alignment)
            dest_cell.number_format = src.number_format
            dest_cell.value = None
        last_stt = ws.cell(prev, 1).value
        ws.cell(dest, 1).value = int(last_stt) + 1 if isinstance(last_stt, (int, float)) else dest - 4
        last_mnv = ws.cell(prev, 2).value
        if isinstance(last_mnv, (int, float)):
            ws.cell(dest, 2).value = int(last_mnv) + 1
        name_cell = ws.cell(dest, 3)
        name_cell.value = name
        name_cell.fill = yellow
        name_cell.font = Font(name="Times New Roman", size=12, bold=True)
        added.append(name)
    _ensure_formulas(ws, list(range(last + 1, last + 1 + len(wanted))))
    return added


def _time_cell(ws: Worksheet, row: int, col: int, value: Optional[time], plus: int = 0) -> None:
    cell = ws.cell(row, col)
    extra = 0
    try:
        extra = int(plus or 0)
    except (TypeError, ValueError):
        extra = 0
    if extra > 0 and value is not None:
        from processing.sessions import format_clock_with_offset

        cell.value = format_clock_with_offset(value, extra)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        return
    cell.value = value
    if value is not None:
        cell.number_format = "h:mm"
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _clear_times(ws: Worksheet, rows: list[int]) -> None:
    for r in rows:
        for c in (4, 5, 6, 7, 12, 13):
            ws.cell(r, c).value = None


def _ensure_formulas(ws: Worksheet, rows: list[int]) -> None:
    """Cách tính 1 / Cách tính 2 — Excel locale dùng dấu chấm phẩy (;)."""
    for r in rows:
        ws.cell(r, 9).value = (
            f'=IF(AND(D{r}<>"";G{r}<>"");'
            f'IF(COUNTA(D{r}:G{r})=3;G{r}-D{r}-TIME(1;0;0);G{r}-D{r});'
            f'"-")'
        )
        ws.cell(r, 9).number_format = "h:mm"
        ws.cell(r, 10).value = (
            f'=IF(AND(D{r}<>"";E{r}<>"";F{r}<>"";G{r}<>"");(E{r}-D{r})+(G{r}-F{r});"-")'
        )
        ws.cell(r, 10).number_format = "h:mm"
        ws.cell(r, 11).value = f'=IF(OR(I{r}="-";J{r}="-");"-";I{r}-J{r})'
        ws.cell(r, 11).number_format = "h:mm"


def export_k9_daily(
    merged,
    output_path: str | Path,
    log: LogFn = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> Path:
    if year is None or month is None:
        year, month = month_from_merged(merged)
    n_days = calendar.monthrange(year, month)[1]
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_find_template(), dest)
    wb = load_workbook(dest)
    lookup = attendance_index(merged)

    proto_name = _prototype_sheet(wb).title
    # Drop leftover day sheets except one prototype; keep Sheet1.
    keep = {proto_name}
    for name in list(wb.sheetnames):
        if name.lower().startswith("sheet"):
            keep.add(name)
    for name in list(wb.sheetnames):
        if name not in keep:
            del wb[name]

    proto = wb[proto_name]
    added = _append_missing_k9(proto, merged)
    emp_rows = _employee_rows(proto)
    if added and log:
        log(f"K9: thêm {len(added)} NV còn thiếu: {', '.join(added)}")

    _clear_times(proto, emp_rows)
    _ensure_formulas(proto, emp_rows)

    sheets: dict[int, Worksheet] = {}
    for day in range(n_days, 0, -1):
        title = f"{day:02d}.{month:02d}"
        if day == n_days:
            ws = proto
            ws.title = title
        else:
            ws = wb.copy_worksheet(proto)
            ws.title = title
        sheets[day] = ws
        ws["C2"] = (
            f"BẢNG THEO DÕI THỜI GIAN LÀM  VIỆC TRÊN APP "
            f"NHÂN VIÊN K9 THÁNG {month:02d}/{year}"
        )
        ws["C2"].font = Font(name="Times New Roman", size=14, bold=True)
        _clear_times(ws, emp_rows)
        _ensure_formulas(ws, emp_rows)
        work_date = date(year, month, day)
        for r in emp_rows:
            name = str(ws.cell(r, 3).value).strip()
            rec = lookup.get((name_match_key(name), work_date))
            if rec is None:
                continue
            d, e, f, g = clocks_from_row(rec)
            _time_cell(ws, r, 4, d, rec.get("in1_plus") if hasattr(rec, "get") else 0)
            _time_cell(ws, r, 5, e, rec.get("out1_plus") if hasattr(rec, "get") else 0)
            _time_cell(ws, r, 6, f, rec.get("in2_plus") if hasattr(rec, "get") else 0)
            _time_cell(ws, r, 7, g, rec.get("out2_plus") if hasattr(rec, "get") else 0)
            hours = rec.get("actual_work_hours") if hasattr(rec, "get") else None
            try:
                import pandas as pd

                if hours is None or (isinstance(hours, float) and pd.isna(hours)):
                    hours = net_hours(
                        d, e, f, g,
                        rec.get("lunch_duration_hours") if hasattr(rec, "get") else None,
                        logical_date=work_date,
                        overnight=bool(rec.get("overnight")) if hasattr(rec, "get") else False,
                    )
            except (TypeError, ValueError):
                hours = net_hours(d, e, f, g, rec.get("lunch_duration_hours") if hasattr(rec, "get") else None, logical_date=work_date, overnight=bool(rec.get("overnight")) if hasattr(rec, "get") else False)
            std = rec.get("standard_shift_hours") if hasattr(rec, "get") else None
            extra = overtime_hours(hours, std)
            if extra:
                ws.cell(r, 12).value = extra / 24.0
                ws.cell(r, 12).number_format = "h:mm"
            cong = cong_standard(hours, work_date, True, std)
            if cong not in (1, None):
                ws.cell(r, 13).value = cong

    if "Sheet1" in wb.sheetnames:
        wb.move_sheet(wb["Sheet1"], offset=len(wb.sheetnames) - 1 - wb.sheetnames.index("Sheet1"))

    wb.save(dest)
    wb.close()
    if log:
        log(f"Đã ghi: {dest.name}  ({n_days} sheet ngày, mẫu K9)")
    return dest
