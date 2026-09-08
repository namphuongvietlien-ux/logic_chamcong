"""Leave balances, paid vs unpaid types, half-day sessions, and request CRUD."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from processing.database import (
    _now,
    connect,
    get_employee,
    init_db,
    list_employees,
    paid_holiday_map,
    parse_iso_date,
    parse_month_key,
)
from processing.holidays import ensure_holiday_years
from processing.utils import name_match_key

LEAVE_TYPE_PAID = "Phép năm (Có lương)"
LEAVE_TYPE_UNPAID = "Nghỉ không lương"
LEAVE_TYPE_BHXH = "Ốm đau/Thai sản (BHXH)"
LEAVE_TYPE_SPECIAL = "Hiếu Hỉ"
LEAVE_TYPE_PAID_LEGACY = "Phép năm"
LEAVE_TYPE_UNPAID_LEGACY = "Không lương"

LEAVE_TYPES = (
    LEAVE_TYPE_PAID,
    LEAVE_TYPE_UNPAID,
    LEAVE_TYPE_BHXH,
    LEAVE_TYPE_SPECIAL,
)
ANNUAL_LEAVE = LEAVE_TYPE_PAID
STATUSES = ("approved", "pending", "rejected")

SESSION_FULL = "Cả ngày"
SESSION_AM = "Sáng"
SESSION_PM = "Chiều"
SESSION_UI = ("Cả ngày", "Buổi Sáng", "Buổi Chiều")

REMARK_PAID = "Nghỉ phép năm"
REMARK_UNPAID = "Nghỉ không lương"
REMARK_PAID_HALF = "Nghỉ phép nửa ngày"
REMARK_UNPAID_HALF = "Nghỉ KL nửa ngày"

_APPROVED_CACHE: list[dict] | None = None


def invalidate_leave_cache() -> None:
    global _APPROVED_CACHE
    _APPROVED_CACHE = None


def normalize_leave_type(value: Any) -> str:
    text = str(value or "").strip()
    if text in (LEAVE_TYPE_PAID_LEGACY, "Phep nam", "Phép năm hưởng lương"):
        return LEAVE_TYPE_PAID
    if text in (LEAVE_TYPE_UNPAID_LEGACY, "Nghi khong luong"):
        return LEAVE_TYPE_UNPAID
    return text or LEAVE_TYPE_PAID


def is_paid_annual(leave_type: Any) -> bool:
    return normalize_leave_type(leave_type) == LEAVE_TYPE_PAID


def is_unpaid_leave(leave_type: Any) -> bool:
    return normalize_leave_type(leave_type) == LEAVE_TYPE_UNPAID


def is_authorized_absence(leave_type: Any) -> bool:
    return normalize_leave_type(leave_type) in {
        LEAVE_TYPE_PAID,
        LEAVE_TYPE_UNPAID,
        LEAVE_TYPE_BHXH,
        LEAVE_TYPE_SPECIAL,
    }


def normalize_session(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("Buổi Sáng", "Sáng", "Morning", "AM"):
        return SESSION_AM
    if text in ("Buổi Chiều", "Chiều", "Afternoon", "PM"):
        return SESSION_PM
    return SESSION_FULL


def session_label(value: Any) -> str:
    session = normalize_session(value)
    if session == SESSION_AM:
        return "Buổi Sáng"
    if session == SESSION_PM:
        return "Buổi Chiều"
    return SESSION_FULL


def session_duration(value: Any) -> float:
    return 0.5 if normalize_session(value) in (SESSION_AM, SESSION_PM) else 1.0


def leave_duration(item: dict | None) -> float:
    if not item:
        return 1.0
    session = normalize_session(item.get("session"))
    if session in (SESSION_AM, SESSION_PM):
        return 0.5
    raw = item.get("duration")
    try:
        amount = float(raw) if raw is not None and raw != "" else 1.0
    except (TypeError, ValueError):
        amount = 1.0
    if 0 < amount < 1:
        return 0.5
    return 1.0


def is_half_day(item: dict | None) -> bool:
    return leave_duration(item) < 1


def remark_for_leave(leave_type: Any, duration: float | None = None, item: dict | None = None) -> str:
    kind = normalize_leave_type(leave_type if leave_type is not None else (item or {}).get("leave_type"))
    amount = leave_duration(item) if duration is None and item is not None else float(duration or 1.0)
    half = 0 < amount < 1
    if kind == LEAVE_TYPE_PAID:
        return REMARK_PAID_HALF if half else REMARK_PAID
    if kind == LEAVE_TYPE_UNPAID:
        return REMARK_UNPAID_HALF if half else REMARK_UNPAID
    if half:
        return f"{kind} (nửa ngày)"
    return kind


def leave_cong_credit(leave_type: Any, duration: float) -> float:
    """Công granted by the leave itself (worked time is added separately for half-days)."""
    if is_unpaid_leave(leave_type):
        return 0.0
    if is_paid_annual(leave_type):
        return 0.5 if 0 < float(duration) < 1 else 1.0
    return 0.0


def seniority_leave(join_date: Any, year: int | None = None) -> int:
    """1 extra day per 5 calendar years since join year. Empty join date → 0."""
    joined = parse_iso_date(join_date)
    if joined is None:
        return 0
    as_of = int(year or date.today().year)
    return max(0, (as_of - joined.year) // 5)


def official_start_of(employee: dict) -> Any:
    """Official start wins; fall back to join date for legacy rows."""
    official = employee.get("official_start_date")
    if str(official or "").strip():
        return official
    return employee.get("join_date")


def prorated_base_leave(
    official_start_date: Any,
    base_leave: Any = 12,
    year: int | None = None,
) -> float:
    """Prorate Base_Leave by official-start month in the current leave year.

    Same year: (Base / 12) * (12 - Month + 1).
    Past year: full Base. Future year (still on probation): 0.
    """
    try:
        base = float(base_leave if base_leave not in (None, "") else 12)
    except (TypeError, ValueError):
        base = 12.0
    as_of = int(year or date.today().year)
    started = parse_iso_date(official_start_date)
    if started is None:
        return round(base, 2)
    if started.year > as_of:
        return 0.0
    if started.year < as_of:
        return round(base, 2)
    months = 12 - started.month + 1
    return round((base / 12.0) * months, 2)


def working_days_between(start: date, end: date) -> float:
    """Mon–Sat minus paid public holidays (VN annual-leave working days)."""
    if end < start:
        start, end = end, start
    ensure_holiday_years(list(range(start.year, end.year + 1)))
    holidays = paid_holiday_map(start, end)
    count = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() != 6 and cursor not in holidays:
            count += 1
        cursor += timedelta(days=1)
    return float(count)


def iter_leave_dates(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    ensure_holiday_years(list(range(start.year, end.year + 1)))
    holidays = paid_holiday_map(start, end)
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() != 6 and cursor not in holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _as_date(value: Any) -> Optional[date]:
    return parse_iso_date(value)


def _chargeable_overlap(item: dict, range_start: date, range_end: date) -> float:
    start = _as_date(item.get("start_date"))
    end = _as_date(item.get("end_date"))
    if start is None or end is None:
        return 0.0
    overlap_start = max(start, range_start)
    overlap_end = min(end, range_end)
    if overlap_start > overlap_end:
        return 0.0
    duration = leave_duration(item)
    if start >= range_start and end <= range_end:
        try:
            return float(item.get("days") or 0)
        except (TypeError, ValueError):
            return working_days_between(overlap_start, overlap_end) * duration
    return working_days_between(overlap_start, overlap_end) * duration


def used_leave(employee_pk: int, year: int | None = None) -> float:
    """Sum of approved paid annual-leave days (including 0.5 half-days) in the year."""
    year = int(year or date.today().year)
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    total = 0.0
    for item in list_leave_requests(employee_pk):
        if not is_paid_annual(item.get("leave_type")) or item.get("status") != "approved":
            continue
        total += _chargeable_overlap(item, year_start, year_end)
    return round(total, 2)


def used_leave_by_employee(year: int | None = None) -> dict[int, float]:
    """One query: approved paid-annual days in the year, keyed by employee PK."""
    year = int(year or date.today().year)
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    totals: dict[int, float] = {}
    for item in list_leave_requests():
        if not is_paid_annual(item.get("leave_type")) or item.get("status") != "approved":
            continue
        try:
            pk = int(item["employee_id"])
        except (TypeError, ValueError, KeyError):
            continue
        totals[pk] = totals.get(pk, 0.0) + _chargeable_overlap(item, year_start, year_end)
    return {pk: round(amount, 2) for pk, amount in totals.items()}


def leave_balance(employee: dict | int, year: int | None = None) -> dict:
    year = int(year or date.today().year)
    if isinstance(employee, int):
        row = get_employee(employee)
        if row is None:
            raise ValueError("Không tìm thấy nhân viên.")
        employee = row
    base = int(employee.get("base_leave") or 12)
    carry = float(employee.get("carryover_leave") or 0)
    senior = seniority_leave(employee.get("join_date"), year)
    prorated = prorated_base_leave(official_start_of(employee), base, year)
    used = used_leave(int(employee["id"]), year)
    entitlement = prorated + senior + carry
    remaining = entitlement - used
    return {
        "base": base,
        "prorated_base": prorated,
        "seniority": senior,
        "carryover": carry,
        "entitlement": entitlement,
        "total_leave": entitlement,
        "used": used,
        "remaining": remaining,
        "year": year,
        "formula": f"{prorated:g} + {senior} + {carry:g} - {used:g} = {remaining:g}",
    }


def company_remaining_leave(year: int | None = None) -> float:
    year = int(year or date.today().year)
    total = 0.0
    for item in list_employees(active_only=True):
        total += leave_balance(item, year)["remaining"]
    return round(total, 2)


def approved_leave_days_in_month(month_year: str, leave_type: str | None = None) -> float:
    year, month = parse_month_key(month_year)
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year, 12, 31)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)
    total = 0.0
    wanted = normalize_leave_type(leave_type) if leave_type else None
    for item in list_leave_requests():
        if item.get("status") != "approved":
            continue
        if wanted and normalize_leave_type(item.get("leave_type")) != wanted:
            continue
        total += _chargeable_overlap(item, month_start, month_end)
    return round(total, 2)


def used_leave_in_month(employee_pk: int, month_year: str) -> float:
    year, month = parse_month_key(month_year)
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year, 12, 31)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)
    total = 0.0
    for item in list_leave_requests(employee_pk):
        if not is_paid_annual(item.get("leave_type")) or item.get("status") != "approved":
            continue
        total += _chargeable_overlap(item, month_start, month_end)
    return round(total, 2)


def list_leave_requests(employee_pk: int | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM leave_requests"
    params: tuple = ()
    if employee_pk is not None:
        sql += " WHERE employee_id = ?"
        params = (int(employee_pk),)
    sql += " ORDER BY start_date DESC, id DESC"
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


def fetch_approved_leaves(*, refresh: bool = False) -> list[dict]:
    global _APPROVED_CACHE
    if _APPROVED_CACHE is not None and not refresh:
        return _APPROVED_CACHE
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT lr.*, e.employee_name, e.name_key
            FROM leave_requests lr
            JOIN employees e ON e.id = lr.employee_id
            WHERE lr.status = 'approved'
            """
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["leave_type"] = normalize_leave_type(item.get("leave_type"))
        item["session"] = normalize_session(item.get("session"))
        item["duration"] = leave_duration(item)
        item["start_date"] = _as_date(item.get("start_date"))
        item["end_date"] = _as_date(item.get("end_date"))
        item["name_key"] = str(item.get("name_key") or name_match_key(item.get("employee_name") or ""))
        out.append(item)
    _APPROVED_CACHE = out
    return out


def leave_covering(
    name: str,
    work_date: date | None,
    approved: list[dict] | None = None,
) -> dict | None:
    """Return the approved leave covering this employee on this calendar date."""
    key = name_match_key(name)
    if not key or work_date is None:
        return None
    rows = approved if approved is not None else fetch_approved_leaves()
    for row in rows:
        if str(row.get("name_key") or "") != key:
            continue
        start = row.get("start_date")
        end = row.get("end_date")
        if isinstance(start, date) and isinstance(end, date) and start <= work_date <= end:
            return row
    return None


def apply_leave_to_day(data: dict, leave: dict | None) -> dict:
    """Overlay paid/unpaid/half-day leave onto an already-computed attendance row."""
    if not leave:
        data.setdefault("is_leave", False)
        data.setdefault("leave_duration", 0)
        data.setdefault("leave_type", "")
        data.setdefault("leave_session", "")
        return data
    punches = any(data.get(key) for key in ("in1", "out1", "in2", "out2"))
    if data.get("is_holiday") and not punches:
        return data
    kind = normalize_leave_type(leave.get("leave_type"))
    duration = leave_duration(leave)
    remark = remark_for_leave(kind, duration)
    worked = 0.0
    try:
        worked = float(data.get("standardized_workday") or 0)
    except (TypeError, ValueError):
        worked = 0.0
    credit = leave_cong_credit(kind, duration)
    data["is_leave"] = True
    data["leave_type"] = kind
    data["leave_duration"] = duration
    data["leave_session"] = normalize_session(leave.get("session"))
    if duration < 1:
        data["standardized_workday"] = round(worked + credit, 2)
    else:
        data["standardized_workday"] = credit
        if not punches:
            data["actual_work_hours"] = 0.0
            data["deducted_lunch_hours"] = 0.0
            data["overtime_hours"] = 0.0
            data["late_minutes"] = 0
            data["early_minutes"] = 0
            data["late_return_minutes"] = 0
    keep: list[str] = []
    for part in str(data.get("notes") or "").split(";"):
        text = part.strip()
        if not text or text in {
            "Thiếu giờ ra",
            "Thiếu giờ vào",
            "Missing In",
            "Missing lunch punch",
            "No Fingerprint",
            "No Photo",
        }:
            continue
        if text == remark:
            continue
        keep.append(text)
    data["notes"] = "; ".join([remark] + keep) if keep else remark
    return data


def create_leave_request(payload: dict) -> int:
    leave_type = normalize_leave_type(payload.get("leave_type"))
    if leave_type not in LEAVE_TYPES:
        raise ValueError("Loại phép phải là: " + ", ".join(LEAVE_TYPES))
    status = str(payload.get("status") or "approved").strip() or "approved"
    if status not in STATUSES:
        raise ValueError("Trạng thái không hợp lệ.")
    start = parse_iso_date(payload.get("start_date"))
    end = parse_iso_date(payload.get("end_date") or payload.get("start_date"))
    if start is None or end is None:
        raise ValueError("Ngày nghỉ phải dạng YYYY-MM-DD hoặc DD/MM/YYYY.")
    if end < start:
        raise ValueError("Ngày kết thúc phải sau hoặc bằng ngày bắt đầu.")
    employee_pk = int(payload.get("employee_id"))
    if get_employee(employee_pk) is None:
        raise ValueError("Không tìm thấy nhân viên.")
    session = normalize_session(payload.get("session"))
    duration = session_duration(session)
    raw_duration = payload.get("duration")
    if raw_duration not in (None, ""):
        try:
            duration = 0.5 if float(raw_duration) < 1 else duration
        except (TypeError, ValueError):
            pass
        if session in (SESSION_AM, SESSION_PM):
            duration = 0.5
    days = payload.get("days")
    working = working_days_between(start, end)
    if days in (None, ""):
        days = working * duration
    else:
        days = float(days)
    if days <= 0:
        raise ValueError("Khoảng ngày không có ngày phép hợp lệ (T2–T7, trừ lễ).")
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO leave_requests (
                employee_id, leave_type, start_date, end_date, days,
                duration, session, status, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                employee_pk,
                leave_type,
                start.isoformat(),
                end.isoformat(),
                float(days),
                float(duration),
                session,
                status,
                str(payload.get("note") or "").strip(),
                _now(),
            ),
        )
        request_id = int(cur.lastrowid)
    invalidate_leave_cache()
    return request_id


def delete_leave_request(request_pk: int) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM leave_requests WHERE id = ?", (int(request_pk),))
    invalidate_leave_cache()


def update_leave_status(request_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError("Trạng thái không hợp lệ.")
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE leave_requests SET status = ? WHERE id = ?",
            (status, int(request_id)),
        )
    invalidate_leave_cache()


def leave_period_labels(year: int | None = None) -> dict[str, str]:
    """HR-facing labels: carryover is always the previous calendar year."""
    year = int(year or date.today().year)
    prev = year - 1
    return {
        "year": str(year),
        "prev_year": str(prev),
        "carry": f"Tồn {prev}",
        "carry_long": f"Tồn năm {prev} (mang sang {year})",
        "remain": f"Còn lại {year}",
        "used": f"Đã nghỉ {year}",
        "hint": (
            f"Tồn {prev} = phép chưa dùng của năm {prev}, mang sang {year}. "
            f"Không dán cột “Còn lại” trên Excel vào ô này nếu số đó đã gồm phép năm {year}. "
            f"Còn lại {year} = phép {year} đã tỷ lệ + thâm niên + tồn {prev} − đã nghỉ {year}."
        ),
    }


def formula_text(balance: dict) -> str:
    labels = leave_period_labels(balance.get("year"))
    prorated = balance.get("prorated_base", balance.get("base"))
    base = balance.get("base")
    year = labels["year"]
    prev = labels["prev_year"]
    base_note = f"phép {year} đã tỷ lệ"
    if base is not None and abs(float(prorated) - float(base)) > 0.001:
        base_note = f"phép {year} đã tỷ lệ từ {float(base):g}"
    return (
        f"{float(prorated):g} ({base_note}) + {balance['seniority']:g} (thâm niên) + "
        f"{balance['carryover']:g} (tồn {prev}) − {balance['used']:g} ({labels['used']}) = "
        f"{balance['remaining']:g} {labels['remain']}"
    )
