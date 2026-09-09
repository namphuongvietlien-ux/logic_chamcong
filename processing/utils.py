"""Shared helpers: name matching, time parsing, logging-safe conversions."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any, Optional


VIETNAMESE_CHARMAP = str.maketrans(
    {
        "à": "a",
        "á": "a",
        "ạ": "a",
        "ả": "a",
        "ã": "a",
        "â": "a",
        "ầ": "a",
        "ấ": "a",
        "ậ": "a",
        "ẩ": "a",
        "ẫ": "a",
        "ă": "a",
        "ằ": "a",
        "ắ": "a",
        "ặ": "a",
        "ẳ": "a",
        "ẵ": "a",
        "è": "e",
        "é": "e",
        "ẹ": "e",
        "ẻ": "e",
        "ẽ": "e",
        "ê": "e",
        "ề": "e",
        "ế": "e",
        "ệ": "e",
        "ể": "e",
        "ễ": "e",
        "ì": "i",
        "í": "i",
        "ị": "i",
        "ỉ": "i",
        "ĩ": "i",
        "ò": "o",
        "ó": "o",
        "ọ": "o",
        "ỏ": "o",
        "õ": "o",
        "ô": "o",
        "ồ": "o",
        "ố": "o",
        "ộ": "o",
        "ổ": "o",
        "ỗ": "o",
        "ơ": "o",
        "ờ": "o",
        "ớ": "o",
        "ợ": "o",
        "ở": "o",
        "ỡ": "o",
        "ù": "u",
        "ú": "u",
        "ụ": "u",
        "ủ": "u",
        "ũ": "u",
        "ư": "u",
        "ừ": "u",
        "ứ": "u",
        "ự": "u",
        "ử": "u",
        "ữ": "u",
        "ỳ": "y",
        "ý": "y",
        "ỵ": "y",
        "ỷ": "y",
        "ỹ": "y",
        "đ": "d",
    }
)


def strip_diacritics(text: str) -> str:
    lowered = text.lower()
    mapped = lowered.translate(VIETNAMESE_CHARMAP)
    nfkd = unicodedata.normalize("NFKD", mapped)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    text = strip_diacritics(text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def name_match_key(value: Any) -> str:
    """Key used to join fingerprint names with image-folder names."""
    return normalize_name(value)


def clock_plus_days(value: Any) -> int:
    """(+1) on a clock string means the punch belongs to the next calendar day."""
    if value is None or isinstance(value, (time, datetime, timedelta, int, float)):
        return 0
    match = re.search(r"\(\s*\+\s*(\d+)\s*\)", str(value))
    if not match:
        return 0
    try:
        return max(0, int(match.group(1)))
    except (TypeError, ValueError):
        return 0


def shift_window_hours(start_value: Any, end_value: Any) -> Optional[float]:
    """Hours from start→end. Overnight (end ≤ start) wraps +24h."""
    start = parse_time_value(start_value)
    end = parse_time_value(end_value)
    if start is None or end is None:
        return None
    start_m = start.hour * 60 + start.minute
    end_m = end.hour * 60 + end.minute
    if end_m <= start_m:
        end_m += 24 * 60
    return (end_m - start_m) / 60.0


def net_shift_hours(start_value: Any, end_value: Any, lunch_hours: Any = 0) -> Optional[float]:
    """Paid shift length: window minus lunch. None if clocks cannot be parsed."""
    window = shift_window_hours(start_value, end_value)
    if window is None:
        return None
    try:
        lunch = float(lunch_hours or 0)
    except (TypeError, ValueError):
        lunch = 0.0
    return max(0.0, window - max(0.0, lunch))


_EXCEL_QUOTE_CHARS = "'\"`´‘’“”′″＇"
_CLOCK_TOKEN_RE = re.compile(
    r"(\d{1,2})\s*[:hH．.：﹕]\s*(\d{2})(?:\s*[:hH．.：﹕]\s*(\d{2}))?"
    r"(?:\s*([AaPp][Mm]))?"
)


def normalize_clock_text(value: Any) -> str:
    """Strip Excel text-prefix apostrophes so `'7:00` becomes `7:00`."""
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").replace("\u200b", "").strip()
    if text.startswith("="):
        text = text[1:].strip()
    text = text.strip(_EXCEL_QUOTE_CHARS + " \t")
    text = re.sub(r"\(\s*\+\s*\d+\s*\)\s*$", "", text).strip()
    text = text.replace("：", ":").replace("﹕", ":").replace("．", ".")
    text = re.sub(r"\s*giờ\s*", ":", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*phút\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clock_from_hms(hh: int, mm: int, ss: int = 0) -> Optional[time]:
    if 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60:
        return time(hh, mm, ss)
    return None


def parse_time_value(value: Any) -> Optional[time]:
    """Parse a real punch/clock cell. Never invent a time if the cell is empty or junk.

    Accepts Excel text clocks with a leading apostrophe (`'7:00`, `'07:00`) and
    unpadded hours (`7:00`). Rejects bare integers like 529 that are not clocks.
    """
    if value is None or value == "":
        return None
    try:
        import pandas as pd

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
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return time(hours, minutes, seconds)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and 0 < value < 1:
            seconds = int(round(value * 86400)) % 86400
            hours, rem = divmod(seconds, 3600)
            minutes, seconds = divmod(rem, 60)
            return time(hours, minutes, seconds)
        return None
    text = normalize_clock_text(value)
    if not text or text in {"-", "--", "—", "None", "NaT", "nan"}:
        return None
    dotted = re.fullmatch(r"(\d{1,2})\.(\d{2})(?:\.(\d{2}))?", text)
    if dotted:
        text = f"{dotted.group(1)}:{dotted.group(2)}" + (
            f":{dotted.group(3)}" if dotted.group(3) else ""
        )
    else:
        text = text.replace(".", ":")
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    match = _CLOCK_TOKEN_RE.fullmatch(text.replace(" ", ""))
    if not match:
        match = _CLOCK_TOKEN_RE.fullmatch(re.sub(r"\s+", "", text))
    if match:
        hh, mm, ss = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
        ampm = (match.group(4) or "").upper()
        if ampm == "PM" and hh < 12:
            hh += 12
        elif ampm == "AM" and hh == 12:
            hh = 0
        return _clock_from_hms(hh, mm, ss)
    return None


def parse_date_value(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def employee_code_text(employee_id: Any) -> str:
    """Normalize mã NV from Excel/SQLite (192, 192.0, '00192') for sorting and display."""
    if employee_id is None:
        return ""
    if isinstance(employee_id, bool):
        return ""
    if isinstance(employee_id, float):
        if employee_id != employee_id:
            return ""
        if employee_id.is_integer():
            return str(int(employee_id))
        return str(employee_id).strip()
    if isinstance(employee_id, int):
        return str(employee_id)
    text = str(employee_id).strip()
    if not text or text in {"-", "-----"} or text.casefold() in {"nan", "none", "nat"}:
        return ""
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def employee_id_sort_key(employee_id: Any = "", employee_name: Any = "") -> tuple[int, int, str, str]:
    """Sort by numeric mã NV (12 before 100), then text code, then name. Blank codes last."""
    code = employee_code_text(employee_id)
    name = str(employee_name or "").strip().casefold()
    if not code:
        return (2, 0, "", name)
    if code.isdigit():
        return (0, int(code), code.casefold(), name)
    return (1, 0, code.casefold(), name)


def sort_frame_by_employee_id(df: Any, extra_columns: tuple[str, ...] = ()):
    """Stable sort of an attendance/employee DataFrame by mã NV, then extra columns."""
    if df is None:
        return df
    try:
        import pandas as pd
    except ImportError:
        return df
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    work = df.copy()
    has_id = "employee_id" in work.columns
    has_name = "employee_name" in work.columns
    work["_eid_key"] = [
        employee_id_sort_key(
            work.at[idx, "employee_id"] if has_id else "",
            work.at[idx, "employee_name"] if has_name else "",
        )
        for idx in work.index
    ]
    cols = ["_eid_key"] + [c for c in extra_columns if c in work.columns]
    work = work.sort_values(cols, kind="mergesort")
    return work.drop(columns="_eid_key").reset_index(drop=True)


def combine_date_time(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second)


def format_time(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        if value.second:
            return value.strftime("%H:%M:%S")
        return value.strftime("%H:%M")
    parsed = parse_time_value(value)
    return format_time(parsed) if parsed else ""


def format_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _as_time(value: Any) -> Optional[time]:
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    return parse_time_value(value)


def min_time(*values: Any) -> Optional[time]:
    times = [t for t in (_as_time(v) for v in values) if t is not None]
    return min(times) if times else None


def max_time(*values: Any) -> Optional[time]:
    times = [t for t in (_as_time(v) for v in values) if t is not None]
    return max(times) if times else None
