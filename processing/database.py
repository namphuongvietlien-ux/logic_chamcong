"""Local SQLite store for employees, holidays, and app settings."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import pandas as pd

from processing.resources import get_db_path, is_ephemeral_bundle_path, writable_dir
from processing.utils import employee_id_sort_key, name_match_key, parse_time_value

LogFn = Optional[Callable[[str], None]]

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    department TEXT NOT NULL DEFAULT '',
    standard_shift_hours REAL NOT NULL DEFAULT 8,
    shift_start TEXT NOT NULL DEFAULT '08:00',
    shift_end TEXT NOT NULL DEFAULT '17:00',
    lunch_duration_hours REAL NOT NULL DEFAULT 0,
    lunch_start TEXT NOT NULL DEFAULT '',
    lunch_end TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    holiday_date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'public',
    paid INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'auto',
    year INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payroll_locks (
    month_year TEXT PRIMARY KEY,
    is_locked INTEGER NOT NULL DEFAULT 1,
    locked_at TEXT,
    locked_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    days REAL NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 1.0,
    session TEXT NOT NULL DEFAULT 'Cả ngày',
    status TEXT NOT NULL DEFAULT 'approved',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attendance_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month_year TEXT NOT NULL,
    name_key TEXT NOT NULL,
    employee_name TEXT NOT NULL DEFAULT '',
    work_date TEXT NOT NULL,
    standardized_workday REAL NOT NULL DEFAULT 0,
    overtime_hours REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (month_year, name_key, work_date)
);

CREATE TABLE IF NOT EXISTS attendance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month_year TEXT NOT NULL,
    name_key TEXT NOT NULL,
    employee_id TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL DEFAULT '',
    workdays REAL NOT NULL DEFAULT 0,
    used_leave REAL NOT NULL DEFAULT 0,
    overtime_hours REAL NOT NULL DEFAULT 0,
    source_file TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL,
    UNIQUE (month_year, name_key)
);
"""

EMPLOYEE_MIGRATIONS = (
    ("join_date", "TEXT NOT NULL DEFAULT ''"),
    ("base_leave", "INTEGER NOT NULL DEFAULT 12"),
    ("carryover_leave", "REAL NOT NULL DEFAULT 0"),
    ("official_start_date", "TEXT NOT NULL DEFAULT ''"),
)

LEAVE_MIGRATIONS = (
    ("duration", "REAL NOT NULL DEFAULT 1.0"),
    ("session", "TEXT NOT NULL DEFAULT 'Cả ngày'"),
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, decl: str) -> bool:
    """ALTER TABLE add-column only. Never DROP. Duplicate column → ignore."""
    if name in _table_columns(conn, table):
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        return True
    except sqlite3.OperationalError:
        return False


def upgrade_database(conn: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS + additive ALTER TABLE. Never DROP or replace the file."""
    if "DROP TABLE" in SCHEMA.upper():
        raise RuntimeError("SCHEMA must never DROP TABLE on startup.")
    conn.executescript(SCHEMA)
    added_official = _add_column_if_missing(
        conn, "employees", "official_start_date", "TEXT NOT NULL DEFAULT ''"
    )
    for name, decl in EMPLOYEE_MIGRATIONS:
        if name == "official_start_date":
            continue
        _add_column_if_missing(conn, "employees", name, decl)
    if added_official or (
        "official_start_date" in _table_columns(conn, "employees")
        and "join_date" in _table_columns(conn, "employees")
    ):
        try:
            conn.execute(
                """
                UPDATE employees
                SET official_start_date = join_date
                WHERE official_start_date = '' AND join_date != ''
                """
            )
        except sqlite3.OperationalError:
            pass
    for name, decl in LEAVE_MIGRATIONS:
        _add_column_if_missing(conn, "leave_requests", name, decl)


def migrate(conn: sqlite3.Connection) -> None:
    """Add new columns/tables on existing databases without dropping data."""
    upgrade_database(conn)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _time_text(value: Any, default: str = "") -> str:
    parsed = parse_time_value(value)
    if parsed is None:
        text = str(value or "").strip()
        return text if text else default
    return parsed.strftime("%H:%M")


def _parse_time_cell(value: Any) -> Optional[time]:
    return parse_time_value(value) if value else None


def date_text(value: Any) -> str:
    """Normalize join/leave dates to ISO YYYY-MM-DD (empty if unknown)."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_iso_date(value: Any) -> Optional[date]:
    text = date_text(value)
    if not text:
        return None
    return date.fromisoformat(text)


def month_key(year: int, month: int) -> str:
    return f"{int(month):02d}/{int(year)}"


def parse_month_key(value: str) -> tuple[int, int]:
    text = str(value or "").strip()
    if "/" in text:
        month, year = text.split("/", 1)
        return int(year), int(month)
    raise ValueError(f"Kỳ không hợp lệ: {value}")


def previous_month_key(month_year: str) -> str:
    year, month = parse_month_key(month_year)
    if month <= 1:
        return month_key(year - 1, 12)
    return month_key(year, month - 1)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_db_path()
    if is_ephemeral_bundle_path(path):
        raise RuntimeError(
            "CSDL không được lưu trong thư mục tạm PyInstaller (_MEIPASS). "
            "Đặt file .db cạnh AttendanceApp.exe."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> Path:
    path = get_db_path()
    if is_ephemeral_bundle_path(path):
        raise RuntimeError("Refusing to create SQLite database inside PyInstaller temp (_MEIPASS).")
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        upgrade_database(conn)
    return path


def backup_database(dest: str | Path | None = None) -> Path:
    """Copy the live DB next to the exe. Does not replace or delete the original."""
    src = get_db_path()
    if not src.is_file() or src.stat().st_size <= 0:
        raise FileNotFoundError("Chưa có CSDL để sao lưu.")
    if dest is None:
        folder = writable_dir() / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_path = folder / f"{src.stem}_{stamp}{src.suffix or '.db'}"
    else:
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_path)
    return dest_path


def get_setting(key: str, default: str = "") -> str:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


def list_employees(active_only: bool = False) -> list[dict]:
    init_db()
    sql = "SELECT * FROM employees"
    if active_only:
        sql += " WHERE active = 1"
    with connect() as conn:
        rows = [dict(row) for row in conn.execute(sql)]
    rows.sort(key=lambda item: employee_id_sort_key(item.get("employee_id"), item.get("employee_name")))
    return rows


def employees_frame(active_only: bool = True) -> pd.DataFrame:
    """Master-compatible dataframe (time objects + name_key) for the pipeline."""
    rows = []
    for item in list_employees(active_only=active_only):
        rows.append(
            {
                "employee_id": item.get("employee_id") or "",
                "employee_name": item.get("employee_name") or "",
                "name_key": item.get("name_key") or name_match_key(item.get("employee_name") or ""),
                "department": item.get("department") or "",
                "standard_shift_hours": float(item.get("standard_shift_hours") or 8),
                "shift_start": _parse_time_cell(item.get("shift_start")) or time(8, 0),
                "shift_end": _parse_time_cell(item.get("shift_end")) or time(17, 0),
                "lunch_duration_hours": float(item.get("lunch_duration_hours") or 0),
                "lunch_start": _parse_time_cell(item.get("lunch_start")),
                "lunch_end": _parse_time_cell(item.get("lunch_end")),
                "join_date": item.get("join_date") or "",
                "official_start_date": item.get("official_start_date") or item.get("join_date") or "",
                "base_leave": int(item.get("base_leave") or 12),
                "carryover_leave": float(item.get("carryover_leave") or 0),
            }
        )
    return pd.DataFrame(rows)


def upsert_employee(payload: dict) -> int:
    name = str(payload.get("employee_name") or "").strip()
    if not name:
        raise ValueError("Tên nhân viên không được trống.")
    key = name_match_key(name)
    if not key:
        raise ValueError("Tên nhân viên không hợp lệ.")
    now = _now()
    fields = {
        "employee_id": str(payload.get("employee_id") or "").strip(),
        "employee_name": name,
        "name_key": key,
        "department": str(payload.get("department") or "").strip(),
        "standard_shift_hours": float(payload.get("standard_shift_hours") or 8),
        "shift_start": _time_text(payload.get("shift_start"), "08:00"),
        "shift_end": _time_text(payload.get("shift_end"), "17:00"),
        "lunch_duration_hours": float(payload.get("lunch_duration_hours") or 0),
        "lunch_start": _time_text(payload.get("lunch_start"), ""),
        "lunch_end": _time_text(payload.get("lunch_end"), ""),
        "active": 1 if payload.get("active", True) else 0,
        "join_date": date_text(payload.get("join_date")),
        "official_start_date": date_text(
            payload.get("official_start_date")
            if payload.get("official_start_date") not in (None, "")
            else payload.get("join_date")
        ),
        "base_leave": int(payload.get("base_leave") if payload.get("base_leave") not in (None, "") else 12),
        "carryover_leave": float(payload.get("carryover_leave") or 0),
        "updated_at": now,
    }
    init_db()
    with connect() as conn:
        existing_id = payload.get("id")
        if existing_id:
            conn.execute(
                """
                UPDATE employees SET
                    employee_id=?, employee_name=?, name_key=?, department=?,
                    standard_shift_hours=?, shift_start=?, shift_end=?,
                    lunch_duration_hours=?, lunch_start=?, lunch_end=?,
                    active=?, join_date=?, official_start_date=?,
                    base_leave=?, carryover_leave=?, updated_at=?
                WHERE id=?
                """,
                (
                    fields["employee_id"],
                    fields["employee_name"],
                    fields["name_key"],
                    fields["department"],
                    fields["standard_shift_hours"],
                    fields["shift_start"],
                    fields["shift_end"],
                    fields["lunch_duration_hours"],
                    fields["lunch_start"],
                    fields["lunch_end"],
                    fields["active"],
                    fields["join_date"],
                    fields["official_start_date"],
                    fields["base_leave"],
                    fields["carryover_leave"],
                    fields["updated_at"],
                    int(existing_id),
                ),
            )
            return int(existing_id)
        hit = conn.execute("SELECT id FROM employees WHERE name_key = ?", (key,)).fetchone()
        if hit:
            conn.execute(
                """
                UPDATE employees SET
                    employee_id=?, employee_name=?, department=?,
                    standard_shift_hours=?, shift_start=?, shift_end=?,
                    lunch_duration_hours=?, lunch_start=?, lunch_end=?,
                    active=?, join_date=?, official_start_date=?,
                    base_leave=?, carryover_leave=?, updated_at=?
                WHERE id=?
                """,
                (
                    fields["employee_id"],
                    fields["employee_name"],
                    fields["department"],
                    fields["standard_shift_hours"],
                    fields["shift_start"],
                    fields["shift_end"],
                    fields["lunch_duration_hours"],
                    fields["lunch_start"],
                    fields["lunch_end"],
                    fields["active"],
                    fields["join_date"],
                    fields["official_start_date"],
                    fields["base_leave"],
                    fields["carryover_leave"],
                    fields["updated_at"],
                    int(hit["id"]),
                ),
            )
            return int(hit["id"])
        cur = conn.execute(
            """
            INSERT INTO employees (
                employee_id, employee_name, name_key, department,
                standard_shift_hours, shift_start, shift_end,
                lunch_duration_hours, lunch_start, lunch_end,
                active, join_date, official_start_date, base_leave, carryover_leave,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields["employee_id"],
                fields["employee_name"],
                fields["name_key"],
                fields["department"],
                fields["standard_shift_hours"],
                fields["shift_start"],
                fields["shift_end"],
                fields["lunch_duration_hours"],
                fields["lunch_start"],
                fields["lunch_end"],
                fields["active"],
                fields["join_date"],
                fields["official_start_date"],
                fields["base_leave"],
                fields["carryover_leave"],
                now,
                now,
            ),
        )
        return int(cur.lastrowid)


def delete_employee(employee_pk: int) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM employees WHERE id = ?", (int(employee_pk),))


def find_employee_by_code(employee_id: str) -> Optional[dict]:
    code = str(employee_id or "").strip()
    if not code:
        return None
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE employee_id = ? COLLATE NOCASE",
            (code,),
        ).fetchone()
        if row:
            return dict(row)
        # padded vs unpadded (00192 / 192)
        digits = code.lstrip("0") or "0"
        row = conn.execute(
            "SELECT * FROM employees WHERE LTRIM(employee_id, '0') = ? AND employee_id != ''",
            (digits,),
        ).fetchone()
    return dict(row) if row else None


def find_employee_by_name_key(key: str) -> Optional[dict]:
    if not key:
        return None
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM employees WHERE name_key = ?", (key,)).fetchone()
    return dict(row) if row else None


def update_employee_master_fields(employee_pk: int, payload: dict) -> None:
    """Update HR master fields only. Never writes attendance_days or payroll_locks."""
    now = _now()
    init_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE employees SET
                department = ?,
                standard_shift_hours = ?,
                lunch_duration_hours = ?,
                lunch_start = ?,
                lunch_end = ?,
                base_leave = ?,
                carryover_leave = ?,
                official_start_date = CASE WHEN ? != '' THEN ? ELSE official_start_date END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                str(payload.get("department") or "").strip(),
                float(payload.get("standard_shift_hours") or 8),
                float(payload.get("lunch_duration_hours") or 0),
                _time_text(payload.get("lunch_start"), ""),
                _time_text(payload.get("lunch_end"), ""),
                int(payload.get("base_leave") if payload.get("base_leave") not in (None, "") else 12),
                float(payload.get("carryover_leave") or 0),
                date_text(payload.get("official_start_date") or payload.get("join_date")),
                date_text(payload.get("official_start_date") or payload.get("join_date")),
                now,
                int(employee_pk),
            ),
        )


def sync_employees_from_excel(path: str | Path, log: LogFn = None) -> dict[str, int]:
    """Upsert Employees by Employee_ID. Does not touch historical attendance or locks."""
    from processing.master_data import load_master_excel

    frame = load_master_excel(path, log=log)
    inserted = 0
    updated = 0
    skipped = 0
    if frame is None or frame.empty:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    for _, rec in frame.iterrows():
        data = rec.to_dict()
        code = str(data.get("employee_id") or "").strip()
        key = str(data.get("name_key") or name_match_key(str(data.get("employee_name") or "")))
        existing = find_employee_by_code(code) if code else None
        if existing is None and key:
            existing = find_employee_by_name_key(key)
        if existing is None:
            upsert_employee(data)
            inserted += 1
            continue
        update_employee_master_fields(int(existing["id"]), data)
        updated += 1
    if log:
        log(
            f"Đồng bộ Excel → CSDL ({Path(path).name}): thêm {inserted}, "
            f"cập nhật {updated}. Không đụng attendance_days / kỳ đã chốt."
        )
        if skipped:
            log(f"Bỏ qua {skipped} dòng Excel không hợp lệ.")
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def import_employees_from_excel(path: str | Path, log: LogFn = None) -> int:
    """Seed/sync wrapper used on first launch. Returns inserted + updated."""
    stats = sync_employees_from_excel(path, log=log)
    return int(stats["inserted"] + stats["updated"])


def list_holidays(year: int | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM holidays"
    params: tuple = ()
    if year is not None:
        sql += " WHERE year = ?"
        params = (int(year),)
    sql += " ORDER BY holiday_date"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


def upsert_holiday(payload: dict) -> int:
    day = payload.get("holiday_date")
    if isinstance(day, datetime):
        day = day.date()
    if isinstance(day, date):
        day_text = day.isoformat()
        year = day.year
    else:
        day_text = str(day or "").strip()[:10]
        year = int(payload.get("year") or day_text[:4] or date.today().year)
    if len(day_text) != 10:
        raise ValueError("Ngày lễ phải dạng YYYY-MM-DD.")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Tên ngày lễ không được trống.")
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO holidays (holiday_date, name, kind, paid, source, year)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(holiday_date) DO UPDATE SET
                name = excluded.name,
                kind = excluded.kind,
                paid = excluded.paid,
                source = excluded.source,
                year = excluded.year
            """,
            (
                day_text,
                name,
                str(payload.get("kind") or "public"),
                1 if payload.get("paid", True) else 0,
                str(payload.get("source") or "manual"),
                year,
            ),
        )
        row = conn.execute("SELECT id FROM holidays WHERE holiday_date = ?", (day_text,)).fetchone()
        return int(row["id"])


def delete_holiday(holiday_pk: int) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM holidays WHERE id = ?", (int(holiday_pk),))


def replace_auto_holidays(year: int, rows: list[dict]) -> int:
    """Refresh generated public holidays for a year; keep manual company days."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM holidays WHERE year = ? AND source = 'auto'", (int(year),))
    count = 0
    for item in rows:
        item = dict(item)
        item["source"] = "auto"
        item["year"] = int(year)
        upsert_holiday(item)
        count += 1
    return count


def paid_holiday_map(start: date, end: date) -> dict[date, str]:
    """Paid holiday dates in [start, end] inclusive."""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT holiday_date, name FROM holidays
            WHERE paid = 1 AND holiday_date >= ? AND holiday_date <= ?
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    out: dict[date, str] = {}
    for row in rows:
        day = date.fromisoformat(str(row["holiday_date"]))
        out[day] = str(row["name"])
    return out


def holiday_name_on(day: date) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM holidays WHERE holiday_date = ? AND paid = 1",
            (day.isoformat(),),
        ).fetchone()
    return str(row["name"]) if row else ""


class MonthLockedError(RuntimeError):
    """Raised when analysis or edits would overwrite a locked payroll month."""


def get_employee(employee_pk: int) -> Optional[dict]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM employees WHERE id = ?", (int(employee_pk),)).fetchone()
    return dict(row) if row else None


def update_employee_hr_fields(
    employee_pk: int,
    join_date: Any = None,
    carryover_leave: Any = None,
    base_leave: Any = None,
    official_start_date: Any = None,
) -> None:
    """Patch join date / leave balances without touching shift fields."""
    current = get_employee(employee_pk)
    if current is None:
        raise ValueError("Không tìm thấy nhân viên.")
    payload = dict(current)
    payload["id"] = employee_pk
    if join_date is not None:
        payload["join_date"] = join_date
    if official_start_date is not None:
        payload["official_start_date"] = official_start_date
    if carryover_leave is not None:
        payload["carryover_leave"] = carryover_leave
    if base_leave is not None:
        payload["base_leave"] = base_leave
    upsert_employee(payload)


def lock_info(month_year: str) -> Optional[dict]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM payroll_locks WHERE month_year = ?", (month_year,)).fetchone()
    return dict(row) if row else None


def is_month_locked(month_year: str) -> bool:
    info = lock_info(month_year)
    return bool(info and info.get("is_locked"))


def lock_month(month_year: str, locked_by: str = "HR") -> dict:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO payroll_locks (month_year, is_locked, locked_at, locked_by)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(month_year) DO UPDATE SET
                is_locked = 1,
                locked_at = excluded.locked_at,
                locked_by = excluded.locked_by
            """,
            (month_year, _now(), locked_by or "HR"),
        )
    set_setting("last_dashboard_month", month_year)
    return lock_info(month_year) or {}


def unlock_month(month_year: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE payroll_locks SET is_locked = 0 WHERE month_year = ?",
            (month_year,),
        )


def list_attendance_months() -> list[str]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT month_year FROM attendance_days
            UNION
            SELECT month_year FROM attendance_history
            """
        ).fetchall()
    return sorted({str(row["month_year"]) for row in rows}, reverse=True)


def replace_attendance_month(month_year: str, rows: list[dict], *, ignore_lock: bool = False) -> int:
    if not ignore_lock and is_month_locked(month_year):
        raise MonthLockedError(
            f"Kỳ {month_year} đã chốt công. Không được ghi đè dữ liệu tháng này."
        )
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM attendance_days WHERE month_year = ?", (month_year,))
        count = 0
        for item in rows:
            day = date_text(item.get("work_date") or item.get("date"))
            if not day:
                continue
            conn.execute(
                """
                INSERT INTO attendance_days (
                    month_year, name_key, employee_name, work_date,
                    standardized_workday, overtime_hours, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    month_year,
                    str(item.get("name_key") or name_match_key(str(item.get("employee_name") or ""))),
                    str(item.get("employee_name") or ""),
                    day,
                    float(item.get("standardized_workday") or 0),
                    float(item.get("overtime_hours") or 0),
                    str(item.get("notes") or ""),
                ),
            )
            count += 1
    set_setting("last_dashboard_month", month_year)
    return count


def replace_attendance_history(month_year: str, rows: list[dict], source_file: str = "") -> int:
    """Upsert monthly aggregates imported from a finalized Chi tiết workbook."""
    init_db()
    now = _now()
    with connect() as conn:
        conn.execute("DELETE FROM attendance_history WHERE month_year = ?", (month_year,))
        count = 0
        for item in rows:
            key = str(item.get("name_key") or name_match_key(str(item.get("employee_name") or "")))
            if not key:
                continue
            conn.execute(
                """
                INSERT INTO attendance_history (
                    month_year, name_key, employee_id, employee_name,
                    workdays, used_leave, overtime_hours, source_file, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    month_year,
                    key,
                    str(item.get("employee_id") or ""),
                    str(item.get("employee_name") or ""),
                    float(item.get("workdays") or 0),
                    float(item.get("used_leave") or 0),
                    float(item.get("overtime_hours") or 0),
                    str(source_file or ""),
                    now,
                ),
            )
            count += 1
    return count


def month_totals(month_year: str) -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(standardized_workday), 0) AS workdays,
                COALESCE(SUM(overtime_hours), 0) AS overtime
            FROM attendance_days
            WHERE month_year = ?
            """,
            (month_year,),
        ).fetchone()
    return {
        "workdays": float(row["workdays"] if row else 0),
        "overtime": float(row["overtime"] if row else 0),
    }


def employee_month_stats(month_year: str) -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT name_key, employee_name,
                   COALESCE(SUM(standardized_workday), 0) AS workdays,
                   COALESCE(SUM(overtime_hours), 0) AS overtime
            FROM attendance_days
            WHERE month_year = ?
            GROUP BY name_key
            ORDER BY employee_name COLLATE NOCASE
            """,
            (month_year,),
        ).fetchall()
    return [dict(row) for row in rows]


def persist_attendance_from_merged(merged, year: int, month: int) -> int:
    """Write one analysis month into attendance_days (blocked if locked)."""
    key = month_key(year, month)
    if merged is None or getattr(merged, "empty", True):
        return replace_attendance_month(key, [])
    rows = []
    for _, rec in merged.iterrows():
        day = rec.get("date")
        rows.append(
            {
                "name_key": rec.get("name_key") or name_match_key(str(rec.get("employee_name") or "")),
                "employee_name": rec.get("employee_name") or "",
                "work_date": day,
                "standardized_workday": rec.get("standardized_workday") or 0,
                "overtime_hours": rec.get("overtime_hours") or 0,
                "notes": rec.get("notes") or "",
            }
        )
    return replace_attendance_month(key, rows)
