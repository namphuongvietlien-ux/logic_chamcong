"""Module 0: load Employee_Master_Data.xlsx (per-employee shift + lunch duration)."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from processing.utils import name_match_key, parse_time_value

LogFn = Optional[Callable[[str], None]]

DEFAULT_SHIFT_HOURS = 8.0
DEFAULT_SHIFT_START = time(8, 0)
DEFAULT_SHIFT_END = time(17, 0)

NAME_ALIASES = {
    "employee name",
    "name",
    "họ và tên",
    "ho va ten",
    "họ tên",
    "ho ten",
    "tên nhân viên",
    "ten nhan vien",
    "nhân viên",
    "nhan vien",
}
ID_ALIASES = {
    "employee id",
    "id",
    "mã nhân viên",
    "ma nhan vien",
    "mnv",
    "manv",
    "mã nv",
}
DEPT_ALIASES = {
    "department",
    "phòng ban",
    "phong ban",
    "dept",
    "bộ phận",
    "bo phan",
}
SHIFT_HOURS_ALIASES = {
    "standard shift hours",
    "standard shift (hours)",
    "standard shift",
    "ca chuẩn",
    "ca chuan",
    "số giờ ca",
    "so gio ca",
    "giờ ca",
    "shift hours",
}
SHIFT_START_ALIASES = {"shift start", "giờ vào ca", "gio vao ca", "start", "giờ bắt đầu"}
SHIFT_END_ALIASES = {"shift end", "giờ tan ca", "gio tan ca", "end", "giờ kết thúc ca"}
LUNCH_HOURS_ALIASES = {
    "lunch duration hours",
    "lunch duration",
    "lunch hours",
    "số giờ nghỉ trưa",
    "so gio nghi trua",
    "giờ nghỉ trưa",
    "gio nghi trua",
    "nghỉ trưa (giờ)",
}
LUNCH_START_ALIASES = {"lunch start", "giờ bắt đầu nghỉ", "lunch begin"}
LUNCH_END_ALIASES = {"lunch end", "giờ hết nghỉ", "gio het nghi", "lunch finish", "kết thúc nghỉ trưa"}
JOIN_DATE_ALIASES = {
    "join date",
    "ngày vào",
    "ngay vao",
    "ngày nhận việc",
    "ngay nhan viec",
    "hire date",
}
OFFICIAL_START_ALIASES = {
    "official start date",
    "official_start_date",
    "ngày chính thức",
    "ngay chinh thuc",
    "ngày vào chính thức",
    "ngay vao chinh thuc",
    "official date",
}
BASE_LEAVE_ALIASES = {
    "base leave",
    "phép gốc",
    "phep goc",
    "phép cơ bản",
    "phep co ban",
    "annual leave",
    "số phép năm",
}
CARRYOVER_ALIASES = {
    "carryover",
    "carryover leave",
    "phép tồn",
    "phep ton",
    "phép tồn năm ngoái",
    "phép tồn năm trước",
    "phep ton nam truoc",
    "tồn năm trước",
    "ton nam truoc",
    "ton phep",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _find_col(columns: list[str], aliases: set[str]) -> Optional[str]:
    for col in columns:
        if _norm(col) in aliases:
            return col
    for col in columns:
        n = _norm(col)
        if any(alias in n for alias in aliases):
            return col
    return None


def _hours(value: Any, default: float) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0 else default
    text = str(value).lower().replace("giờ", " ").replace("h", " ").replace(",", ".")
    try:
        parsed = float(text.strip().split()[0])
        return parsed if parsed >= 0 else default
    except (ValueError, IndexError):
        return default


def _pad_id(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(5)
    return text


def default_config() -> dict:
    return {
        "employee_id": "",
        "department": "",
        "standard_shift_hours": DEFAULT_SHIFT_HOURS,
        "shift_start": DEFAULT_SHIFT_START,
        "shift_end": DEFAULT_SHIFT_END,
        "lunch_duration_hours": 0.0,
        "lunch_start": None,
        "lunch_end": None,
    }


def load_master_excel(path: str | Path | None, log: LogFn = None) -> pd.DataFrame:
    """Parse an Employee_Master_Data.xlsx-style sheet (used to seed SQLite)."""
    if not path:
        return pd.DataFrame()
    file = Path(path)
    if not file.exists():
        if log:
            log(f"Không thấy file Excel master ({file.name}).")
        return pd.DataFrame()

    raw = pd.read_excel(file, dtype=object)
    if raw.empty:
        if log:
            log("Employee_Master_Data.xlsx trống — dùng ca mặc định.")
        return pd.DataFrame()

    cols = list(raw.columns)
    name_col = _find_col(cols, NAME_ALIASES)
    if name_col is None:
        raise ValueError(
            "Employee_Master_Data.xlsx thiếu cột tên nhân viên "
            "(Employee Name / Họ và tên)."
        )
    id_col = _find_col(cols, ID_ALIASES)
    dept_col = _find_col(cols, DEPT_ALIASES)
    hours_col = _find_col(cols, SHIFT_HOURS_ALIASES)
    start_col = _find_col(cols, SHIFT_START_ALIASES)
    end_col = _find_col(cols, SHIFT_END_ALIASES)
    lunch_h_col = _find_col(cols, LUNCH_HOURS_ALIASES)
    lunch_s_col = _find_col(cols, LUNCH_START_ALIASES)
    lunch_e_col = _find_col(cols, LUNCH_END_ALIASES)
    official_col = _find_col(cols, OFFICIAL_START_ALIASES)
    join_col = _find_col([c for c in cols if c != official_col], JOIN_DATE_ALIASES)
    base_leave_col = _find_col(cols, BASE_LEAVE_ALIASES)
    carry_col = _find_col(cols, CARRYOVER_ALIASES)

    rows = []
    for _, row in raw.iterrows():
        name = str(row[name_col] or "").strip()
        if not name or name.lower().startswith("employee"):
            continue
        lunch_start = parse_time_value(row[lunch_s_col] if lunch_s_col else None)
        lunch_end = parse_time_value(row[lunch_e_col] if lunch_e_col else None)
        # Chỉ lấy số giờ nghỉ khi master khai rõ. Không suy ra 1h từ 12:00–13:00
        # (nhiều ca không nghỉ trưa — không mặc định khung giờ nghỉ).
        lunch_hours = _hours(row[lunch_h_col], 0.0) if lunch_h_col else 0.0
        rows.append(
            {
                "employee_id": _pad_id(row[id_col] if id_col else None),
                "employee_name": name,
                "name_key": name_match_key(name),
                "department": str(row[dept_col] or "").strip() if dept_col else "",
                "standard_shift_hours": _hours(row[hours_col] if hours_col else None, DEFAULT_SHIFT_HOURS),
                "shift_start": parse_time_value(row[start_col] if start_col else None) or DEFAULT_SHIFT_START,
                "shift_end": parse_time_value(row[end_col] if end_col else None) or DEFAULT_SHIFT_END,
                "lunch_duration_hours": lunch_hours,
                "lunch_start": lunch_start,
                "lunch_end": lunch_end,
                "join_date": row[join_col] if join_col else "",
                "official_start_date": row[official_col] if official_col else "",
                "base_leave": int(_hours(row[base_leave_col], 12)) if base_leave_col else 12,
                "carryover_leave": _hours(row[carry_col], 0.0) if carry_col else 0.0,
            }
        )
    df = pd.DataFrame(rows).drop_duplicates(subset=["name_key"], keep="first")
    if log:
        log(
            f"Master data: {len(df)} nhân viên (ID, phòng ban, ca). "
            "Nghỉ trưa theo dấu chấm công, không gán 12:00–13:00."
        )
    return df


def load_master_data(path: str | Path | None, log: LogFn = None) -> pd.DataFrame:
    """Prefer the local SQLite roster. Optionally seed from Excel if the DB is empty."""
    from processing.database import employees_frame, import_employees_from_excel, init_db

    init_db()
    frame = employees_frame(active_only=True)
    if not frame.empty:
        if log:
            log(f"Master CSDL: {len(frame)} nhân viên (ID, phòng ban, ca).")
        return frame
    if path and Path(path).exists():
        imported = import_employees_from_excel(path, log=log)
        frame = employees_frame(active_only=True)
        if imported and log:
            log(f"Đã chuyển {imported} NV từ Excel vào CSDL nội bộ.")
        if not frame.empty:
            return frame
    if log:
        log("Chưa có nhân viên trong CSDL — dùng ca mặc định 8h (08:00–17:00), không gán giờ nghỉ trưa.")
    return pd.DataFrame()


def is_in_master(master: pd.DataFrame | None, name: str) -> bool:
    """True when lookup_config would use a master row (exact key or unique fuzzy)."""
    return _master_hit(master, name) is not None


def _master_hit(master: pd.DataFrame | None, name: str) -> Optional[pd.Series]:
    if master is None or master.empty or not name:
        return None
    if "name_key" not in master.columns:
        return None
    key = name_match_key(name)
    if not key:
        return None
    hit = master[master["name_key"] == key]
    if hit.empty:
        hit = master[master["name_key"].map(lambda k: bool(k) and (k in key or key in k))]
        if len(hit) != 1:
            return None
    return hit.iloc[0]


def lookup_config(master: pd.DataFrame, name: str) -> dict:
    """Shift/lunch for an employee. Unmatched → 8h shift, 0 lunch (safe defaults)."""
    cfg = default_config()
    row = _master_hit(master, name)
    if row is None:
        return cfg
    return {
        "employee_id": str(row.get("employee_id") or ""),
        "department": str(row.get("department") or ""),
        "standard_shift_hours": float(row["standard_shift_hours"]),
        "shift_start": row["shift_start"],
        "shift_end": row["shift_end"],
        "lunch_duration_hours": float(row.get("lunch_duration_hours") or 0),
        "lunch_start": row.get("lunch_start"),
        "lunch_end": row.get("lunch_end"),
    }
