"""
Energy consumption router.

GET /energy/fleet/current-shift     — kWh per machine since shift start
GET /energy/fleet/range             — kWh per machine per operational day
GET /energy/machines/{id}/range     — kWh for one machine over date range

Power tag: tag_definition_id = 7 (Output Power in kW) — hard contract.
Operational day: 09:00 IST → 09:00 IST (03:30 UTC → 03:30 UTC).
kWh = SUM(power_kW) × POLL_INTERVAL_SEC / 3600
Electricity tariff: ₹9 per kWh (SSPPL Surat industrial rate).
"""

import logging
from datetime import datetime, timezone, timedelta, date

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from auth import get_current_user          # auth.py, not security.py
from schemas.energy import (
    ShiftEnergyResponse,
    DailyEnergyRow,
    MachineEnergySummary,
    FleetEnergyRangeResponse,
    MachineEnergyRangeResponse,
)

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/energy", tags=["energy"])

# Hard contracts — do not change
POWER_TAG_ID       = 7        # Output Power in kW — verified 2026-07-16
POLL_INTERVAL_SEC  = 23       # approximate seconds between readings
TARIFF_PER_KWH_INR = 9.0     # ₹9 per kWh — SSPPL Surat industrial rate
IST_OFFSET         = timedelta(hours=5, minutes=30)
DAY_SHIFT_START_H  = 9        # 09:00 IST
DAY_SHIFT_END_H    = 21       # 21:00 IST


# ---------------------------------------------------------------------------
# Helpers — same shift/day logic as runtime router
# ---------------------------------------------------------------------------

def _current_shift_bounds_utc() -> tuple[datetime, datetime, str]:
    """Return (shift_start_utc, shift_end_utc, shift_name) for the current shift."""
    now_utc  = datetime.now(timezone.utc)
    now_ist  = now_utc + IST_OFFSET
    hour_ist = now_ist.hour
    ist_date = now_ist.date()

    if DAY_SHIFT_START_H <= hour_ist < DAY_SHIFT_END_H:
        shift_start_ist = datetime(ist_date.year, ist_date.month, ist_date.day,
                                   DAY_SHIFT_START_H, 0, 0)
        shift_end_ist   = datetime(ist_date.year, ist_date.month, ist_date.day,
                                   DAY_SHIFT_END_H, 0, 0)
        shift_name = "day"
    else:
        if hour_ist >= DAY_SHIFT_END_H:
            shift_start_ist = datetime(ist_date.year, ist_date.month, ist_date.day,
                                       DAY_SHIFT_END_H, 0, 0)
        else:
            yesterday = ist_date - timedelta(days=1)
            shift_start_ist = datetime(yesterday.year, yesterday.month, yesterday.day,
                                       DAY_SHIFT_END_H, 0, 0)
        shift_end_ist = shift_start_ist + timedelta(hours=12)
        shift_name = "night"

    # Convert IST naive datetimes to UTC-aware datetimes.
    shift_start_utc = shift_start_ist.replace(tzinfo=timezone.utc) - IST_OFFSET
    shift_end_utc   = shift_end_ist.replace(tzinfo=timezone.utc)   - IST_OFFSET
    return shift_start_utc, shift_end_utc, shift_name


def _op_day_bounds_utc(op_date: date) -> tuple[datetime, datetime]:
    """Return (start_utc, end_utc) for an operational day (09:00 IST start)."""
    start_ist = datetime(op_date.year, op_date.month, op_date.day,
                         DAY_SHIFT_START_H, 0, 0)
    start_utc = start_ist.replace(tzinfo=timezone.utc) - IST_OFFSET
    end_utc   = start_utc + timedelta(hours=24)
    return start_utc, end_utc


def _build_energy_summary(machine_id: int, machine_name: str,
                           daily_rows: list[DailyEnergyRow]) -> MachineEnergySummary:
    """Compute summary stats from daily rows for one machine."""
    rows = [r for r in daily_rows if r.machine_id == machine_id]
    if not rows:
        return MachineEnergySummary(
            machine_id=machine_id, machine_name=machine_name,
            total_kwh=0, total_cost_inr=0,
            avg_kwh_per_day=0, peak_day_kwh=0, days_with_data=0,
        )

    total_kwh      = sum(r.kwh_consumed for r in rows)
    days_with_data = len([r for r in rows if r.kwh_consumed > 0])
    avg_per_day    = total_kwh / days_with_data if days_with_data > 0 else 0
    peak_day       = max((r.kwh_consumed for r in rows), default=0)

    return MachineEnergySummary(
        machine_id      = machine_id,
        machine_name    = machine_name,
        total_kwh       = round(total_kwh, 2),
        total_cost_inr  = round(total_kwh * TARIFF_PER_KWH_INR, 2),
        avg_kwh_per_day = round(avg_per_day, 2),
        peak_day_kwh    = round(peak_day, 2),
        days_with_data  = days_with_data,
    )


# ---------------------------------------------------------------------------
# GET /energy/fleet/current-shift
# ---------------------------------------------------------------------------

@router.get("/fleet/current-shift", response_model=list[ShiftEnergyResponse])
def get_fleet_current_shift_energy(
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    kWh consumed per machine since the current shift started.
    Left-joins machine → telemetry so every machine appears even with no data.
    """
    company_id = current_user["company_id"]
    shift_start, shift_end, shift_name = _current_shift_bounds_utc()
    shift_start_ist = "09:00" if shift_name == "day" else "21:00"
    shift_end_ist   = "21:00" if shift_name == "day" else "09:00"

    # kWh = SUM(power_kW readings) × poll_interval_sec / 3600
    sql = text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            COALESCE(
                SUM(td.value_num) * :poll_sec / 3600.0,
                0
            )      AS kwh_consumed
        FROM machine m
        JOIN machine_component_instance ci ON ci.machine_id = m.id
        LEFT JOIN telemetry_data td
               ON td.component_instance_id = ci.id
              AND td.tag_definition_id     = :power_tag
              AND td.timestamp            >= :shift_start
              AND td.timestamp             < :shift_end
              AND td.company_id            = :company_id
        WHERE m.company_id = :company_id
        GROUP BY m.id, m.name
        ORDER BY m.name
    """)

    rows = db.execute(sql, {
        "company_id":  company_id,
        "power_tag":   POWER_TAG_ID,
        "shift_start": shift_start,
        "shift_end":   shift_end,
        "poll_sec":    POLL_INTERVAL_SEC,
    }).mappings().fetchall()

    return [
        ShiftEnergyResponse(
            machine_id      = row["machine_id"],
            machine_name    = row["machine_name"],
            shift           = shift_name,
            shift_start_ist = shift_start_ist,
            shift_end_ist   = shift_end_ist,
            kwh_consumed    = round(float(row["kwh_consumed"]), 2),
            cost_inr        = round(float(row["kwh_consumed"]) * TARIFF_PER_KWH_INR, 2),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# GET /energy/fleet/range
# ---------------------------------------------------------------------------

@router.get("/fleet/range", response_model=FleetEnergyRangeResponse)
def get_fleet_energy_range(
    from_date:    str     = Query(..., description="Start date YYYY-MM-DD"),
    to_date:      str     = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    kWh per machine per operational day over a date range.
    Operational day: 09:00 IST → 09:00 IST next day (03:30 UTC boundary).
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

    from_utc, _ = _op_day_bounds_utc(from_d)
    _, to_utc   = _op_day_bounds_utc(to_d)

    # Shift timestamps by -3h30m before bucketing so the daily boundary
    # aligns to 03:30 UTC = 09:00 IST (operational day start).
    sql = text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            time_bucket('1 day', td.timestamp - INTERVAL '3 hours 30 minutes')
                + INTERVAL '3 hours 30 minutes'                           AS operational_day,
            SUM(td.value_num) * :poll_sec / 3600.0                        AS kwh_consumed
        FROM telemetry_data td
        JOIN machine_component_instance ci ON ci.id = td.component_instance_id
        JOIN machine m                     ON m.id  = ci.machine_id
        WHERE td.tag_definition_id  = :power_tag
          AND td.company_id         = :company_id
          AND td.timestamp         >= :from_utc
          AND td.timestamp          < :to_utc
        GROUP BY m.id, m.name, operational_day
        ORDER BY operational_day, m.name
    """)

    rows = db.execute(sql, {
        "company_id": company_id,
        "power_tag":  POWER_TAG_ID,
        "from_utc":   from_utc,
        "to_utc":     to_utc,
        "poll_sec":   POLL_INTERVAL_SEC,
    }).mappings().fetchall()

    daily_rows: list[DailyEnergyRow] = []
    machine_index: dict[int, str]    = {}

    for row in rows:
        kwh = float(row["kwh_consumed"])
        daily_rows.append(DailyEnergyRow(
            machine_id      = row["machine_id"],
            machine_name    = row["machine_name"],
            operational_day = row["operational_day"],
            kwh_consumed    = round(kwh, 2),
            cost_inr        = round(kwh * TARIFF_PER_KWH_INR, 2),
        ))
        machine_index[row["machine_id"]] = row["machine_name"]

    summaries = [
        _build_energy_summary(mid, mname, daily_rows)
        for mid, mname in sorted(machine_index.items(), key=lambda x: x[1])
    ]

    return FleetEnergyRangeResponse(
        from_date          = from_date,
        to_date            = to_date,
        tariff_per_kwh_inr = TARIFF_PER_KWH_INR,
        daily_rows         = daily_rows,
        summaries          = summaries,
    )


# ---------------------------------------------------------------------------
# GET /energy/machines/{machine_id}/range
# ---------------------------------------------------------------------------

@router.get("/machines/{machine_id}/range", response_model=MachineEnergyRangeResponse)
def get_machine_energy_range(
    machine_id:   int,
    from_date:    str     = Query(..., description="Start date YYYY-MM-DD"),
    to_date:      str     = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    kWh for a single machine over a date range.
    404 if machine_id does not belong to the authenticated tenant.
    """
    company_id = current_user["company_id"]

    # Ownership check — prevents cross-tenant data leakage.
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
                + INTERVAL '3 hours 30 minutes'                           AS operational_day,
            SUM(td.value_num) * :poll_sec / 3600.0                        AS kwh_consumed
        FROM telemetry_data td
        JOIN machine_component_instance ci ON ci.id = td.component_instance_id
        WHERE td.tag_definition_id  = :power_tag
          AND td.company_id         = :company_id
          AND ci.machine_id         = :machine_id
          AND td.timestamp         >= :from_utc
          AND td.timestamp          < :to_utc
        GROUP BY operational_day
        ORDER BY operational_day
    """)

    rows = db.execute(sql, {
        "company_id": company_id,
        "power_tag":  POWER_TAG_ID,
        "machine_id": machine_id,
        "from_utc":   from_utc,
        "to_utc":     to_utc,
        "poll_sec":   POLL_INTERVAL_SEC,
    }).mappings().fetchall()

    daily_rows: list[DailyEnergyRow] = []
    for row in rows:
        kwh = float(row["kwh_consumed"])
        daily_rows.append(DailyEnergyRow(
            machine_id      = machine_id,
            machine_name    = machine["name"],
            operational_day = row["operational_day"],
            kwh_consumed    = round(kwh, 2),
            cost_inr        = round(kwh * TARIFF_PER_KWH_INR, 2),
        ))

    summary = _build_energy_summary(machine_id, machine["name"], daily_rows)

    return MachineEnergyRangeResponse(
        machine_id         = machine_id,
        machine_name       = machine["name"],
        from_date          = from_date,
        to_date            = to_date,
        tariff_per_kwh_inr = TARIFF_PER_KWH_INR,
        daily_rows         = daily_rows,
        summary            = summary,
    )
