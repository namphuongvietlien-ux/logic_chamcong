"""Map punches to K9 4-clock columns, standard công, and overtime hours."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import pandas as pd

from processing.utils import name_match_key, parse_time_value

STANDARD_HOURS = 8.0
FULL_DAY_HOURS = 7.0
TWO_THIRD_HOURS = 6.0
WEEKDAY_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def _as_time(value: Any) -> Optional[time]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, timedelta):
        total = int(value.total_seconds()) % 86400
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return time(h, m, s)
    return parse_time_value(value)


def _hours_between(start: time, end: time) -> float:
    s = datetime.combine(date(2000, 1, 1), start)
    e = datetime.combine(date(2000, 1, 1), end)
    if e < s:
        e += timedelta(days=1)
    return (e - s).total_seconds() / 3600.0


def punches_from_row(row: Any) -> list[time]:
    if isinstance(row, dict):
        raw = row.get("punches") or ()
    elif hasattr(row, "index") and "punches" in row.index:
        raw = row["punches"]
    else:
        raw = ()
    try:
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            raw = ()
    except (TypeError, ValueError):
        raw = ()
    times: list[time] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            parsed = _as_time(item)
            if parsed and parsed not in times:
                times.append(parsed)
    return times


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "index") and key in row.index:
        return row[key]
    return None


def collect_punches(row: Any) -> list[time]:
    """All fingerprint + OCR punches, sorted unique."""
    times = punches_from_row(row)
    for key in ("fingerprint_in", "fingerprint_out", "photo_in", "photo_out"):
        parsed = _as_time(_row_get(row, key))
        if parsed and parsed not in times:
            times.append(parsed)
    return sorted(times)


def last_out_time(in1: Optional[time], out1: Optional[time], in2: Optional[time], out2: Optional[time]) -> Optional[time]:
    del in1, in2
    for value in (out2, out1):
        if value is not None:
            return value
    return None


def four_clocks(times: list[time]) -> tuple[Optional[time], Optional[time], Optional[time], Optional[time]]:
    """Map sorted punches to In1, Out1, In2, Out2."""
    if not times:
        return None, None, None, None
    if len(times) == 1:
        return times[0], None, None, None
    if len(times) == 2:
        return times[0], None, None, times[1]
    if len(times) == 3:
        return times[0], times[1], None, times[2]
    return times[0], times[1], times[2], times[-1]


def session_clocks(row: Any) -> tuple[Optional[time], Optional[time], Optional[time], Optional[time]]:
    """In1, Out1, In2, Out2 from combined fingerprint + photo punches."""
    times = collect_punches(row)
    d, e, f, g = four_clocks(times)
    if d is not None and d == g:
        if d < time(12, 0):
            g = None
        else:
            d = None
    if d is None and g is None:
        final_in = _as_time(row["final_check_in"] if hasattr(row, "index") and "final_check_in" in row.index else None)
        final_out = _as_time(row["final_check_out"] if hasattr(row, "index") and "final_check_out" in row.index else None)
        d, g = final_in, final_out
    return d, e, f, g


def clocks_from_row(row: Any) -> tuple[Optional[time], Optional[time], Optional[time], Optional[time]]:
    """Prefer stored In1/Out1/In2/Out2 (after manual edit) over re-mapping punches."""
    in1 = _as_time(_row_get(row, "in1"))
    out1 = _as_time(_row_get(row, "out1"))
    in2 = _as_time(_row_get(row, "in2"))
    out2 = _as_time(_row_get(row, "out2"))
    if in1 is not None or out1 is not None or in2 is not None or out2 is not None:
        return in1, out1, in2, out2
    return session_clocks(row)


def _minutes_positive(start: Optional[time], end: Optional[time]) -> int:
    """Minutes from start to end; 0 if end is not after start."""
    if not start or not end:
        return 0
    if end <= start:
        return 0
    return int(round(_hours_between(start, end) * 60))


def late_minutes(in1: Optional[time], shift_start: Optional[time]) -> int:
    """Max(0, In1 - Shift Start) in minutes."""
    return _minutes_positive(shift_start, in1)


def early_minutes(last_out: Optional[time], shift_end: Optional[time]) -> int:
    """Max(0, Shift End - Last Out) in minutes."""
    return _minutes_positive(last_out, shift_end)


def late_return_minutes(last_out: Optional[time], shift_end: Optional[time]) -> int:
    """Về trễ: minutes after shift end."""
    return _minutes_positive(shift_end, last_out)


def lunch_overlap_hours(
    check_in: Optional[time],
    check_out: Optional[time],
    lunch_start: Optional[time],
    lunch_end: Optional[time],
    work_date: Optional[date] = None,
) -> float:
    """Overlap between actual work interval and configured lunch window, in hours."""
    if not check_in or not check_out or not lunch_start or not lunch_end:
        return 0.0
    day = work_date or date(2000, 1, 1)
    work_s = datetime.combine(day, check_in)
    work_e = datetime.combine(day, check_out)
    if work_e <= work_s:
        work_e += timedelta(days=1)
    lunch_s = datetime.combine(day, lunch_start)
    lunch_e = datetime.combine(day, lunch_end)
    if lunch_e <= lunch_s:
        lunch_e += timedelta(days=1)
    start = max(work_s, lunch_s)
    end = min(work_e, lunch_e)
    if end <= start:
        return 0.0
    return round((end - start).total_seconds() / 3600.0, 4)


def filled_clocks(
    in1: Optional[time],
    out1: Optional[time],
    in2: Optional[time],
    out2: Optional[time],
) -> int:
    return sum(1 for value in (in1, out1, in2, out2) if value is not None)


MISSING_LUNCH_PUNCH_HOURS = 1.0
FULL_DAY_RATIO = 0.85
HALF_DAY_RATIO = 0.45
LUNCH_SPAN_HOURS = 5.0


def safe_shift_hours(standard_shift_hours: Any, default: float = STANDARD_HOURS) -> float:
    """Cast master shift length to float; never return 0 (avoids division by zero)."""
    try:
        if standard_shift_hours is None or (isinstance(standard_shift_hours, float) and pd.isna(standard_shift_hours)):
            hours = default
        else:
            hours = float(standard_shift_hours)
    except (TypeError, ValueError):
        hours = default
    if hours <= 0:
        return float(default)
    return hours


def _as_hours(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        if pd.isna(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def has_in_out_pair(
    in1: Optional[time],
    out1: Optional[time],
    in2: Optional[time],
    out2: Optional[time],
) -> bool:
    """True only when the day has at least one IN and one OUT (a complete pair)."""
    has_in = in1 is not None or in2 is not None
    has_out = out1 is not None or out2 is not None
    return has_in and has_out


def first_in_time(
    in1: Optional[time],
    out1: Optional[time],
    in2: Optional[time],
    out2: Optional[time],
) -> Optional[time]:
    for value in (in1, in2):
        if value is not None:
            return value
    return None


def _lunch_deduct_hours(
    lunch_duration_hours: Optional[float],
    unset_lunch_hours: float = 0.0,
) -> float:
    amount = _as_hours(lunch_duration_hours)
    if amount <= 0:
        return max(float(unset_lunch_hours or 0), 0.0)
    return amount


def hours_from_clocks(
    in1: Optional[time],
    out1: Optional[time],
    in2: Optional[time],
    out2: Optional[time],
    lunch_duration_hours: Optional[float] = None,
    logical_date: Optional[date] = None,
    unset_lunch_hours: float = 0.0,
    overnight: bool = False,
    standard_shift_hours: float = 8.0,
) -> tuple[float, float]:
    """Net hours from clocks on one calendar day.

    Missing OUT (no overnight flag) → that pair contributes 0; never pull the next day.
    Overnight=True → last OUT is Day N+1, duration = that datetime − Day N IN.
    
    For 8h shifts: If enough hours worked, counts as full day even without lunch break.
    For 12h shifts: Must have lunch break, otherwise flagged for review.
    """
    from processing.sessions import clocks_to_datetimes, hours_from_datetimes

    in1, out1, in2, out2 = _as_time(in1), _as_time(out1), _as_time(in2), _as_time(out2)
    if not has_in_out_pair(in1, out1, in2, out2):
        return 0.0, 0.0
    if overnight:
        stamps = clocks_to_datetimes(logical_date, in1, out1, in2, out2, overnight=True)
        return hours_from_datetimes(stamps, 0.0, unset_lunch_hours=0.0, standard_shift_hours=standard_shift_hours)
    if in1 is not None and out1 is None and out2 is None:
        return 0.0, 0.0
    last_out = last_out_time(in1, out1, in2, out2)
    first_in = first_in_time(in1, out1, in2, out2)
    if first_in and last_out and last_out < first_in:
        return 0.0, 0.0
    stamps = clocks_to_datetimes(logical_date, in1, out1, in2, out2, overnight=False)
    return hours_from_datetimes(stamps, lunch_duration_hours, unset_lunch_hours=unset_lunch_hours, standard_shift_hours=standard_shift_hours)


def deducted_lunch_hours(
    check_in: Optional[time],
    check_out: Optional[time],
    lunch_duration: float = 0.0,
    lunch_start: Optional[time] = None,
    lunch_end: Optional[time] = None,
    in1: Optional[time] = None,
    out1: Optional[time] = None,
    in2: Optional[time] = None,
    out2: Optional[time] = None,
) -> float:
    """Lunch deduct from punch pattern, not a default 12:00–13:00 window."""
    if in1 is not None or out1 is not None or in2 is not None or out2 is not None:
        _, deducted = hours_from_clocks(in1, out1, in2, out2, lunch_duration)
        return deducted
    if not check_in or not check_out:
        return 0.0
    return 0.0


def actual_work_hours(
    check_in: Optional[time],
    check_out: Optional[time],
    lunch_start: Optional[time] = None,
    lunch_end: Optional[time] = None,
    work_date: Optional[date] = None,
    lunch_duration_hours: Optional[float] = None,
    in1: Optional[time] = None,
    out1: Optional[time] = None,
    in2: Optional[time] = None,
    out2: Optional[time] = None,
) -> float:
    if in1 is not None or out2 is not None or out1 is not None or in2 is not None:
        hours, _ = hours_from_clocks(in1, out1, in2, out2, lunch_duration_hours)
        return hours
    if not check_in or not check_out:
        return 0.0
    span = _hours_between(check_in, check_out)
    if span > LUNCH_SPAN_HOURS:
        amount = _lunch_deduct_hours(lunch_duration_hours)
        return round(max(span - amount, 0.0), 4)
    return round(max(span, 0.0), 4)


def workday_count(
    hours: Optional[float],
    standard_shift_hours: float,
    check_in: Optional[time] = None,
    check_out: Optional[time] = None,
) -> float:
    """Công from Actual Hours / Standard Shift Hours only.

    0 hours → 0 công. Ratio >= 0.85 → 1.0; >= 0.45 → 0.5; else 0.
    check_in/check_out are ignored (kept for call-site compatibility).
    """
    del check_in, check_out
    actual = _as_hours(hours)
    if actual <= 0:
        return 0.0
    std = safe_shift_hours(standard_shift_hours)
    ratio = actual / std
    if ratio >= FULL_DAY_RATIO:
        return 1.0
    if ratio >= HALF_DAY_RATIO:
        return 0.5
    return 0.0


def standardized_workday(
    hours: Optional[float],
    standard_shift_hours: float,
    check_in: Optional[time] = None,
    check_out: Optional[time] = None,
) -> float:
    return workday_count(hours, standard_shift_hours, check_in, check_out)


def net_hours(
    d: Optional[time],
    e: Optional[time],
    f: Optional[time],
    g: Optional[time],
    lunch_duration_hours: Optional[float] = None,
    logical_date: Optional[date] = None,
    overnight: bool = False,
    standard_shift_hours: float = 8.0,
) -> float:
    """Net hours from punches. Missing IN/OUT pair → 0. Overnight OUT is Day N+1 only if flagged."""
    hours, _ = hours_from_clocks(
        d, e, f, g, lunch_duration_hours, logical_date=logical_date, overnight=overnight,
        standard_shift_hours=standard_shift_hours
    )
    return hours


def is_paid_holiday(work_date: Optional[date]) -> bool:
    if work_date is None:
        return False
    try:
        from processing.database import holiday_name_on

        return bool(holiday_name_on(work_date))
    except Exception:
        return False


def cong_standard(
    hours: Optional[float],
    work_date: date,
    has_punch: bool,
    standard_shift_hours: float = STANDARD_HOURS,
) -> Any:
    """CCONG TH cell: 1 / 0.5 / 0 / N — same ratio rules as workday_count."""
    if not has_punch:
        if is_paid_holiday(work_date):
            return 1
        return "N" if work_date.weekday() == 6 else None
    cong = workday_count(hours, standard_shift_hours)
    if cong >= 1.0:
        return 1
    if cong >= 0.5:
        return 0.5
    return 0


def overtime_hours(hours: Optional[float], standard_shift_hours: float = STANDARD_HOURS) -> float:
    """Daily overtime = max(0, Actual Hours - Standard Shift Hours)."""
    actual = _as_hours(hours)
    std = safe_shift_hours(standard_shift_hours)
    return round(max(0.0, actual - std), 2)


def weekday_label(work_date: date) -> str:
    return WEEKDAY_VN[work_date.weekday()]


def month_from_merged(merged: pd.DataFrame) -> tuple[int, int]:
    """Most common year-month in the frame (not max date — OCR must not pull the grid forward)."""
    from processing.period import period_from_dates

    if merged is None or merged.empty or "date" not in merged.columns:
        today = date.today()
        return today.year, today.month
    pair = period_from_dates(merged["date"])
    if pair:
        return pair
    today = date.today()
    return today.year, today.month


def attendance_index(merged: pd.DataFrame) -> dict[tuple[str, date], pd.Series]:
    index: dict[tuple[str, date], pd.Series] = {}
    if merged is None or merged.empty:
        return index
    for _, row in merged.iterrows():
        name = row.get("employee_name")
        day = row.get("date")
        if name is None or day is None or (isinstance(day, float) and pd.isna(day)):
            continue
        if isinstance(day, datetime):
            day = day.date()
        index[(name_match_key(name), day)] = row
    return index
