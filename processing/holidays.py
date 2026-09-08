"""Vietnamese public holidays (Labor Code) including lunar Tết and Giỗ Tổ."""

from __future__ import annotations

import math
from datetime import date, timedelta

# Ho Ngoc Duc lunisolar calendar, timezone Vietnam (UTC+7).

_TZ = 7.0


def _jd_from_ymd(day: int, month: int, year: int) -> int:
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    jd = day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    if jd < 2299161:
        jd = day + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083
    return jd


def _ymd_from_jd(jd: int) -> date:
    if jd > 2299160:
        a = jd + 32044
        b = (4 * a + 3) // 146097
        c = a - (b * 146097) // 4
    else:
        b = 0
        c = jd + 32082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = b * 100 + d - 4800 + m // 10
    return date(year, month, day)


def _new_moon_day(k: int) -> int:
    t = k / 1236.85
    t2 = t * t
    t3 = t2 * t
    dr = math.pi / 180.0
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * t2 - 0.000000155 * t3
    jd1 += 0.00033 * math.sin((166.56 + 132.87 * t - 0.009173 * t2) * dr)
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    mpr = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    c1 = (0.1734 - 0.000393 * t) * math.sin(m * dr) + 0.0021 * math.sin(2 * dr * m)
    c1 -= 0.4068 * math.sin(mpr * dr) + 0.0161 * math.sin(dr * 2 * mpr)
    c1 -= 0.0004 * math.sin(dr * 3 * mpr) + 0.0104 * math.sin(dr * 2 * f)
    c1 -= 0.0051 * math.sin(dr * (m + mpr)) - 0.0074 * math.sin(dr * (m - mpr))
    c1 += 0.0004 * math.sin(dr * (2 * f + m)) - 0.0004 * math.sin(dr * (2 * f - m))
    c1 -= 0.0006 * math.sin(dr * (2 * f + mpr)) + 0.0010 * math.sin(dr * (2 * f - mpr))
    c1 += 0.0005 * math.sin(dr * (2 * mpr + m))
    if t < -11:
        delta_t = 0.001 + 0.000839 * t + 0.0002261 * t2 - 0.00000845 * t3 - 0.000000081 * t * t3
    else:
        delta_t = -0.000278 + 0.000265 * t + 0.000262 * t2
    return int(jd1 + c1 - delta_t + 0.5 + _TZ / 24.0)


def _sun_longitude(jdn: float) -> float:
    t = (jdn - 2451545.0) / 36525.0
    t2 = t * t
    dr = math.pi / 180.0
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    dl = (1.914600 - 0.004817 * t - 0.000014 * t2) * math.sin(dr * m)
    dl += (0.019993 - 0.000101 * t) * math.sin(dr * 2 * m) + 0.000290 * math.sin(dr * 3 * m)
    return (l0 + dl) * dr


def _month_11(year: int) -> int:
    off = _jd_from_ymd(31, 12, year) - 2415021
    k = int(off / 29.530588853)
    nm = _new_moon_day(k)
    sun = _sun_longitude(nm)
    if sun >= 9 * math.pi / 6:
        nm = _new_moon_day(k - 1)
    return nm


def _leap_month_offset(a11: int, b11: int) -> int:
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    last = 0.0
    i = 1
    arc = _sun_longitude(_new_moon_day(k + i))
    while True:
        last = arc
        i += 1
        arc = _sun_longitude(_new_moon_day(k + i))
        if arc == last or i >= 14:
            break
    return i - 1


def solar_from_lunar(lunar_day: int, lunar_month: int, lunar_year: int, leap: bool = False) -> date:
    """Convert Vietnamese lunar Y/M/D to a solar date."""
    if lunar_month < 11:
        a11 = _month_11(lunar_year - 1)
        b11 = _month_11(lunar_year)
    else:
        a11 = _month_11(lunar_year)
        b11 = _month_11(lunar_year + 1)
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    off = lunar_month - 11
    if off < 0:
        off += 12
    if b11 - a11 > 365:
        leap_off = _leap_month_offset(a11, b11)
        if leap and off >= leap_off:
            off += 1
        elif not leap and off >= leap_off:
            off += 1
    month_start = _new_moon_day(k + off)
    return _ymd_from_jd(month_start + lunar_day - 1)


# Official 1/1 âm lịch (Tết) — used first so HR dates stay correct if the
# astronomical fallback drifts for a given year.
_TET_FIRST = {
    2020: date(2020, 1, 25),
    2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),
    2023: date(2023, 1, 22),
    2024: date(2024, 2, 10),
    2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
    2031: date(2031, 1, 23),
    2032: date(2032, 2, 11),
    2033: date(2033, 1, 31),
    2034: date(2034, 2, 19),
    2035: date(2035, 2, 8),
}

_HUNG_KINGS = {
    2020: date(2020, 4, 2),
    2021: date(2021, 4, 21),
    2022: date(2022, 4, 10),
    2023: date(2023, 3, 31),
    2024: date(2024, 4, 18),
    2025: date(2025, 4, 7),
    2026: date(2026, 3, 28),
    2027: date(2027, 4, 16),
    2028: date(2028, 4, 4),
    2029: date(2029, 3, 24),
    2030: date(2030, 4, 12),
    2031: date(2031, 4, 1),
    2032: date(2032, 4, 19),
    2033: date(2033, 4, 8),
    2034: date(2034, 3, 29),
    2035: date(2035, 4, 16),
}


def tet_range(solar_year: int) -> list[date]:
    """Five Tết days: last lunar day of previous year + first four of the new year."""
    first = _TET_FIRST.get(solar_year) or solar_from_lunar(1, 1, solar_year)
    days = [first + timedelta(days=offset) for offset in range(0, 4)]
    eve = first - timedelta(days=1)
    return [eve, *days]


def hung_kings_day(solar_year: int) -> date:
    return _HUNG_KINGS.get(solar_year) or solar_from_lunar(10, 3, solar_year)


def national_day_pair(solar_year: int) -> list[date]:
    """2/9 plus the adjacent day (default 3/9)."""
    main = date(solar_year, 9, 2)
    extra = date(solar_year, 9, 3)
    return [main, extra]


def vietnam_public_holidays(year: int) -> list[dict]:
    """Official paid public holidays for a solar year (BLLĐ 2019 Điều 112)."""
    rows: list[dict] = [
        {"holiday_date": date(year, 1, 1), "name": "Tết Dương lịch", "kind": "public", "paid": True},
        {"holiday_date": date(year, 4, 30), "name": "Ngày Giải phóng miền Nam", "kind": "public", "paid": True},
        {"holiday_date": date(year, 5, 1), "name": "Ngày Quốc tế Lao động", "kind": "public", "paid": True},
    ]
    this_tet = tet_range(year)
    next_tet = tet_range(year + 1)
    for day in (*this_tet, *next_tet):
        if day.year != year:
            continue
        label = "Tết Nguyên Đán (giao thừa)" if day in {this_tet[0], next_tet[0]} else "Tết Nguyên Đán"
        rows.append({"holiday_date": day, "name": label, "kind": "public", "paid": True})
    rows.append(
        {"holiday_date": hung_kings_day(year), "name": "Giỗ Tổ Hùng Vương", "kind": "public", "paid": True}
    )
    rows.append({"holiday_date": date(year, 9, 2), "name": "Quốc khánh", "kind": "public", "paid": True})
    rows.append({"holiday_date": date(year, 9, 3), "name": "Quốc khánh (ngày liền kề)", "kind": "public", "paid": True})

    # Tết eve can fall in December of the previous solar year — skip those here.
    # Sunday falling on a holiday → nghỉ bù Monday (if not already a holiday).
    occupied = {item["holiday_date"] for item in rows}
    extra: list[dict] = []
    for item in list(rows):
        day = item["holiday_date"]
        if day.weekday() == 6:
            monday = day + timedelta(days=1)
            if monday not in occupied:
                extra.append(
                    {
                        "holiday_date": monday,
                        "name": f"Nghỉ bù {item['name']}",
                        "kind": "compensatory",
                        "paid": True,
                    }
                )
                occupied.add(monday)
    rows.extend(extra)
    rows.sort(key=lambda item: item["holiday_date"])
    return rows


def seed_year(year: int) -> int:
    from processing.database import replace_auto_holidays

    rows = vietnam_public_holidays(year)
    return replace_auto_holidays(year, rows)


def ensure_holiday_years(years: list[int] | None = None) -> None:
    """Generate auto holidays when a year has none yet (keeps manual rows)."""
    from processing.database import list_holidays

    if years is None:
        current = date.today().year
        years = [current - 1, current, current + 1]
    for year in years:
        existing = [row for row in list_holidays(year) if row.get("source") == "auto"]
        if not existing:
            seed_year(year)
