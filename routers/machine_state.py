"""
Machine state-timeline / utilization endpoints — powers the Uptime tab's
Gantt timeline, heatmap calendar, and OEE availability cards.

All three endpoints read from machine_state_event (migration 006), a
dedicated event log of running/stopped transitions written by
services/state_tracker.py — never from raw telemetry_data. This keeps these
endpoints cheap regardless of how much history accumulates: a handful of
rows per machine per day, not a scan over tens of thousands of raw readings.

  GET /machines/{machine_id}/state-timeline?from=&to=
      State intervals for one machine — powers the single-row Gantt view.

  GET /machines/state-timeline?from=&to=
      Same, but for every machine at once, grouped by machine_id in a single
      query — powers the multi-row fleet Gantt view. Avoids N+1 queries.

  GET /machines/{machine_id}/utilization-daily?from_date=&to_date=
      One row per operational day: running minutes vs elapsed minutes,
      computed by intersecting state_event intervals with each day's
      09:00 IST -> 09:00 IST boundary. Powers the heatmap calendar and the
      OEE availability cards.

  GET /machines/{machine_id}/state-log/pdf?date=
      One machine's full operational day of running/stopped intervals,
      rendered as a downloadable PDF — powers the Machine Log view's export
      button. Same reportlab structure as
      telemetry_read.py::get_machine_sensor_log_pdf (title/subtitle, summary
      table, striped interval table), different columns.
"""

import io
from datetime import datetime, timedelta, timezone, date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from auth import get_current_user, get_tenant_db
from schemas.machine_state import (
    StateInterval,
    MachineStateTimelineResponse,
    FleetStateTimelineResponse,
    DailyUtilizationRow,
    MachineUtilizationDailyResponse,
    FleetUtilizationDailyResponse,
)

router = APIRouter(prefix="/machines", tags=["machine-state"])

IST_OFFSET        = timedelta(hours=5, minutes=30)
DAY_SHIFT_START_H = 9   # operational day starts 09:00 IST — same convention as
                        # routers/runtime.py and routers/energy.py


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 datetime string (with or without a trailing Z) to
    an aware UTC datetime."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"Invalid datetime '{value}'. Use ISO-8601, e.g. 2026-08-05T00:00:00Z.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _op_day_bounds_utc(op_date: date_type) -> tuple[datetime, datetime]:
    """Operational day: 09:00 IST -> 09:00 IST next day, as UTC bounds."""
    start_ist = datetime(op_date.year, op_date.month, op_date.day, DAY_SHIFT_START_H, 0, 0)
    start_utc = start_ist.replace(tzinfo=timezone.utc) - IST_OFFSET
    end_utc   = start_utc + timedelta(hours=24)
    return start_utc, end_utc


def _compute_daily_utilization(
    intervals: list[tuple[str, datetime, datetime]],
    from_d: date_type,
    to_d: date_type,
) -> list[DailyUtilizationRow]:
    """Bucket a small list of (state, started_at, ended_at) intervals — already
    capped at "now" for any open interval — into one row per operational day.

    Intersects each interval with each day's [day_start, day_end) window;
    intervals per machine are few (a handful per day), so doing this in
    Python rather than a SQL generate_series/LATERAL join keeps the query
    itself trivial while this loop stays cheap.
    """
    rows: list[DailyUtilizationRow] = []
    d = from_d
    while d <= to_d:
        day_start, day_end = _op_day_bounds_utc(d)
        running_min = 0.0
        elapsed_min = 0.0
        for state, started_at, ended_at in intervals:
            overlap_start = max(started_at, day_start)
            overlap_end   = min(ended_at, day_end)
            if overlap_end > overlap_start:
                minutes = (overlap_end - overlap_start).total_seconds() / 60.0
                elapsed_min += minutes
                if state == "running":
                    running_min += minutes

        utilization_pct = (running_min / elapsed_min * 100) if elapsed_min > 0 else 0.0
        rows.append(DailyUtilizationRow(
            operational_day=day_start,
            running_minutes=round(running_min, 1),
            elapsed_minutes=round(elapsed_min, 1),
            utilization_pct=round(utilization_pct, 1),
        ))
        d += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/state-timeline
# ---------------------------------------------------------------------------

@router.get("/{machine_id}/state-timeline", response_model=MachineStateTimelineResponse)
def get_machine_state_timeline(
    machine_id: int,
    from_ts: str = Query(..., alias="from", description="Start of range, ISO-8601 UTC"),
    to_ts:   str = Query(..., alias="to",   description="End of range, ISO-8601 UTC"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """State intervals for one machine over a time range, including the
    currently-open interval (ended_at IS NULL) capped at "now" for display."""
    company_id = current_user["company_id"]

    machine = db.execute(
        text("SELECT id, name FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": company_id},
    ).mappings().first()
    if machine is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    from_utc = _parse_iso_utc(from_ts)
    to_utc   = _parse_iso_utc(to_ts)
    if to_utc < from_utc:
        raise HTTPException(400, "'to' must be >= 'from'.")

    rows = db.execute(text("""
        SELECT state, started_at, COALESCE(ended_at, NOW()) AS ended_at
        FROM machine_state_event
        WHERE machine_id = :machine_id
          AND company_id = :company_id
          AND started_at < :to_utc
          AND COALESCE(ended_at, NOW()) > :from_utc
        ORDER BY started_at
    """), {
        "machine_id": machine_id, "company_id": company_id,
        "from_utc": from_utc, "to_utc": to_utc,
    }).mappings().fetchall()

    intervals = [
        StateInterval(state=r["state"], started_at=r["started_at"], ended_at=r["ended_at"])
        for r in rows
    ]

    return MachineStateTimelineResponse(
        machine_id=machine_id,
        machine_name=machine["name"],
        range_from=from_ts,
        range_to=to_ts,
        intervals=intervals,
    )


# ---------------------------------------------------------------------------
# GET /machines/state-timeline
# ---------------------------------------------------------------------------

@router.get("/state-timeline", response_model=FleetStateTimelineResponse)
def get_fleet_state_timeline(
    from_ts: str = Query(..., alias="from", description="Start of range, ISO-8601 UTC"),
    to_ts:   str = Query(..., alias="to",   description="End of range, ISO-8601 UTC"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """State intervals for every machine over a time range, in one query
    grouped by machine_id — avoids N+1 queries for the multi-row Gantt view."""
    company_id = current_user["company_id"]

    from_utc = _parse_iso_utc(from_ts)
    to_utc   = _parse_iso_utc(to_ts)
    if to_utc < from_utc:
        raise HTTPException(400, "'to' must be >= 'from'.")

    rows = db.execute(text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            e.state,
            e.started_at,
            COALESCE(e.ended_at, NOW()) AS ended_at
        FROM machine_state_event e
        JOIN machine m ON m.id = e.machine_id
        WHERE e.company_id  = :company_id
          AND e.started_at  < :to_utc
          AND COALESCE(e.ended_at, NOW()) > :from_utc
        ORDER BY m.name, e.started_at
    """), {
        "company_id": company_id,
        "from_utc": from_utc, "to_utc": to_utc,
    }).mappings().fetchall()

    machines: dict[int, MachineStateTimelineResponse] = {}
    for r in rows:
        mid = r["machine_id"]
        if mid not in machines:
            machines[mid] = MachineStateTimelineResponse(
                machine_id=mid,
                machine_name=r["machine_name"],
                range_from=from_ts,
                range_to=to_ts,
                intervals=[],
            )
        machines[mid].intervals.append(
            StateInterval(state=r["state"], started_at=r["started_at"], ended_at=r["ended_at"])
        )

    return FleetStateTimelineResponse(
        range_from=from_ts,
        range_to=to_ts,
        machines=sorted(machines.values(), key=lambda m: m.machine_name),
    )


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/utilization-daily
# ---------------------------------------------------------------------------

@router.get("/{machine_id}/utilization-daily", response_model=MachineUtilizationDailyResponse)
def get_machine_utilization_daily(
    machine_id: int,
    from_date: str = Query(..., description="Start date YYYY-MM-DD (operational day, IST)"),
    to_date:   str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """One row per operational day: running minutes vs elapsed minutes,
    computed from machine_state_event — never raw telemetry."""
    company_id = current_user["company_id"]

    machine = db.execute(
        text("SELECT id, name FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": company_id},
    ).mappings().first()
    if machine is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    try:
        from_d = date_type.fromisoformat(from_date)
        to_d   = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    if to_d < from_d:
        raise HTTPException(400, "to_date must be >= from_date.")
    if (to_d - from_d).days > 366:
        raise HTTPException(400, "Date range cannot exceed 366 days.")

    from_utc, _ = _op_day_bounds_utc(from_d)
    _, to_utc   = _op_day_bounds_utc(to_d)

    rows = db.execute(text("""
        SELECT state, started_at, COALESCE(ended_at, NOW()) AS ended_at
        FROM machine_state_event
        WHERE machine_id = :machine_id
          AND company_id = :company_id
          AND started_at < :to_utc
          AND COALESCE(ended_at, NOW()) > :from_utc
        ORDER BY started_at
    """), {
        "machine_id": machine_id, "company_id": company_id,
        "from_utc": from_utc, "to_utc": to_utc,
    }).mappings().fetchall()

    intervals = [(r["state"], r["started_at"], r["ended_at"]) for r in rows]
    daily_rows = _compute_daily_utilization(intervals, from_d, to_d)

    return MachineUtilizationDailyResponse(
        machine_id=machine_id,
        machine_name=machine["name"],
        from_date=from_date,
        to_date=to_date,
        daily_rows=daily_rows,
    )


# ---------------------------------------------------------------------------
# GET /machines/utilization-daily
# ---------------------------------------------------------------------------

@router.get("/utilization-daily", response_model=FleetUtilizationDailyResponse)
def get_fleet_utilization_daily(
    from_date: str = Query(..., description="Start date YYYY-MM-DD (operational day, IST)"),
    to_date:   str = Query(..., description="End date YYYY-MM-DD (inclusive)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Daily utilization for every machine over a date range, in one query
    grouped by machine_id — powers the heatmap calendar and OEE availability
    cards without one request per machine (33 machines would otherwise mean
    33 sequential round trips)."""
    company_id = current_user["company_id"]

    try:
        from_d = date_type.fromisoformat(from_date)
        to_d   = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    if to_d < from_d:
        raise HTTPException(400, "to_date must be >= from_date.")
    if (to_d - from_d).days > 366:
        raise HTTPException(400, "Date range cannot exceed 366 days.")

    from_utc, _ = _op_day_bounds_utc(from_d)
    _, to_utc   = _op_day_bounds_utc(to_d)

    rows = db.execute(text("""
        SELECT
            m.id   AS machine_id,
            m.name AS machine_name,
            e.state,
            e.started_at,
            COALESCE(e.ended_at, NOW()) AS ended_at
        FROM machine_state_event e
        JOIN machine m ON m.id = e.machine_id
        WHERE e.company_id  = :company_id
          AND e.started_at  < :to_utc
          AND COALESCE(e.ended_at, NOW()) > :from_utc
        ORDER BY m.name, e.started_at
    """), {
        "company_id": company_id,
        "from_utc": from_utc, "to_utc": to_utc,
    }).mappings().fetchall()

    # Group rows by machine_id in Python, then reuse the same day-bucketing
    # helper as the single-machine endpoint for each machine's interval list.
    by_machine: dict[int, dict] = {}
    for r in rows:
        mid = r["machine_id"]
        if mid not in by_machine:
            by_machine[mid] = {"machine_name": r["machine_name"], "intervals": []}
        by_machine[mid]["intervals"].append((r["state"], r["started_at"], r["ended_at"]))

    machines = [
        MachineUtilizationDailyResponse(
            machine_id=mid,
            machine_name=info["machine_name"],
            from_date=from_date,
            to_date=to_date,
            daily_rows=_compute_daily_utilization(info["intervals"], from_d, to_d),
        )
        for mid, info in by_machine.items()
    ]
    machines.sort(key=lambda m: m.machine_name)

    return FleetUtilizationDailyResponse(
        from_date=from_date,
        to_date=to_date,
        machines=machines,
    )


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/state-log/pdf
# ---------------------------------------------------------------------------

@router.get("/{machine_id}/state-log/pdf")
def get_machine_state_log_pdf(
    machine_id: int,
    date: str = Query(..., description="Operational day date YYYY-MM-DD (IST)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    One machine's full operational day of running/stopped intervals, rendered
    as a downloadable PDF — the Machine Log view's export button. Same
    reportlab structure as telemetry_read.py::get_machine_sensor_log_pdf
    (title/subtitle, summary table, striped interval table), just different
    columns (Status/Start/End/Duration instead of Time/Avg value).
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    company_id = current_user["company_id"]

    machine = db.execute(
        text("SELECT id, name FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": company_id},
    ).mappings().first()
    if machine is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")
    machine_name = machine["name"]

    try:
        op_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    from_utc, to_utc = _op_day_bounds_utc(op_date)

    rows = db.execute(text("""
        SELECT state, started_at, COALESCE(ended_at, NOW()) AS ended_at
        FROM machine_state_event
        WHERE machine_id = :machine_id
          AND company_id = :company_id
          AND started_at < :to_utc
          AND COALESCE(ended_at, NOW()) > :from_utc
        ORDER BY started_at
    """), {
        "machine_id": machine_id, "company_id": company_id,
        "from_utc": from_utc, "to_utc": to_utc,
    }).mappings().fetchall()

    if not rows:
        raise HTTPException(404, "No state data for this date.")

    # --- Summary stats, same fields the JSON table view computes client-side ---
    running_min = 0.0
    stopped_min = 0.0
    stoppage_count = 0
    longest_stoppage_min = 0.0
    for r in rows:
        minutes = (r["ended_at"] - r["started_at"]).total_seconds() / 60.0
        if r["state"] == "running":
            running_min += minutes
        else:
            stopped_min += minutes
            stoppage_count += 1
            longest_stoppage_min = max(longest_stoppage_min, minutes)

    # --- Build PDF in memory ---
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.6*inch, bottomMargin=0.6*inch,
        leftMargin=0.7*inch, rightMargin=0.7*inch,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'MevionTitle', parent=styles['Title'],
        textColor=colors.HexColor('#dc2626'), fontSize=18,
    )
    subtitle_style = ParagraphStyle(
        'MevionSubtitle', parent=styles['Normal'],
        textColor=colors.HexColor('#6b7280'), fontSize=10,
    )

    story = []
    story.append(Paragraph(f"Mevion — Machine Log", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{machine_name} · Operational day {op_date.strftime('%d %b %Y')} "
        f"(09:00 to 09:00 next day, IST)",
        subtitle_style
    ))
    story.append(Spacer(1, 16))

    # Summary stats
    def _fmt_minutes(m):
        h, mm = divmod(round(m), 60)
        return f"{h}h {mm}m" if h else f"{mm}m"

    summary_data = [
        ["Total Running", "Total Stopped", "Stoppage Events", "Longest Stop"],
        [_fmt_minutes(running_min), _fmt_minutes(stopped_min),
         str(stoppage_count), _fmt_minutes(longest_stoppage_min)],
    ]
    summary_table = Table(summary_data, colWidths=[1.4*inch]*4)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f3f4f6')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#374151')),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,1), (-1,1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 10),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))

    # Main interval table — one row per running/stopped interval
    table_data = [["Status", "Start", "End", "Duration"]]
    for r in rows:
        start_ist = r["started_at"] + IST_OFFSET
        end_ist   = r["ended_at"] + IST_OFFSET
        minutes   = (r["ended_at"] - r["started_at"]).total_seconds() / 60.0
        table_data.append([
            "Running" if r["state"] == "running" else "Stopped",
            start_ist.strftime("%H:%M:%S"),
            end_ist.strftime("%H:%M:%S"),
            _fmt_minutes(minutes),
        ])

    log_table = Table(table_data, colWidths=[1.1*inch, 1.3*inch, 1.3*inch, 1.1*inch])
    log_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#dc2626')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(log_table)

    doc.build(story)
    buffer.seek(0)

    machine_slug = machine_name.lower().replace(" ", "-")
    filename = f"mevion-{machine_slug}-machine-log-{date}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
