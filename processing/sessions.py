"""Calendar-day punch grouping. Overnight (+1) is applied only when flagged manually."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional

import pandas as pd

from processing.utils import parse_time_value

DAYTIME_LUNCH_START = time(12, 0)
DAYTIME_LUNCH_END = time(13, 0)
LUNCH_IF_SPAN_HOURS = 6.0


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date) and not isinstance(value, datetime):
        return None
    return None


def _clock(value: Any) -> Optional[time]:
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
    return parse_time_value(value)


def combine_punch(day: date, clock: Any) -> Optional[datetime]:
    parsed = _clock(clock)
    if day is None or parsed is None:
        return None
    return datetime.combine(day, parsed)


NOISE_GAP = timedelta(minutes=5)
STATUS_MISSING_OUT = "Thiếu Giờ Ra"
STATUS_MISSING_IN = "Thiếu Giờ Vào"
STATUS_ODD_PUNCH = "Thiếu mốc quẹt"
STATUS_CROSS_DAY = "Quên quẹt ra (vượt 16 giờ)"
ODD_PUNCH_HOURS = -1.0
MAX_SHIFT_HOURS = 16.0
MIN_WORK_MINUTES = 15


def unique_sorted(datetimes: Iterable[datetime]) -> list[datetime]:
    """Exact-minute dedupe via set, then chronological sort (earliest → latest)."""
    normalized: set[datetime] = set()
    for raw in datetimes:
        dt = _as_datetime(raw) if not isinstance(raw, datetime) else raw.replace(microsecond=0)
        if dt is None:
            continue
        normalized.add(dt.replace(second=0, microsecond=0))
    return sorted(normalized)


def collapse_noise_punches(stamps: list[datetime]) -> list[datetime]:
    """Merge punches less than 5 minutes apart so they are not an In/Out pair.

    First cluster of the day = Check-In → keep the earliest.
    Last cluster of the day = Check-Out → keep the latest.
    A single burst (only noise, no real pair) stays one punch.
    """
    stamps = unique_sorted(stamps)
    if len(stamps) <= 1:
        return list(stamps)
    groups: list[list[datetime]] = [[stamps[0]]]
    for dt in stamps[1:]:
        if dt - groups[-1][-1] < NOISE_GAP:
            groups[-1].append(dt)
        else:
            groups.append([dt])

    def _pick(group: list[datetime], role: str) -> datetime:
        return min(group) if role == "in" else max(group)

    if len(groups) == 1:
        role = "in" if groups[0][0].hour < 12 else "out"
        return [_pick(groups[0], role)]
    last_i = len(groups) - 1
    collapsed: list[datetime] = []
    for index, group in enumerate(groups):
        if index == 0:
            collapsed.append(_pick(group, "in"))
        elif index == last_i:
            collapsed.append(_pick(group, "out"))
        else:
            collapsed.append(_pick(group, "out" if index % 2 else "in"))
    return collapsed


def merge_day_punches(datetimes: Iterable[datetime]) -> list[datetime]:
    """Fingerprint + OCR for one calendar day: set-dedupe, sort, 5-minute debounce.

    Check-In is always the first remaining stamp; Check-Out is the last.
    """
    return collapse_noise_punches(unique_sorted(datetimes))


def _pair_span_hours(in_time: datetime, out_time: datetime) -> float:
    """Duration of one In/Out pair. If OUT is earlier, treat as next-day wrap."""
    if out_time < in_time:
        out_time = out_time + timedelta(days=1)
    return (out_time - in_time).total_seconds() / 3600.0


def missing_punch_status(stamps: Iterable[datetime]) -> str:
    """1 punch → missing IN/OUT. Odd count → missing clock. Pair > 16h → cross-day trap."""
    items = unique_sorted(stamps or [])
    n = len(items)
    if n == 0:
        return ""
    if n == 1:
        return STATUS_MISSING_OUT if items[0].hour < 12 else STATUS_MISSING_IN
    if n % 2 == 1:
        return STATUS_ODD_PUNCH
    for i in range(0, n, 2):
        if _pair_span_hours(items[i], items[i + 1]) > MAX_SHIFT_HOURS:
            return STATUS_CROSS_DAY
    return ""


def cluster_sessions(
    datetimes: Iterable[datetime],
    gap_hours: float | None = None,
) -> list[list[datetime]]:
    """Group punches by physical calendar date. Never pull the next day's punches.

    Each day is set-deduped, sorted, then noise-collapsed (< 5 minutes).
    gap_hours is ignored (kept so old call sites still import this helper).
    """
    del gap_hours
    by_day: dict[date, list[datetime]] = {}
    for stamp in unique_sorted(datetimes):
        by_day.setdefault(stamp.date(), []).append(stamp)
    return [merge_day_punches(by_day[day]) for day in sorted(by_day)]


def four_clocks_dt(
    stamps: list[datetime],
) -> tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
    """FIRST stamp = Check-In, LAST stamp = Check-Out (after sort + noise collapse)."""
    if not stamps:
        return None, None, None, None
    if len(stamps) == 1:
        if stamps[0].hour >= 12:
            return None, None, None, stamps[0]
        return stamps[0], None, None, None
    if len(stamps) == 2:
        return stamps[0], None, None, stamps[1]
    if len(stamps) == 3:
        return stamps[0], stamps[1], None, stamps[2]
    return stamps[0], stamps[1], stamps[2], stamps[-1]


def clocks_to_datetimes(
    logical_date: Optional[date],
    in1,
    out1,
    in2,
    out2,
    overnight: bool = False,
) -> list[datetime]:
    """Rebuild datetimes from 4 clocks on one calendar day.

    Overnight is never inferred. Only when overnight=True does the last OUT
    belong to Day N+1 (manual night-shift toggle).
    """
    day = logical_date or date(2000, 1, 1)
    slots = [_clock(in1), _clock(out1), _clock(in2), _clock(out2)]
    last_out_idx: Optional[int] = None
    for idx in (3, 1):
        if slots[idx] is not None:
            last_out_idx = idx
            break
    stamps: list[datetime] = []
    for idx, parsed in enumerate(slots):
        if parsed is None:
            continue
        use_day = day
        if overnight and last_out_idx is not None and idx == last_out_idx:
            use_day = day + timedelta(days=1)
        stamps.append(datetime.combine(use_day, parsed))
    return stamps


def plus_days(stamp: Optional[datetime], logical_date: Optional[date]) -> int:
    if stamp is None or logical_date is None:
        return 0
    return max(0, (stamp.date() - logical_date).days)


def format_clock_with_offset(clock, plus: int = 0) -> Optional[str]:
    parsed = _clock(clock)
    if parsed is None:
        return None
    text = parsed.strftime("%H:%M")
    if plus and int(plus) > 0:
        return f"{text} (+{int(plus)})"
    return text


def overlaps_daytime_lunch(
    start: datetime,
    end: datetime,
    lunch_start: time = DAYTIME_LUNCH_START,
    lunch_end: time = DAYTIME_LUNCH_END,
) -> bool:
    """True if [start, end] overlaps 12:00–13:00 on any calendar day in the span."""
    if end <= start:
        return False
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        lunch_s = datetime.combine(day, lunch_start)
        lunch_e = datetime.combine(day, lunch_end)
        if lunch_e <= lunch_s:
            lunch_e += timedelta(days=1)
        if min(end, lunch_e) > max(start, lunch_s):
            return True
        day += timedelta(days=1)
    return False


def calculate_smart_work_hours(stamps: list[datetime], shift_lunch_hours: float, standard_shift_hours: float = 8.0) -> float:
    """Pair-wise In/Out with anomaly guards. Returns -1.0 when HR must intervene.

    Even count (2, 4, 6…): sum each pair. Pair > 16h → cross-day trap (-1).
    Pair under 15 minutes → junk punch, skipped (not an exception).
    
    Lunch deduction logic (NEW):
    - 8h shift (≤ 10h): If total hours ≥ standard_shift_hours → count as full day, no lunch deduction needed
      Example: 8h shift, worked 8.5h without lunch break → still counts as 1.0 công (full day)
    - 12h shift (> 10h): Must have lunch break hours, otherwise mark for review (-1.0)
      Example: 12h shift, worked 12h straight without clocking out for lunch → -1.0 (needs lunch 补充)
    
    Exactly 2 punches and total > 6h → deduct shift_lunch_hours (worked through break).
    Odd count or fewer than 2 punches → -1.0 (exception tab).
    """
    stamps = unique_sorted(stamps or [])
    num_punches = len(stamps)
    try:
        lunch = float(shift_lunch_hours or 0)
    except (TypeError, ValueError):
        lunch = 0.0
    if lunch < 0:
        lunch = 0.0
    
    try:
        standard_hours = float(standard_shift_hours or 8.0)
    except (TypeError, ValueError):
        standard_hours = 8.0

    if num_punches < 2:
        return ODD_PUNCH_HOURS

    if num_punches % 2 == 1:
        return ODD_PUNCH_HOURS

    total_hours = 0.0
    min_hours = MIN_WORK_MINUTES / 60.0
    for i in range(0, num_punches, 2):
        pair_hours = _pair_span_hours(stamps[i], stamps[i + 1])
        if pair_hours > MAX_SHIFT_HOURS:
            return ODD_PUNCH_HOURS
        if pair_hours < min_hours:
            continue
        total_hours += pair_hours

    # Smart lunch deduction based on shift duration
    if num_punches == 2 and total_hours > LUNCH_IF_SPAN_HOURS:
        # 8h shifts (≤ 10h): if worked enough hours, don't force lunch deduction
        if standard_hours <= 10.0:
            # If total hours already meet or exceed standard hours, count as full day
            if total_hours >= standard_hours:
                # Don't deduct lunch - enough hours worked
                pass
            else:
                # Not enough hours, deduct lunch
                total_hours -= lunch
        else:
            # 12h shifts (> 10h): MUST deduct lunch, otherwise return -1 for review
            if lunch > 0:
                total_hours -= lunch
            else:
                # 12h shift but no lunch configured → needs HR review
                return ODD_PUNCH_HOURS

    return max(0.0, round(total_hours, 2))


def hours_from_datetimes(
    stamps: list[datetime],
    lunch_duration_hours: Optional[float] = None,
    lunch_start: time = DAYTIME_LUNCH_START,
    lunch_end: time = DAYTIME_LUNCH_END,
    unset_lunch_hours: float = 0.0,
    standard_shift_hours: float = 8.0,
) -> tuple[float, float]:
    """Net hours from In/Out pairs. Odd punch count → 0 hours (exception tab).

    Lunch is deducted only when master provides ``lunch_duration_hours`` (or the
    caller passes unset_lunch_hours). Never invent 1h / 12:00–13:00.
    
    For 8h shifts: If enough hours worked, counts as full day even without lunch break.
    For 12h shifts: Must have lunch break, otherwise flagged for review.
    """
    del lunch_start, lunch_end
    stamps = merge_day_punches(stamps)
    try:
        amount = float(lunch_duration_hours or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        amount = float(unset_lunch_hours or 0)
    if amount < 0:
        amount = 0.0
    
    try:
        standard_hours = float(standard_shift_hours or 8.0)
    except (TypeError, ValueError):
        standard_hours = 8.0

    raw = calculate_smart_work_hours(stamps, amount, standard_hours)
    if raw < 0 or len(stamps) < 2:
        return 0.0, 0.0

    first, last = stamps[0], stamps[-1]
    span = (last - first).total_seconds() / 3600.0
    net = round(float(raw), 4)
    deducted = round(max(span - net, 0.0), 4)
    return net, deducted


def session_clock_fields(stamps: list[datetime], logical_date: date) -> dict:
    stamps = merge_day_punches(stamps)
    d, e, f, g = four_clocks_dt(stamps)
    return {
        "in1": d.time() if d else None,
        "out1": e.time() if e else None,
        "in2": f.time() if f else None,
        "out2": g.time() if g else None,
        "in1_plus": plus_days(d, logical_date),
        "out1_plus": plus_days(e, logical_date),
        "in2_plus": plus_days(f, logical_date),
        "out2_plus": plus_days(g, logical_date),
        "punch_datetimes": tuple(stamps),
        "overnight": any(plus_days(dt, logical_date) > 0 for dt in stamps),
    }
