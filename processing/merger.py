"""Module 3: cluster punches into work sessions, then apply hours / công rules."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Callable, Optional

import pandas as pd

from processing.cong_rules import (
    _as_time,
    early_minutes,
    hours_from_clocks,
    last_out_time,
    late_minutes,
    late_return_minutes,
    overtime_hours,
    safe_shift_hours,
    session_clocks,
    workday_count,
)
from processing.leave import apply_leave_to_day, fetch_approved_leaves, is_half_day, leave_covering
from processing.master_data import is_in_master, lookup_config
from processing.sessions import (
    STATUS_CROSS_DAY,
    STATUS_ODD_PUNCH,
    cluster_sessions,
    clocks_to_datetimes,
    combine_punch,
    hours_from_datetimes,
    merge_day_punches,
    missing_punch_status,
    session_clock_fields,
    unique_sorted,
)
from processing.utils import name_match_key, sort_frame_by_employee_id

LogFn = Optional[Callable[[str], None]]


def _as_bool(value) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "overnight"}
    return bool(value)


def _blank(value) -> bool:
    try:
        return value is None or pd.isna(value)
    except (TypeError, ValueError):
        return value is None


def _as_date(value) -> Optional[date]:
    if _blank(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _has_complete_pair(row) -> bool:
    in1, out1, in2, out2 = row.get("in1"), row.get("out1"), row.get("in2"), row.get("out2")
    return bool((in1 and (out1 or out2)) or (in2 and out2))


def _notes(row: pd.Series) -> str:
    authorized = bool(row.get("is_leave"))
    half = bool(row.get("is_leave")) and float(row.get("leave_duration") or 1) < 1
    skip_missing = authorized and (not half or _has_complete_pair(row) or not any(
        not _blank(row.get(key)) for key in ("in1", "out1", "in2", "out2")
    ))
    parts: list[str] = []
    if not authorized:
        if _blank(row.get("fingerprint_in")) and _blank(row.get("fingerprint_out")):
            parts.append("No Fingerprint")
        if _blank(row.get("photo_in")) and _blank(row.get("photo_out")):
            parts.append("No Photo")
    if not skip_missing and _blank(row.get("in1")) and not _blank(row.get("last_out")):
        parts.append("Missing In")
    if row.get("overnight"):
        parts.append("Tăng ca qua đêm")
    if not skip_missing:
        if not row.get("overnight") and not _blank(row.get("in2")) and _blank(row.get("out2")):
            parts.append("Thiếu giờ ra")
        elif _blank(row.get("last_out")) and not _blank(row.get("in1")):
            parts.append("Thiếu giờ ra")
    n_clocks = sum(1 for key in ("in1", "out1", "in2", "out2") if not _blank(row.get(key)))
    if not skip_missing and n_clocks == 3 and _blank(row.get("in2")):
        parts.append("Missing lunch punch")
    late = row.get("late_minutes") or 0
    try:
        if int(late) > 0:
            parts.append(f"Late {int(late)}m")
    except (TypeError, ValueError):
        pass
    early = row.get("early_minutes") or 0
    try:
        if int(early) > 0:
            parts.append(f"Early {int(early)}m")
    except (TypeError, ValueError):
        pass
    if row.get("master_matched") is False:
        parts.append("Default shift (not in master)")
    return "; ".join(parts)


def _expand_fingerprint_punches(row: dict) -> list[datetime]:
    day = _as_date(row.get("date"))
    stamps: list[datetime] = []
    raw = row.get("punches") or ()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            dt = combine_punch(day, item) if day else None
            if dt:
                stamps.append(dt)
    if not stamps and day:
        for key in ("fingerprint_in", "fingerprint_out"):
            dt = combine_punch(day, row.get(key))
            if dt:
                stamps.append(dt)
    return stamps


def _expand_photo_punches(row: dict) -> list[datetime]:
    stamps: list[datetime] = []
    for key in ("photo_datetime", "photo_datetimes", "punch_datetimes"):
        raw = row.get(key)
        if isinstance(raw, datetime):
            stamps.append(raw)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, datetime):
                    stamps.append(item)
    day = _as_date(row.get("date"))
    if not stamps and day:
        for key in ("photo_in", "photo_out", "photo_time"):
            dt = combine_punch(day, row.get(key))
            if dt:
                stamps.append(dt)
    return stamps


def recompute_record(
    data: dict,
    master: pd.DataFrame,
    trust_clocks: bool = False,
    approved_leaves: list | None = None,
) -> dict:
    """Fill shift, hours, công, notes from a clustered session or edited clocks."""
    name = str(data.get("employee_name") or "")
    cfg = lookup_config(master, name)
    for key, value in cfg.items():
        if key == "employee_id" and str(data.get("employee_id") or "").strip():
            continue
        if key == "department" and str(data.get("department") or "").strip().strip("- "):
            continue
        data[key] = value
    logical = _as_date(data.get("date"))
    overnight_flag = _as_bool(data.get("overnight"))
    stamps: list[datetime] = []
    if trust_clocks or overnight_flag:
        in1 = _as_time(data.get("in1"))
        out1 = _as_time(data.get("out1"))
        in2 = _as_time(data.get("in2"))
        out2 = _as_time(data.get("out2"))
        stamps = clocks_to_datetimes(logical, in1, out1, in2, out2, overnight=overnight_flag)
    else:
        raw = data.get("punch_datetimes") or ()
        if isinstance(raw, (list, tuple)) and raw:
            stamps = unique_sorted(raw)
        if not stamps:
            in1, out1, in2, out2 = session_clocks(data)
            stamps = clocks_to_datetimes(logical, in1, out1, in2, out2, overnight=False)
    if stamps:
        logical = logical or stamps[0].date()
        data["date"] = logical
        data.update(session_clock_fields(stamps, logical))
        data["overnight"] = bool(overnight_flag)
        if overnight_flag:
            if data.get("out2") is not None:
                data["out2_plus"] = 1
            elif data.get("out1") is not None:
                data["out1_plus"] = 1
    else:
        in1 = _as_time(data.get("in1"))
        out1 = _as_time(data.get("out1"))
        in2 = _as_time(data.get("in2"))
        out2 = _as_time(data.get("out2"))
        data["in1"], data["out1"], data["in2"], data["out2"] = in1, out1, in2, out2
        for key in ("in1_plus", "out1_plus", "in2_plus", "out2_plus"):
            data[key] = 0
        if overnight_flag and data.get("out2") is not None:
            data["out2_plus"] = 1
        elif overnight_flag and data.get("out1") is not None:
            data["out1_plus"] = 1
        data["overnight"] = bool(overnight_flag)
        data["punch_datetimes"] = ()
    in1 = data.get("in1")
    out1 = data.get("out1")
    in2 = data.get("in2")
    out2 = data.get("out2")
    last_out = last_out_time(in1, out1, in2, out2)
    data["final_check_in"] = in1
    data["final_check_out"] = last_out
    data["last_out"] = last_out
    data["punches"] = tuple(t for t in (in1, out1, in2, out2) if t is not None)
    std = safe_shift_hours(data.get("standard_shift_hours"))
    data["standard_shift_hours"] = std
    matched = is_in_master(master, name)
    data["master_matched"] = matched
    lunch_h = float(data.get("lunch_duration_hours") or 0)
    if not matched:
        lunch_h = 0.0
        data["lunch_duration_hours"] = 0.0
        data["standard_shift_hours"] = std
    # Chỉ trừ nghỉ khi master có số giờ. Không tự thêm 1h.
    hour_stamps = [s for s in (data.get("punch_datetimes") or ()) if s]
    if hour_stamps:
        actual, deducted = hours_from_datetimes(
            hour_stamps,
            0.0 if overnight_flag else lunch_h,
            unset_lunch_hours=0.0,
            standard_shift_hours=std,
        )
    else:
        actual, deducted = hours_from_clocks(
            in1,
            out1,
            in2,
            out2,
            lunch_h,
            logical_date=logical,
            unset_lunch_hours=0.0,
            overnight=bool(overnight_flag),
            standard_shift_hours=std,
        )
    data["deducted_lunch_hours"] = round(deducted, 2)
    data["actual_work_hours"] = round(actual, 2)
    data["standardized_workday"] = workday_count(actual, std)
    data["overtime_hours"] = overtime_hours(actual, std)
    overnight = bool(data.get("overnight"))
    if overnight:
        data["late_minutes"] = 0
        data["early_minutes"] = 0
        data["late_return_minutes"] = 0
    else:
        data["late_minutes"] = late_minutes(in1, data.get("shift_start"))
        data["early_minutes"] = early_minutes(last_out, data.get("shift_end"))
        data["late_return_minutes"] = late_return_minutes(last_out, data.get("shift_end"))
    data["name_key"] = name_match_key(name)
    holiday_name = str(data.get("holiday_name") or "")
    if not holiday_name and logical is not None:
        try:
            from processing.database import holiday_name_on

            holiday_name = holiday_name_on(logical)
        except Exception:
            holiday_name = ""
    data["holiday_name"] = holiday_name
    data["is_holiday"] = bool(holiday_name)
    if data["is_holiday"] and not any((in1, out1, in2, out2)):
        data["actual_work_hours"] = 0.0
        data["deducted_lunch_hours"] = 0.0
        data["standardized_workday"] = 1.0
        data["overtime_hours"] = 0.0
        data["late_minutes"] = 0
        data["early_minutes"] = 0
        data["late_return_minutes"] = 0
    leave = leave_covering(name, logical, approved_leaves)
    if leave:
        data["is_leave"] = True
        data["leave_type"] = leave.get("leave_type") or ""
        data["leave_duration"] = 0.5 if is_half_day(leave) else 1.0
        data["leave_session"] = leave.get("session") or ""
    else:
        data["is_leave"] = False
        data["leave_type"] = ""
        data["leave_duration"] = 0
        data["leave_session"] = ""
    data["notes"] = _notes(pd.Series(data))
    if data["is_holiday"]:
        tag = f"Ngày lễ: {holiday_name}" if holiday_name else "Ngày lễ"
        if any((in1, out1, in2, out2)):
            tag = f"Làm ngày lễ: {holiday_name}" if holiday_name else "Làm ngày lễ"
        notes = str(data.get("notes") or "")
        if tag not in notes:
            data["notes"] = f"{notes}; {tag}" if notes else tag
    apply_leave_to_day(data, leave)
    raw_stamps = data.get("punch_datetimes") or ()
    if isinstance(raw_stamps, (list, tuple)) and raw_stamps:
        merged_stamps = merge_day_punches(raw_stamps)
    else:
        merged_stamps = merge_day_punches(
            clocks_to_datetimes(logical, in1, out1, in2, out2, overnight=bool(overnight_flag))
        )
    authorized_full = bool(data.get("is_leave")) and float(data.get("leave_duration") or 0) >= 1
    holiday_rest = bool(data.get("is_holiday")) and not any((in1, out1, in2, out2))
    status = "" if authorized_full or holiday_rest else missing_punch_status(merged_stamps)
    data["missing_punch"] = status
    if status:
        notes = str(data.get("notes") or "")
        for old in (
            "Thiếu giờ ra",
            "Thiếu giờ vào",
            "Thiếu Giờ Ra",
            "Thiếu Giờ Vào",
            STATUS_ODD_PUNCH,
            STATUS_CROSS_DAY,
        ):
            notes = "; ".join(part.strip() for part in notes.split(";") if part.strip() and part.strip() != old)
        data["notes"] = f"{notes}; {status}" if notes else status
    if data.get("manual_edit"):
        extra = "Manual edit"
        if extra not in str(data.get("notes") or ""):
            data["notes"] = f"{data['notes']}; {extra}" if data.get("notes") else extra
    return data


def _collect_events(fp: pd.DataFrame, ph: pd.DataFrame) -> list[dict]:
    events: list[dict] = []
    meta: dict[str, dict] = {}

    def _remember(key: str, name: str, emp_id: str, dept: str, source: str) -> None:
        slot = meta.setdefault(key, {"employee_name": name, "employee_id": "", "department": "", "sources": set()})
        if name:
            slot["employee_name"] = name
        if emp_id and not slot["employee_id"]:
            slot["employee_id"] = emp_id
        if dept and not slot["department"]:
            slot["department"] = dept
        slot["sources"].add(source)

    if not fp.empty:
        for _, row in fp.iterrows():
            data = row.to_dict()
            name = str(data.get("employee_name") or data.get("fp_employee_name") or "").strip()
            key = name_match_key(name)
            if not key:
                continue
            emp_id = str(data.get("employee_id") or data.get("fp_employee_id") or "")
            dept = str(data.get("department") or data.get("fp_department") or "")
            _remember(key, name, emp_id, dept, "fp")
            for dt in _expand_fingerprint_punches(data):
                events.append({"name_key": key, "punch_at": dt, "source": "fp"})
    if not ph.empty:
        fp_keys = {e["name_key"] for e in events} | set(meta)
        remap: dict[str, str] = {}
        for raw in ph.get("employee_name", pd.Series(dtype=object)).dropna().unique():
            photo_key = name_match_key(raw)
            if photo_key in fp_keys:
                remap[photo_key] = photo_key
                continue
            hits = [fk for fk in fp_keys if fk and (fk in photo_key or photo_key in fk)]
            remap[photo_key] = hits[0] if len(hits) == 1 else photo_key
        for _, row in ph.iterrows():
            data = row.to_dict()
            name = str(data.get("employee_name") or data.get("photo_employee_name") or "").strip()
            photo_key = name_match_key(name)
            key = remap.get(photo_key, photo_key)
            if not key:
                continue
            _remember(key, name, "", "", "photo")
            for dt in _expand_photo_punches(data):
                events.append({"name_key": key, "punch_at": dt, "source": "photo"})
    for event in events:
        info = meta.get(event["name_key"], {})
        event["employee_name"] = info.get("employee_name") or event["name_key"]
        event["employee_id"] = info.get("employee_id") or ""
        event["department"] = info.get("department") or ""
        event["has_fp"] = "fp" in (info.get("sources") or set())
        event["has_photo"] = "photo" in (info.get("sources") or set())
    return events


def _sessions_to_rows(events: list[dict], master: pd.DataFrame, log: LogFn) -> pd.DataFrame:
    if not events:
        return pd.DataFrame()
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(event["name_key"], []).append(event)
    records = []
    for _key, items in grouped.items():
        stamps = unique_sorted(item["punch_at"] for item in items)
        proto = items[0]
        for session in cluster_sessions(stamps):
            # set-dedupe + 5-minute debounce already applied per calendar day
            session = merge_day_punches(session)
            if not session:
                continue
            logical = session[0].date()
            check_in, check_out = session[0], session[-1]
            data = {
                "employee_name": proto["employee_name"],
                "employee_id": proto.get("employee_id") or "",
                "department": proto.get("department") or "",
                "date": logical,
                "punch_datetimes": tuple(session),
                "fingerprint_in": check_in.time() if proto.get("has_fp") else None,
                "fingerprint_out": check_out.time() if proto.get("has_fp") and len(session) > 1 else None,
                "photo_in": None,
                "photo_out": None,
            }
            if not proto.get("has_fp"):
                data["fingerprint_in"] = None
                data["fingerprint_out"] = None
            row = recompute_record(data, master, trust_clocks=False)
            records.append(row)
    out = pd.DataFrame(records)
    if log:
        n_emp = out["employee_name"].nunique() if not out.empty else 0
        log(f"Gom theo ngày lịch (không kéo punch ngày sau): {len(out)} dòng / {n_emp} NV.")
    return out


def merge_attendance(
    fingerprint_df: pd.DataFrame,
    photo_df: pd.DataFrame,
    work_start: time = time(8, 0),
    master: Optional[pd.DataFrame] = None,
    log: LogFn = None,
) -> pd.DataFrame:
    del work_start
    fp = fingerprint_df.copy() if fingerprint_df is not None and not fingerprint_df.empty else pd.DataFrame()
    ph = photo_df.copy() if photo_df is not None and not photo_df.empty else pd.DataFrame()
    if fp.empty and ph.empty:
        raise ValueError("Không có dữ liệu vân tay lẫn ảnh để gộp.")
    if not fp.empty:
        fp["date"] = pd.to_datetime(fp["date"]).dt.date
    if not ph.empty and "date" in ph.columns:
        ph["date"] = pd.to_datetime(ph["date"]).dt.date
    fetch_approved_leaves(refresh=True)
    events = _collect_events(fp, ph)
    merged = _sessions_to_rows(events, master if master is not None else pd.DataFrame(), log)
    if merged.empty:
        raise ValueError("Không gom được phiên chấm công nào từ dữ liệu vân tay/ảnh.")
    roster = master if master is not None else pd.DataFrame()
    merged = _ensure_holiday_rows(merged, roster, log)
    merged = _ensure_leave_rows(merged, roster, log)
    merged = sort_frame_by_employee_id(merged, extra_columns=("date",))
    if log:
        log(f"Đã gộp {len(merged)} dòng theo nhân viên + ngày lịch.")
    return merged


def _as_work_date(value) -> Optional[date]:
    return _as_date(value)


def _ensure_holiday_rows(merged: pd.DataFrame, master: pd.DataFrame, log: LogFn) -> pd.DataFrame:
    """Add paid-holiday rows (1 công, no punches) for every active employee in the period."""
    if merged is None or merged.empty:
        return merged
    try:
        from processing.database import paid_holiday_map
        from processing.holidays import ensure_holiday_years
    except Exception:
        return merged
    days = [_as_work_date(v) for v in merged["date"].tolist()]
    days = [d for d in days if d]
    if not days:
        return merged
    start, end = min(days), max(days)
    ensure_holiday_years(list(range(start.year, end.year + 1)))
    holidays = paid_holiday_map(start, end)
    if not holidays:
        return merged
    if "name_key" not in merged.columns:
        merged = merged.copy()
        merged["name_key"] = merged["employee_name"].map(lambda n: name_match_key(str(n or "")))
    existing = {
        (str(row.get("name_key") or ""), _as_work_date(row.get("date")))
        for _, row in merged.iterrows()
    }
    roster = master if master is not None and not master.empty else pd.DataFrame()
    extra = []
    for _, emp in roster.iterrows():
        key = str(emp.get("name_key") or name_match_key(str(emp.get("employee_name") or "")))
        if not key:
            continue
        for day, title in holidays.items():
            if (key, day) in existing:
                continue
            extra.append(
                recompute_record(
                    {
                        "employee_name": emp.get("employee_name"),
                        "employee_id": emp.get("employee_id") or "",
                        "department": emp.get("department") or "",
                        "date": day,
                        "holiday_name": title,
                        "is_holiday": True,
                    },
                    roster,
                )
            )
            existing.add((key, day))
    if extra:
        merged = pd.concat([merged, pd.DataFrame(extra)], ignore_index=True)
        if log:
            log(f"Ngày lễ: thêm {len(extra)} dòng nghỉ lễ có lương (1 công).")
    return merged


def _ensure_leave_rows(merged: pd.DataFrame, master: pd.DataFrame, log: LogFn) -> pd.DataFrame:
    """Insert approved-leave days that have no punches so Ghi chú / công still appear."""
    if merged is None or merged.empty:
        return merged
    try:
        from processing.leave import fetch_approved_leaves, iter_leave_dates
    except Exception:
        return merged
    days = [_as_work_date(v) for v in merged["date"].tolist()]
    days = [d for d in days if d]
    if not days:
        return merged
    start, end = min(days), max(days)
    approved = fetch_approved_leaves()
    if not approved:
        return merged
    if "name_key" not in merged.columns:
        merged = merged.copy()
        merged["name_key"] = merged["employee_name"].map(lambda n: name_match_key(str(n or "")))
    existing = {
        (str(row.get("name_key") or ""), _as_work_date(row.get("date")))
        for _, row in merged.iterrows()
    }
    roster = master if master is not None and not master.empty else pd.DataFrame()
    roster_by_key: dict[str, dict] = {}
    if not roster.empty:
        for _, emp in roster.iterrows():
            key = str(emp.get("name_key") or name_match_key(str(emp.get("employee_name") or "")))
            if key:
                roster_by_key[key] = emp.to_dict()
    extra = []
    for leave in approved:
        key = str(leave.get("name_key") or "")
        leave_start = leave.get("start_date")
        leave_end = leave.get("end_date")
        if not key or leave_start is None or leave_end is None:
            continue
        emp = roster_by_key.get(key, {})
        for day in iter_leave_dates(leave_start, leave_end):
            if day < start or day > end or (key, day) in existing:
                continue
            extra.append(
                recompute_record(
                    {
                        "employee_name": emp.get("employee_name") or leave.get("employee_name") or "",
                        "employee_id": emp.get("employee_id") or "",
                        "department": emp.get("department") or "",
                        "date": day,
                    },
                    roster,
                    approved_leaves=approved,
                )
            )
            existing.add((key, day))
    if extra:
        merged = pd.concat([merged, pd.DataFrame(extra)], ignore_index=True)
        if log:
            log(f"Nghỉ phép: thêm {len(extra)} dòng không có chấm công.")
    return merged
