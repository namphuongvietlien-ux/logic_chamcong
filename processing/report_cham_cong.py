"""Export monthly timesheet matching CHẤM CÔNG THÁNG 09.xlsx (CCONG TH + CCONG NGOAI GIO)."""

from __future__ import annotations

import calendar
import re
import shutil
from copy import copy
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from processing.cong_rules import (
    attendance_index,
    clocks_from_row,
    cong_standard,
    month_from_merged,
    net_hours,
    overtime_hours,
    weekday_label,
)
from processing.resources import bundled_file, resource_path
from processing.utils import employee_id_sort_key, name_match_key

LogFn = Optional[Callable[[str], None]]

GENERIC_TEMPLATE_NAMES = (
    "Template_Cham_Cong.xlsx",
    "Mau_Cham_Cong.xlsx",
    "Mẫu_Chấm_Công.xlsx",
    "CHẤM CÔNG THÁNG.xlsx",
    "CHAM CONG THANG.xlsx",
)

THIN = Border(
    left=Side(style="thin", color="000000"),
    right=Side(style="thin", color="000000"),
    top=Side(style="thin", color="000000"),
    bottom=Side(style="thin", color="000000"),
)
SUNDAY_FILL = PatternFill("solid", fgColor="BFBFBF")
HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
SECTION_FILL = PatternFill("solid", fgColor="FFFF00")


def _copy_style(src, dest) -> None:
    dest.font = copy(src.font)
    dest.fill = copy(src.fill)
    dest.border = copy(src.border)
    dest.alignment = copy(src.alignment)
    dest.number_format = src.number_format


def _bundled_template() -> Path | None:
    """Prefer the Excel layout packed next to the app / inside _MEIPASS."""
    for name in GENERIC_TEMPLATE_NAMES:
        found = bundled_file(name) or bundled_file("templates", name)
        if found is not None:
            return found
    return None


def _search_roots() -> list[Path]:
    """Folders that may contain a local override (dev only; never the system month)."""
    roots: list[Path] = [Path(resource_path(".")), Path(__file__).resolve().parent.parent]
    extra: list[Path] = []
    for root in list(roots):
        extra.append(root / "templates")
        extra.append(root / "mau")
    seen: set[Path] = set()
    out: list[Path] = []
    for path in roots + extra:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _is_output_report(path: Path) -> bool:
    name = path.name.upper()
    if path.name.startswith("~$"):
        return True
    if "CHECK" in name:
        return True
    if name.startswith("CHAM_CONG_THANG_"):
        return True
    if "output" in {part.lower() for part in path.parts}:
        return True
    return False


def _period_template_names(year: int, month: int) -> list[str]:
    """Names derived from the report period only (not datetime.now())."""
    mm = f"{int(month):02d}"
    m = str(int(month))
    yyyy = f"{int(year):04d}"
    stems = []
    for month_token in (mm, m):
        stems.extend(
            [
                f"CHẤM CÔNG THÁNG {month_token}.xlsx",
                f"CHẤM CÔNG THÁNG {month_token}.{yyyy}.xlsx",
                f"CHAM CONG THANG {month_token}.xlsx",
                f"CHAM CONG THANG {month_token}.{yyyy}.xlsx",
            ]
        )
    return stems


def _preferred_template_names(year: int, month: int) -> list[str]:
    names: list[str] = []
    for item in (*GENERIC_TEMPLATE_NAMES, *_period_template_names(year, month)):
        if item not in names:
            names.append(item)
    return names


def _find_template(year: int, month: int) -> Path | None:
    """Locate a CCONG layout file. Prefer the bundled template, then a local override."""
    bundled = _bundled_template()
    if bundled is not None:
        return bundled
    roots = _search_roots()
    for name in _preferred_template_names(year, month):
        for root in roots:
            candidate = root / name
            if candidate.is_file() and not _is_output_report(candidate):
                return candidate
    fallback: list[Path] = []
    for root in roots:
        fallback.extend(root.glob("*CHẤM CÔNG*.xlsx"))
        fallback.extend(root.glob("*CHAM CONG*.xlsx"))
        fallback.extend(root.glob("*Cham_Cong*.xlsx"))
    fallback = [p for p in fallback if p.is_file() and not _is_output_report(p)]
    if fallback:
        return sorted(fallback, key=lambda p: p.name.lower())[0]
    return None


def _template_missing_message(year: int, month: int) -> str:
    shown = _preferred_template_names(year, month)[0]
    return (
        f"Lỗi: Không tìm thấy file mẫu đã đóng gói ({shown}). "
        "Cần rebuild exe với Template_Cham_Cong.xlsx trong thư mục nguồn."
    )


def _is_person_name(value: Any) -> bool:
    if value is None or not str(value).strip():
        return False
    text = str(value).strip().upper()
    if text.startswith(("NHÂN VIÊN", "PHÒNG", "TỔNG", "STT", "HỌ", "HỌ")):
        return False
    if "TRƯỞNG" in text:
        return False
    return True


def _employee_rows(ws: Worksheet, name_col: int, start_row: int) -> list[int]:
    rows = []
    for r in range(start_row, (ws.max_row or start_row) + 1):
        name = ws.cell(r, name_col).value
        if _is_person_name(name):
            rows.append(r)
    return rows


def _people_on_sheet(ws: Worksheet, name_col: int, start_row: int, mnv_col: int) -> list[dict[str, Any]]:
    people = []
    for r in _employee_rows(ws, name_col, start_row):
        name = str(ws.cell(r, name_col).value).strip()
        mnv = ws.cell(r, mnv_col).value
        people.append({"row": r, "name": name, "key": name_match_key(name), "mnv": mnv})
    return people


def _names_from_merged(merged) -> list[str]:
    if merged is None or getattr(merged, "empty", True) or "employee_name" not in merged.columns:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for raw in merged["employee_name"].tolist():
        if raw is None or not str(raw).strip():
            continue
        name = str(raw).strip()
        key = name_match_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _ids_from_merged(merged) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if merged is None or getattr(merged, "empty", True):
        return out
    if "employee_name" not in merged.columns or "employee_id" not in merged.columns:
        return out
    for rec in merged.itertuples(index=False):
        name = str(getattr(rec, "employee_name", "") or "").strip()
        key = name_match_key(name)
        if not key or key in out:
            continue
        out[key] = getattr(rec, "employee_id", "")
    return out


def _catalog_employees(ws_th: Worksheet, ws_ot: Worksheet, merged) -> list[dict[str, Any]]:
    """Union of CCONG TH + CCONG NGOAI GIO + dữ liệu chấm công, xếp theo mã NV."""
    catalog: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    ids = _ids_from_merged(merged)

    def add(name: str, mnv: Any = None) -> None:
        key = name_match_key(name)
        if not key:
            return
        if key not in catalog:
            catalog[key] = {"name": name.strip(), "mnv": mnv if mnv not in (None, "") else ids.get(key), "key": key}
            order.append(key)
            return
        if catalog[key]["mnv"] in (None, "") and mnv not in (None, ""):
            catalog[key]["mnv"] = mnv
        elif catalog[key]["mnv"] in (None, "") and ids.get(key) not in (None, ""):
            catalog[key]["mnv"] = ids.get(key)

    for person in _people_on_sheet(ws_th, name_col=5, start_row=8, mnv_col=4):
        add(person["name"], person["mnv"])
    for person in _people_on_sheet(ws_ot, name_col=3, start_row=8, mnv_col=2):
        add(person["name"], person["mnv"])
    for name in _names_from_merged(merged):
        add(name, ids.get(name_match_key(name)))
    people = [catalog[k] for k in order]
    people.sort(key=lambda person: employee_id_sort_key(person.get("mnv"), person.get("name")))
    return people


def _retarget_formula(formula: Any, src_row: int, dest_row: int) -> Any:
    if not isinstance(formula, str) or not formula.startswith("="):
        return None
    if "#REF!" in formula.upper():
        return None

    def repl(match: re.Match[str]) -> str:
        col, row = match.group(1), int(match.group(2))
        if row == src_row:
            return f"{col}{dest_row}"
        return match.group(0)

    return re.sub(r"(\$?[A-Z]+)(\d+)", repl, formula)


def _clone_row_style(ws: Worksheet, src_row: int, dest_row: int, max_col: int) -> None:
    src_height = ws.row_dimensions[src_row].height
    ws.row_dimensions[dest_row].height = src_height if src_height else 18
    for col in range(1, max_col + 1):
        src = ws.cell(src_row, col)
        dest = ws.cell(dest_row, col)
        _copy_style(src, dest)
        dest.value = None


def _expand_footer_sums(ws: Worksheet, after_row: int, old_last: int, new_last: int) -> None:
    old_dummy = old_last + 1
    new_dummy = new_last + 1
    max_col = ws.max_column or 52
    for r in range(after_row, after_row + 8):
        for c in range(1, max_col + 1):
            val = ws.cell(r, c).value
            if not isinstance(val, str) or not val.startswith("="):
                continue
            if r == after_row:
                rewritten = _retarget_formula(val, old_dummy, new_dummy) or val
                rewritten = re.sub(rf"([A-Z]+){old_last}(?!\d)", rf"\g<1>{new_last}", rewritten)
                ws.cell(r, c).value = rewritten
            else:
                updated = re.sub(
                    rf"(SUM\([A-Z]+)8(:[A-Z]+){old_dummy}(\))",
                    rf"\g<1>8\g<2>{new_dummy}\3",
                    val,
                    flags=re.I,
                )
                if updated != val:
                    ws.cell(r, c).value = updated


def _append_missing_people(
    ws: Worksheet,
    *,
    name_col: int,
    mnv_col: int,
    start_row: int,
    wanted: list[dict[str, Any]],
    max_col: int,
    insert_before: Optional[int] = None,
    extra_formulas: Optional[Callable[[Worksheet, int, int], None]] = None,
) -> list[str]:
    existing = {p["key"] for p in _people_on_sheet(ws, name_col, start_row, mnv_col)}
    missing = [p for p in wanted if p["key"] not in existing]
    if not missing:
        return []

    emp_rows = _employee_rows(ws, name_col, start_row)
    proto = emp_rows[-1] if emp_rows else start_row
    last = emp_rows[-1] if emp_rows else start_row - 1
    insert_at = insert_before if insert_before is not None else last + 1
    if insert_at <= last:
        insert_at = last + 1
    ws.insert_rows(insert_at, amount=len(missing))
    style_proto = proto if proto < insert_at else proto + len(missing)
    added: list[str] = []
    for offset, person in enumerate(missing):
        dest = insert_at + offset
        prev = dest - 1
        _clone_row_style(ws, style_proto, dest, max_col)
        if extra_formulas:
            extra_formulas(ws, style_proto, dest)
        ws.cell(dest, 1).value = f"=A{prev}+1"
        if mnv_col != 2:
            last_stt = ws.cell(prev, 2).value
            if isinstance(last_stt, (int, float)):
                ws.cell(dest, 2).value = int(last_stt) + 1
        if person["mnv"] not in (None, ""):
            ws.cell(dest, mnv_col).value = person["mnv"]
        name_cell = ws.cell(dest, name_col)
        name_cell.value = person["name"]
        name_cell.fill = SECTION_FILL
        name_cell.font = Font(name="Times New Roman", size=10, bold=True)
        name_cell.alignment = Alignment(horizontal="left", vertical="center")
        name_cell.border = THIN
        added.append(person["name"])
    return added


def _ot_row_formulas(ws: Worksheet, src_row: int, dest_row: int) -> None:
    for col in range(1, (ws.max_column or 42) + 1):
        rewritten = _retarget_formula(ws.cell(src_row, col).value, src_row, dest_row)
        if rewritten:
            ws.cell(dest_row, col).value = rewritten


def _th_row_formulas(ws: Worksheet, src_row: int, dest_row: int) -> None:
    for col in range(1, (ws.max_column or 52) + 1):
        rewritten = _retarget_formula(ws.cell(src_row, col).value, src_row, dest_row)
        if rewritten:
            ws.cell(dest_row, col).value = rewritten


def _ensure_employees_on_reports(ws_th: Worksheet, ws_ot: Worksheet, merged, log: LogFn = None) -> None:
    wanted = _catalog_employees(ws_th, ws_ot, merged)

    th_rows = _employee_rows(ws_th, 5, 8)
    th_last = th_rows[-1] if th_rows else 8
    footer = th_last + 1
    added_th = _append_missing_people(
        ws_th,
        name_col=5,
        mnv_col=4,
        start_row=8,
        wanted=wanted,
        max_col=max(ws_th.max_column or 52, 52),
        insert_before=footer,
        extra_formulas=_th_row_formulas,
    )
    if added_th:
        new_last = _employee_rows(ws_th, 5, 8)[-1]
        _expand_footer_sums(ws_th, new_last + 1, th_last, new_last)
        if log:
            log(f"CCONG TH: thêm {len(added_th)} NV còn thiếu: {', '.join(added_th)}")

    ot_rows = _employee_rows(ws_ot, 3, 8)
    ot_last = ot_rows[-1] if ot_rows else 8
    added_ot = _append_missing_people(
        ws_ot,
        name_col=3,
        mnv_col=2,
        start_row=8,
        wanted=wanted,
        max_col=max(ws_ot.max_column or 44, 44),
        insert_before=ot_last + 1,
        extra_formulas=_ot_row_formulas,
    )
    if added_ot and log:
        log(f"CCONG NGOAI GIO: thêm {len(added_ot)} NV còn thiếu: {', '.join(added_ot)}")
    elif log and not added_ot:
        log("CCONG NGOAI GIO: đủ nhân viên, không cần thêm.")


def _rewrite_day_headers(ws: Worksheet, start_col: int, n_days: int, year: int, month: int, header_row=5, wd_row=6) -> None:
    empty_fill = PatternFill(fill_type=None)
    for offset in range(0, 32):
        col = start_col + offset
        day = offset + 1
        header = ws.cell(header_row, col)
        wd = ws.cell(wd_row, col)
        if day <= n_days:
            work_date = date(year, month, day)
            header.value = day
            wd.value = weekday_label(work_date)
            if work_date.weekday() == 6:
                wd.fill = SUNDAY_FILL
            else:
                wd.fill = empty_fill
        elif isinstance(header.value, (int, float)) or (
            isinstance(header.value, str) and str(header.value).startswith("=")
        ):
            header.value = None
            wd.value = None
            wd.fill = empty_fill


def _apply_sunday_data_fill(ws: Worksheet, start_col: int, n_days: int, year: int, month: int, rows: list[int]) -> None:
    for day in range(1, n_days + 1):
        col = start_col + day - 1
        if date(year, month, day).weekday() != 6:
            continue
        for r in rows:
            cell = ws.cell(r, col)
            if not cell.fill or not cell.fill.patternType:
                cell.fill = SUNDAY_FILL


def _join_add(cells: list[str]) -> str:
    if not cells:
        return "0"
    return "+".join(cells)


def export_cham_cong_thang(
    merged, output_path: str | Path, log: LogFn = None, year: int | None = None, month: int | None = None
) -> Path | None:
    if year is None or month is None:
        year, month = month_from_merged(merged)
    template = _find_template(year, month)
    if template is None:
        if log:
            log(_template_missing_message(year, month))
        return None
    n_days = calendar.monthrange(year, month)[1]
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(template, dest)
    except OSError as exc:
        if log:
            log(
                f"Lỗi: Không đọc được file mẫu đã đóng gói {template.name} ({exc})."
            )
        return None
    if log:
        log(f"Dùng file mẫu CCONG: {template.name}")
    wb = load_workbook(dest)
    lookup = attendance_index(merged)

    ws = wb["CCONG TH"] if "CCONG TH" in wb.sheetnames else wb.worksheets[0]
    ws2 = wb["CCONG NGOAI GIO"] if "CCONG NGOAI GIO" in wb.sheetnames else wb.worksheets[-1]
    _ensure_employees_on_reports(ws, ws2, merged, log)

    # ----- CCONG TH : days start at G (col 7) -----
    ws["A3"] = f"BẢNG CHẤM CÔNG NHÂN VIÊN THÁNG {month:02d}-{year}"
    _rewrite_day_headers(ws, start_col=7, n_days=n_days, year=year, month=month)
    emp_rows = _employee_rows(ws, name_col=5, start_row=8)
    _apply_sunday_data_fill(ws, 7, n_days, year, month, emp_rows)

    for r in emp_rows:
        name = str(ws.cell(r, 5).value).strip()
        key = name_match_key(name)
        for day in range(1, n_days + 1):
            col = 6 + day
            work_date = date(year, month, day)
            rec = lookup.get((key, work_date))
            cell = ws.cell(r, col)
            if rec is None:
                cell.value = cong_standard(None, work_date, False)
                continue
            std = rec.get("standardized_workday") if hasattr(rec, "get") else None
            try:
                import pandas as pd

                if std is not None and not (isinstance(std, float) and pd.isna(std)):
                    cell.value = float(std)
                    cell.number_format = "0.00"
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    continue
            except (TypeError, ValueError):
                pass
            d, e, f, g = clocks_from_row(rec)
            hours = net_hours(
                d, e, f, g,
                rec.get("lunch_duration_hours") if hasattr(rec, "get") else None,
                logical_date=work_date,
                overnight=bool(rec.get("overnight")) if hasattr(rec, "get") else False,
            )
            std = rec.get("standard_shift_hours") if hasattr(rec, "get") else None
            cell.value = cong_standard(hours, work_date, True, std)
            cell.number_format = "0.0"
            cell.alignment = Alignment(horizontal="center", vertical="center")

    # footer date
    for r in range(ws.max_row or 80, 3, -1):
        val = ws.cell(r, 27).value or ws.cell(r, 1).value
        if isinstance(val, str) and "Ngày" in val and "Tháng" in val:
            ws.cell(r, 27).value = f"                                       Ngày {date.today().day:02d} Tháng {date.today().month:02d} Năm  {date.today().year}"
            break

    # ----- CCONG NGOAI GIO : days start at E (col 5) -----
    ws2["A3"] = f"BẢNG CHẤM CÔNG NHÂN VIÊN THÁNG {month:02d}-{year}"
    _rewrite_day_headers(ws2, start_col=5, n_days=n_days, year=year, month=month)
    weekday_cols: list[int] = []
    weekend_cols: list[int] = []
    for day in range(1, n_days + 1):
        col = 4 + day
        if date(year, month, day).weekday() >= 5:
            weekend_cols.append(col)
        else:
            weekday_cols.append(col)

    emp_rows2 = _employee_rows(ws2, name_col=3, start_row=8)
    _apply_sunday_data_fill(ws2, 5, n_days, year, month, emp_rows2)
    last_day_col = 4 + n_days
    cong_col = 39  # AM in the August template

    for r in emp_rows2:
        name = str(ws2.cell(r, 3).value).strip()
        key = name_match_key(name)
        for day in range(1, n_days + 1):
            col = 4 + day
            work_date = date(year, month, day)
            rec = lookup.get((key, work_date))
            cell = ws2.cell(r, col)
            if rec is None:
                cell.value = None
                continue
            extra = rec.get("overtime_hours") if hasattr(rec, "get") else None
            try:
                import pandas as pd

                if extra is None or (isinstance(extra, float) and pd.isna(extra)):
                    extra = None
            except (TypeError, ValueError):
                extra = extra
            if extra is None:
                d, e, f, g = clocks_from_row(rec)
                extra = overtime_hours(
                    net_hours(
                        d, e, f, g,
                        rec.get("lunch_duration_hours") if hasattr(rec, "get") else None,
                        logical_date=work_date,
                        overnight=bool(rec.get("overnight")) if hasattr(rec, "get") else False,
                    ),
                    rec.get("standard_shift_hours") if hasattr(rec, "get") else None,
                )
            if extra:
                cell.value = extra
                cell.number_format = "0.0"
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.value = None

        wd_letters = [f"{get_column_letter(c)}{r}" for c in weekday_cols]
        we_letters = [f"{get_column_letter(c)}{r}" for c in weekend_cols]
        # Locale Excel VN: phân tách đối số công thức bằng dấu chấm phẩy (;).
        ws2.cell(r, 39).value = f"=SUM(E{r}:{get_column_letter(last_day_col)}{r})"
        ws2.cell(r, 41).value = f"=({_join_add(wd_letters)})/8*1.5"
        ws2.cell(r, 42).value = f"=({_join_add(we_letters)})/8*2"
        ws2.cell(r, 40).value = f"=AO{r}+AP{r}"

    wb.save(dest)
    wb.close()
    if log:
        log(f"Đã ghi: {dest.name}  (CCONG TH = công chuẩn, CCONG NGOAI GIO = ngoài giờ)")
    return dest
