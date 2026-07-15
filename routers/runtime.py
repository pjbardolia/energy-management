"""
Runtime analytics router.

GET /runtime/fleet/current-shift         — all machines, current shift
GET /runtime/fleet/range                 — all machines, date range, daily buckets
GET /runtime/machines/{machine_id}/range — one machine, date range

Operational day: 09:00 IST → 09:00 IST next day = 03:30 UTC → 03:30 UTC next day
Shift:  Day   09:00–21:00 IST (03:30–15:30 UTC)
        Night 21:00–09:00 IST (15:30–03:30 UTC)
Runtime = readings where frequency (tag_id=6) > 0, converted to minutes.
"""

import logging
from datetime import datetime, timezone, timedelta, date

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from auth import get_current_user          # auth.py, not security.py
from schemas.runtime import (
    ShiftRuntimeResponse,
    FleetRangeResponse,
    MachineRangeResponse,
    DailyRuntimeRow,
    MachineRangeSummary,
)

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/runtime", tags=["runtime"])

# Hard contracts — do not change
FREQUENCY_TAG_ID  = 6      # tag_definition_id for 'frequency' (Hz)
IST_OFFSET        = timedelta(hours=5, minutes=30)
DAY_SHIFT_START_H = 9      # 09:00 IST
DAY_SHIFT_END_H   = 21     # 21:00 IST
APPROX_POLL_SEC   = 23     # approximate seconds between readings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _current_shift_bounds_utc() -> tuple[datetime, datetime, str]:
    """
    Return (shift_start_utc, shift_end_utc, shift_name) for the current shift.
    Day shift:   09:00–21:00 IST = 03:30–15:30 UTC
    Night shift: 21:00–09:00 IST = 15:30–03:30 UTC next day
    """
    now_utc  = datetime.now(timezone.utc)
    now_ist  = now_utc + IST_OFFSET
    hour_ist = now_ist.hour
    ist_date = now_ist.date()

    if DAY_SHIFT_START_H <= hour_ist < DAY_SHIFT_END_H:
        # Day shift — started at 09:00 IST today
        shift_start_ist = datetime(ist_date.year, ist_date.month, ist_date.day,
                                   DAY_SHIFT_START_H, 0, 0)
        shift_end_ist   = datetime(ist_date.year, ist_date.month, ist_date.day,
                                   DAY_SHIFT_END_H, 0, 0)
        shift_name = "day"
    else:
        # Night shift — started at 21:00 IST today or yesterday
        if hour_ist >= DAY_SHIFT_END_H:
            # After 21:00 IST — night shift started today at 21:00
            shift_start_ist = datetime(ist_date.year, ist_date.month, ist_date.day,
                                       DAY_SHIFT_END_H, 0, 0)
        else:
            # Before 09:00 IST — night shift started yesterday at 21:00
            yesterday = ist_date - timedelta(days=1)
            shift_start_ist = datetime(yesterday.year, yesterday.month, yesterday.day,
                                       DAY_SHIFT_END_H, 0, 0)
        shift_end_ist = shift_start_ist + timedelta(hours=12)
        shift_name = "night"

    # IST naive → UTC (replace tzinfo to mark as UTC after subtracting IST offset)
    shift_start_utc = shift_start_ist.replace(tzinfo=timezone.utc) - IST_OFFSET
    shift_end_utc   = shift_end_ist.replace(tzinfo=timezone.utc)   - IST_OFFSET

    return shift_start_utc, shift_end_utc, shift_name


def _op_day_bounds_utc(op_date: date) -> tuple[datetime, datetime]:
    """
    Return (start_utc, end_utc) for one operational day.
    Operational day starts 09:00 IST = 03:30 UTC; runs 24 hours.
    """
    start_ist = datetime(op_date.year, op_date.month, op_date.day,
                         DAY_SHIFT_START_H, 0, 0)
    start_utc = start_ist.replace(tzinfo=timezone.utc) - IST_OFFSET
    end_utc   = start_utc + timedelta(hours=24)
    return start_utc, end_utc


def _build_summary(machine_id: int, machine_name: str,
                   daily_rows: list[DailyRuntimeRow]) -> MachineRangeSummary:
    """Compute summary stats from daily rows for one machine."""
    rows = [r for r in daily_rows if r.machine_id == machine_id]
    if not rows:
        return MachineRangeSummary(
            machine_id=machine_id, machine_name=machine_name,
            total_runtime_minutes=0, total_sampled_minutes=0,
            utilisation_pct=0, avg_runtime_per_day_minutes=0,
            best_day_runtime_minutes=0, worst_day_runtime_minutes=0,
            days_with_data=0,
        )

    total_runtime  = sum(r.runtime_minutes for r in rows)
    total_sampled  = sum(r.sampled_minutes for r in rows)
    days_with_data = len([r for r in rows if r.sampled_minutes > 0])
    utilisation    = (total_runtime / total_sampled * 100) if total_sampled > 0 else 0
    avg_per_day    = total_runtime / days_with_data if days_with_data > 0 else 0
    best_day       = max((r.runtime_minutes for r in rows), default=0)
    # worst_day excludes days with no data (sampled_minutes == 0)
    worst_day      = min((r.runtime_minutes for r in rows if r.sampled_minutes > 0),
                         default=0)

    return MachineRangeSummary(
        machine_id=machine_id,
        machine_name=machine_name,
        total_runtime_minutes=round(total_runtime, 1),
        total_sampled_minutes=round(total_sampled, 1),
        utilisation_pct=round(utilisation, 1),
        avg_runtime_per_day_minutes=round(avg_per_day, 1),
        best_day_runtime_minutes=round(best_day, 1),
        worst_day_runtime_minutes=round(worst_day, 1),
        days_with_data=days_with_data,
    )


# ---------------------------------------------------------------------------
# GET /runtime/fleet/current-shift
# ---------------------------------------------------------------------------

@router.get("/fleet/current-shift", response_model=list[ShiftRuntimeResponse])
def get_fleet_current_shift_runtime(
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    Runtime for all machines in the current shift (day or night).
    Left-joins machine → telemetry so every machine appears even with no data.
    """
    company_id = current_user["company_id"]
    shift_start, shift_end, shift_name = _current_shift_bounds_utc()
    shift_duration_min = 720  # 12 hours, always

    shift_start_ist_label = "09:00" if shift_name == "day" else "21:00"
    shift_end_ist_label   = "21:00" if shift_name == "day" else "09:00"

    sql = text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            COALESCE(
                COUNT(td.id) FILTER (WHERE td.value_num > 0) * :poll_sec / 60.0,
                0
            )      AS runtime_minutes,
            COALESCE(
                COUNT(td.id) * :poll_sec / 60.0,
                0
            )      AS sampled_minutes
        FROM machine m
        JOIN machine_component_instance ci ON ci.machine_id = m.id
        LEFT JOIN telemetry_data td
               ON td.component_instance_id = ci.id
              AND td.tag_definition_id     = :freq_tag
              AND td.timestamp            >= :shift_start
              AND td.timestamp             < :shift_end
              AND td.company_id            = :company_id
        WHERE m.company_id = :company_id
        GROUP BY m.id, m.name
        ORDER BY m.name
    """)

    rows = db.execute(sql, {
        "company_id":  company_id,
        "freq_tag":    FREQUENCY_TAG_ID,
        "shift_start": shift_start,
        "shift_end":   shift_end,
        "poll_sec":    APPROX_POLL_SEC,
    }).mappings().fetchall()

    result = []
    for row in rows:
        runtime_min = float(row["runtime_minutes"])
        sampled_min = float(row["sampled_minutes"])
        runtime_pct = (runtime_min / shift_duration_min * 100) if shift_duration_min > 0 else 0

        result.append(ShiftRuntimeResponse(
            machine_id             = row["machine_id"],
            machine_name           = row["machine_name"],
            shift                  = shift_name,
            shift_start_ist        = shift_start_ist_label,
            shift_end_ist          = shift_end_ist_label,
            shift_duration_minutes = shift_duration_min,
            runtime_minutes        = round(runtime_min, 1),
            runtime_pct            = round(runtime_pct, 1),
            sampled_minutes        = round(sampled_min, 1),
        ))

    return result


# ---------------------------------------------------------------------------
# GET /runtime/fleet/range
# ---------------------------------------------------------------------------

@router.get("/fleet/range", response_model=FleetRangeResponse)
def get_fleet_runtime_range(
    from_date:    str     = Query(..., description="Start date YYYY-MM-DD (op day starts 09:00 IST)"),
    to_date:      str     = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    Runtime per machine per operational day over a date range.
    Returns daily_rows (one row per machine per day) and summaries (one per machine).
    """
    company_id = current_user["company_id"]

    try:
        from_d = date.fromisoformat(from_date)
        to_d   = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    if to_d < from_d:
        raise HTTPException(400, "to_date must be >= from_date.")
    if (to_d - from_d).days > 366:
        raise HTTPException(400, "Date range cannot exceed 366 days.")

    from_utc, _  = _op_day_bounds_utc(from_d)
    _, to_utc    = _op_day_bounds_utc(to_d)   # end of to_date operational day

    # Shift timestamps by -3h30m into UTC day buckets, then shift back.
    # This aligns the 1-day bucket boundary to 03:30 UTC = 09:00 IST.
    sql = text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            time_bucket('1 day', td.timestamp - INTERVAL '3 hours 30 minutes')
                + INTERVAL '3 hours 30 minutes'                          AS operational_day,
            COUNT(*) FILTER (WHERE td.value_num > 0) * :poll_sec / 60.0 AS runtime_minutes,
            COUNT(*) * :poll_sec / 60.0                                  AS sampled_minutes
        FROM telemetry_data td
        JOIN machine_component_instance ci ON ci.id = td.component_instance_id
        JOIN machine m                     ON m.id  = ci.machine_id
        WHERE td.tag_definition_id  = :freq_tag
          AND td.company_id         = :company_id
          AND td.timestamp         >= :from_utc
          AND td.timestamp          < :to_utc
        GROUP BY m.id, m.name, operational_day
        ORDER BY operational_day, m.name
    """)

    rows = db.execute(sql, {
        "company_id": company_id,
        "freq_tag":   FREQUENCY_TAG_ID,
        "from_utc":   from_utc,
        "to_utc":     to_utc,
        "poll_sec":   APPROX_POLL_SEC,
    }).mappings().fetchall()

    daily_rows: list[DailyRuntimeRow] = []
    machine_index: dict[int, str] = {}

    for row in rows:
        runtime_min = float(row["runtime_minutes"])
        sampled_min = float(row["sampled_minutes"])
        runtime_pct = (runtime_min / 1440 * 100) if runtime_min > 0 else 0

        daily_rows.append(DailyRuntimeRow(
            machine_id      = row["machine_id"],
            machine_name    = row["machine_name"],
            operational_day = row["operational_day"],
            runtime_minutes = round(runtime_min, 1),
            runtime_pct     = round(runtime_pct, 1),
            sampled_minutes = round(sampled_min, 1),
        ))
        machine_index[row["machine_id"]] = row["machine_name"]

    summaries = [
        _build_summary(mid, mname, daily_rows)
        for mid, mname in sorted(machine_index.items(), key=lambda x: x[1])
    ]

    return FleetRangeResponse(
        from_date  = from_date,
        to_date    = to_date,
        bucket     = "day",
        daily_rows = daily_rows,
        summaries  = summaries,
    )


# ---------------------------------------------------------------------------
# GET /runtime/machines/{machine_id}/range
# ---------------------------------------------------------------------------

@router.get("/machines/{machine_id}/range", response_model=MachineRangeResponse)
def get_machine_runtime_range(
    machine_id:   int,
    from_date:    str     = Query(..., description="Start date YYYY-MM-DD"),
    to_date:      str     = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    Runtime for a single machine over a date range.
    Returns daily rows and summary stats.
    404 if machine_id does not belong to the authenticated tenant.
    """
    company_id = current_user["company_id"]

    # Ownership check — prevents cross-tenant data leakage
    machine = db.execute(text("""
        SELECT id, name FROM machine
        WHERE id = :machine_id AND company_id = :company_id
    """), {"machine_id": machine_id, "company_id": company_id}).mappings().first()

    if not machine:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    try:
        from_d = date.fromisoformat(from_date)
        to_d   = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    if to_d < from_d:
        raise HTTPException(400, "to_date must be >= from_date.")
    if (to_d - from_d).days > 366:
        raise HTTPException(400, "Date range cannot exceed 366 days.")

    from_utc, _ = _op_day_bounds_utc(from_d)
    _, to_utc   = _op_day_bounds_utc(to_d)

    sql = text("""
        SELECT
            time_bucket('1 day', td.timestamp - INTERVAL '3 hours 30 minutes')
                + INTERVAL '3 hours 30 minutes'                          AS operational_day,
            COUNT(*) FILTER (WHERE td.value_num > 0) * :poll_sec / 60.0 AS runtime_minutes,
            COUNT(*) * :poll_sec / 60.0                                  AS sampled_minutes
        FROM telemetry_data td
        JOIN machine_component_instance ci ON ci.id = td.component_instance_id
        WHERE td.tag_definition_id  = :freq_tag
          AND td.company_id         = :company_id
          AND ci.machine_id         = :machine_id
          AND td.timestamp         >= :from_utc
          AND td.timestamp          < :to_utc
        GROUP BY operational_day
        ORDER BY operational_day
    """)

    rows = db.execute(sql, {
        "company_id": company_id,
        "freq_tag":   FREQUENCY_TAG_ID,
        "machine_id": machine_id,
        "from_utc":   from_utc,
        "to_utc":     to_utc,
        "poll_sec":   APPROX_POLL_SEC,
    }).mappings().fetchall()

    machine_name = machine["name"]
    daily_rows: list[DailyRuntimeRow] = []

    for row in rows:
        runtime_min = float(row["runtime_minutes"])
        sampled_min = float(row["sampled_minutes"])
        runtime_pct = (runtime_min / 1440 * 100) if runtime_min > 0 else 0

        daily_rows.append(DailyRuntimeRow(
            machine_id      = machine_id,
            machine_name    = machine_name,
            operational_day = row["operational_day"],
            runtime_minutes = round(runtime_min, 1),
            runtime_pct     = round(runtime_pct, 1),
            sampled_minutes = round(sampled_min, 1),
        ))

    summary = _build_summary(machine_id, machine_name, daily_rows)

    return MachineRangeResponse(
        machine_id   = machine_id,
        machine_name = machine_name,
        from_date    = from_date,
        to_date      = to_date,
        daily_rows   = daily_rows,
        summary      = summary,
    )
