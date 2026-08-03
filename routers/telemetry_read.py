# Read-oriented telemetry endpoints for the dashboard frontend.
#
# Four endpoints live here:
#
#   GET /machines/live
#       Most-recent readings for every machine, pivoted by tag_key slug into a
#       tags dict.  Ready for the fleet dashboard to render without further
#       client-side aggregation.  Used to replace buildFleet() in Phase 5c.
#
#   GET /machines/{machine_id}/live
#       Same pivot for a single machine.  Used by the detail page header.
#
#   GET /fleet/summary
#       Derived from the same DISTINCT ON query as /machines/live.
#       Returns total/running/stopped machine counts and total power in kW.
#       Identification by tag_key slug ("frequency", "power") — not by integer
#       tag_definition_id, so it works correctly for every tenant.
#
#   GET /machines/{machine_id}/history
#       Long-form SQL (one row per bucket + tag_key) followed by Python pivot.
#       The pivot produces {"bucket": ..., "tags": {"frequency": 30.5, ...}}
#       per time step — symmetric with the live endpoint.  No hardcoded tag IDs.
#       Merges tags across every component instance owned by the machine.
#
#   GET /machines/{machine_id}/sensor-log, /machines/{machine_id}/sensor-log/pdf
#       5-minute average log (and PDF export) for one (machine_id, tag) pair
#       over one operational day (09:00 IST -> 09:00 IST next day). Generic —
#       works for any tag key any machine has (temperature, pressure, ...).
#
# Write endpoint (POST /data) stays in data_router.py — not touched here.

from datetime import datetime, timedelta, timezone, date as date_type
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from auth import get_current_user, get_tenant_db
from schemas.telemetry_read import (
    MachineTagsResponse,
    FleetSummaryResponse,
    HistoryBucketResponse,
    MachineHistoryResponse,
)


router = APIRouter()


# ---------------------------------------------------------------------------
# Private helper — shared by /machines/live, /machines/{id}/live, and /fleet/summary
# ---------------------------------------------------------------------------

def _get_latest_rows(db: Session, company_id: int) -> list:
    """Return the most-recent reading per (component, tag) for one tenant.

    PERFORMANCE NOTE (2026-07-19): Previously used a bare DISTINCT ON across
    the full telemetry_data table, which forced PostgreSQL into a sequential
    scan + external disk sort as the table grew past ~2M rows (13+ second
    query time, 60+ second API response under load). Fixed by first finding
    each component's single latest timestamp within a bounded recent window
    (uses the ix_telemetry_data_component_tag_ts_desc index for a fast range
    scan), then joining back to fetch only those exact rows. This avoids
    sorting the entire table on every request.

    Window is 10 minutes — comfortably covers the ~10-25s poll interval
    even accounting for a temporary gateway outage; if a component has no
    reading in the last 10 minutes it simply won't appear (correct — the
    frontend already treats missing data as NO_DATA/STALE).

    tag_key is the stable slug from tag_definition.key ("frequency", "power",
    …) — not the human-editable display name.  Callers use it as the key in
    the tags dict so frontend/gateway contracts are unaffected by name changes.

    The query is fully parameterised — company_id is bound, never interpolated.

    Returns a list of RowMapping objects; each field is accessible by name
    (e.g. row.machine_name, row.value_num).
    """
    sql = text("""
        WITH recent AS (
            SELECT
                td.component_instance_id,
                td.tag_definition_id,
                td.value_num,
                td.value_text,
                td.timestamp,
                ROW_NUMBER() OVER (
                    PARTITION BY td.component_instance_id, td.tag_definition_id
                    ORDER BY td.timestamp DESC
                ) AS rn
            FROM telemetry_data td
            WHERE td.company_id = :company_id
              AND td.timestamp > NOW() - INTERVAL '10 minutes'
        )
        SELECT
            r.component_instance_id,
            r.tag_definition_id,
            r.value_num,
            r.value_text,
            r.timestamp,
            m.id          AS machine_id,
            m.name        AS machine_name,
            tdef.key      AS tag_key
        FROM recent r
        JOIN machine_component_instance mci
          ON mci.id = r.component_instance_id
        JOIN machine m
          ON m.id = mci.machine_id
        JOIN tag_definition tdef
          ON tdef.id = r.tag_definition_id
        WHERE r.rn = 1
    """)

    result = db.execute(sql, {"company_id": company_id})

    return result.mappings().all()


def _pivot_rows(rows) -> list[dict]:
    """Group flat tag rows by machine_id, pivot tag_key → value_num into tags dict.

    Input: list of RowMapping objects from _get_latest_rows() — one row per
           (component, tag) combination, with machine_id and tag_key attached.
    Output: one dict per machine with all its latest tag values in a 'tags' sub-dict,
            keyed by tag slug (e.g. {"frequency": 30.5, "power": 22.1}).
    """
    machines: dict[int, dict] = {}
    for row in rows:
        mid = row["machine_id"]
        if mid not in machines:
            machines[mid] = {
                "machine_id":            mid,
                "machine_name":          row["machine_name"],
                "component_instance_id": row["component_instance_id"],
                "last_updated":          row["timestamp"],
                "tags":                  {},
            }
        # Key by slug, not display name — stable across operator renames
        machines[mid]["tags"][row["tag_key"]] = row["value_num"]
        # Keep last_updated as the most-recent timestamp across all tags
        if row["timestamp"] > machines[mid]["last_updated"]:
            machines[mid]["last_updated"] = row["timestamp"]
    return list(machines.values())


# ---------------------------------------------------------------------------
# GET /machines/live
# ---------------------------------------------------------------------------

@router.get("/machines/live", response_model=list[MachineTagsResponse])
def get_machines_live(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Return the most-recent readings for every machine, pivoted by tag slug.

    Each machine appears once; all its latest tag values are collapsed into a
    single tags dict keyed by slug (e.g. {"frequency": 30.5, "power": 22.1}).
    Phase 5c: replace buildFleet() in App.jsx with a fetch to this endpoint.
    """
    rows = _get_latest_rows(db, current_user["company_id"])
    return _pivot_rows(rows)


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/live
# ---------------------------------------------------------------------------

@router.get("/machines/{machine_id}/live", response_model=MachineTagsResponse)
def get_machine_live(
    machine_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Return the most-recent readings for a single machine, pivoted by tag slug.

    Reuses the fleet-wide DISTINCT ON query and filters in Python — avoids a
    separate per-machine query when the fleet data is already cached.
    Returns 404 if the machine_id belongs to a different tenant or does not exist.
    """
    rows = _get_latest_rows(db, current_user["company_id"])
    machine_rows = [r for r in rows if r["machine_id"] == machine_id]
    if not machine_rows:
        raise HTTPException(status_code=404, detail="Machine {} not found.".format(machine_id))
    return _pivot_rows(machine_rows)[0]


# ---------------------------------------------------------------------------
# GET /fleet/summary
# ---------------------------------------------------------------------------

@router.get("/fleet/summary", response_model=FleetSummaryResponse)
def get_fleet_summary(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Return high-level fleet KPIs derived from the latest readings.

    Shares the DISTINCT ON query result with /machines/live — no second
    round-trip to the database.

    Derivation logic:
    - total_machines : count of distinct machine_id values in the rows
    - running        : machines with a "frequency" tag reading > 0
    - stopped        : total_machines - running
    - total_power_kw : sum of "power" tag readings across all components
    - last_updated   : max timestamp across all rows

    Tag identification uses tag_key slugs, not hardcoded integer IDs, so this
    works correctly for every tenant regardless of their tag_definition IDs.
    Phase 5c: replace the KPI bar in FleetDashboard with a fetch to this endpoint.
    """
    rows = _get_latest_rows(db, current_user["company_id"])

    if not rows:
        # No telemetry in the DB yet — return safe zero-state rather than 404.
        return FleetSummaryResponse(
            total_machines=0,
            running=0,
            stopped=0,
            total_power_kw=0.0,
            last_updated=datetime.utcnow(),
        )

    # --- Derive KPIs in Python from the shared row set ---

    # Collect the latest frequency value per machine.
    # A machine may have multiple components; we take the max frequency
    # so that any running component marks the machine as running.
    freq_by_machine: dict[int, float] = {}
    power_total = 0.0
    max_ts = None

    for row in rows:
        # Track the most-recent timestamp across the whole fleet
        if max_ts is None or row["timestamp"] > max_ts:
            max_ts = row["timestamp"]

        tag_key = row["tag_key"]

        # "frequency" slug → Hz reading; > 0 means the reel motor is running
        if tag_key == "frequency" and row["value_num"] is not None:
            mid = row["machine_id"]
            freq_by_machine[mid] = max(freq_by_machine.get(mid, 0.0), row["value_num"])

        # "power" slug → kW reading; sum across all machines/components
        if tag_key == "power" and row["value_num"] is not None:
            power_total += row["value_num"]

    total_machines = len(freq_by_machine)
    running = sum(1 for freq in freq_by_machine.values() if freq > 0)
    stopped = total_machines - running

    return FleetSummaryResponse(
        total_machines=total_machines,
        running=running,
        stopped=stopped,
        total_power_kw=round(power_total, 2),
        last_updated=max_ts or datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/history
# ---------------------------------------------------------------------------

def _bucket_interval(hours: int) -> str:
    """Return the TimescaleDB time_bucket interval string for a given window.

    Scales bucket size so the result set stays at roughly 60–100 points
    regardless of the window length, keeping chart rendering fast.

    hours ≤ 1  → '1 minute'   (up to 60 points)
    hours ≤ 6  → '5 minutes'  (up to 72 points)
    hours ≤ 24 → '15 minutes' (up to 96 points)

    The returned string is embedded directly into the SQL query text, not bound
    as a parameter — but it is chosen from a fixed lookup, never from user input,
    so there is no injection risk.
    """
    if hours <= 1:
        return "1 minute"
    if hours <= 6:
        return "5 minutes"
    return "15 minutes"


@router.get(
    "/machines/{machine_id}/history",
    response_model=MachineHistoryResponse,
)
def get_history(
    machine_id: int,
    hours: int = Query(default=1, ge=1, le=24),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Return bucketed time-series data for one machine over a recent window.

    Uses a long-form SQL query (one row per bucket + tag combination) and a
    Python-side pivot.  This approach requires no hardcoded tag IDs — it works
    for any tenant's tag catalogue because the JOIN to tag_definition gives us
    the slug key, not an integer ID.

    Path param:
        machine_id — the machine.id to query.

    Query params:
        hours  — window length in hours (1–24, default 1).

    Ownership check:
        machine is queried for a row matching both machine_id and company_id.
        A valid machine_id belonging to a different tenant returns 404, not
        leaking the fact that the ID exists.

    Phase 5c: replace buildHistory() in App.jsx with a fetch to this endpoint.

    MULTI-COMPONENT NOTE (2026-08-04): A machine can own more than one
    component_instance — e.g. Jet 27 has a Reel Motor (VFD tags), a
    temperature sensor, and a pressure sensor, all sharing machine_id=14.
    The query below joins by mci.machine_id so tags from every component
    instance under this machine are merged into the same bucket/tag_key
    result set — symmetric with how _get_latest_rows()/_pivot_rows() already
    merge multi-component tags for /machines/live. Previously this endpoint
    resolved machine_id -> a single arbitrary component_instance_id via
    fetchone(), which silently dropped every tag not on that one component.
    """
    # --- Ownership check: does this machine belong to the tenant? ---
    machine_row = db.execute(
        text("SELECT id FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": current_user["company_id"]},
    ).fetchone()

    if machine_row is None:
        raise HTTPException(
            status_code=404,
            detail="Machine {} not found.".format(machine_id),
        )

    # --- Time window and bucket size ---
    since = datetime.utcnow() - timedelta(hours=hours)
    interval = _bucket_interval(hours)

    # --- Long-form query: one row per (bucket, tag_key), across ALL of this
    # machine's component instances. JOIN to tag_definition gives the slug key
    # so results are keyed by contract slug ("frequency", "temperature",
    # "pressure", …) rather than integer tag_definition_id.
    # The interval string is chosen from a fixed lookup — not user-supplied —
    # so embedding it directly in the SQL text is safe.
    sql = text("""
        SELECT
            time_bucket('{interval}', td.timestamp) AS bucket,
            tdef.key                                AS tag_key,
            AVG(td.value_num)                       AS avg_value
        FROM telemetry_data td
        JOIN machine_component_instance mci
          ON mci.id = td.component_instance_id
        JOIN tag_definition tdef
          ON tdef.id = td.tag_definition_id
        WHERE mci.machine_id  = :machine_id
          AND td.company_id   = :company_id
          AND td.timestamp   >= :since
        GROUP BY bucket, tdef.key
        ORDER BY bucket ASC, tdef.key
    """.format(interval=interval))

    rows = db.execute(sql, {
        "machine_id": machine_id,
        "company_id": current_user["company_id"],
        "since":      since,
    }).mappings().all()

    # --- Python-side pivot: collect (bucket, tag_key, avg_value) into per-bucket dicts ---
    # Each unique bucket gets one HistoryBucketResponse with all its tag values
    # in a tags dict, symmetric with the live endpoint shape.
    buckets_map: dict = {}
    for r in rows:
        b = r["bucket"]
        if b not in buckets_map:
            buckets_map[b] = {"bucket": b, "tags": {}}
        if r["avg_value"] is not None:
            buckets_map[b]["tags"][r["tag_key"]] = r["avg_value"]

    # Sort by bucket ascending; buckets_map insertion order is not guaranteed
    # across all Python versions when keys are datetime objects.
    data = [
        HistoryBucketResponse(**v)
        for v in sorted(buckets_map.values(), key=lambda x: x["bucket"])
    ]

    return MachineHistoryResponse(
        machine_id=machine_id,
        hours=hours,
        data=data,
    )


# ---------------------------------------------------------------------------
# GET /machines/{machine_id}/sensor-log, /machines/{machine_id}/sensor-log/pdf
#
# 5-minute average log for one (machine, tag) pair over an operational day
# (09:00 IST -> 09:00 IST next day), plus a downloadable PDF rendering.
#
# GENERIC BY DESIGN (2026-08-04): these replace the earlier hardcoded
# /sensors/temperature/log(/pdf) endpoints, which only ever worked for one
# sensor (component_instance_id=29, tag_definition_id=8). As more machines
# gain temperature/pressure/other sensors, a per-sensor hardcoded endpoint
# doesn't scale — these are parameterized by machine_id + tag key (the same
# slug convention /machines/live and /machines/{id}/history already use), so
# any (machine, tag) combination works without new backend code.
# ---------------------------------------------------------------------------

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _op_day_bounds_utc(op_date: date_type) -> tuple[datetime, datetime]:
    """Operational day: 09:00 IST -> 09:00 IST next day, as UTC bounds."""
    start_ist = datetime(op_date.year, op_date.month, op_date.day, 9, 0, 0)
    start_utc = start_ist.replace(tzinfo=timezone.utc) - _IST_OFFSET
    end_utc   = start_utc + timedelta(hours=24)
    return start_utc, end_utc


def _fetch_sensor_log_rows(db: Session, company_id: int, machine_id: int,
                            tag_key: str, op_date: date_type):
    """Shared query — 5-minute average buckets for one (machine, tag) over one operational day.

    Joins through machine_component_instance by machine_id (not a single
    resolved component_instance_id) so it works regardless of which physical
    component on the machine produces this tag — symmetric with the
    multi-component fix applied to /machines/{id}/history.
    """
    start_utc, end_utc = _op_day_bounds_utc(op_date)

    sql = text("""
        SELECT
            time_bucket('5 minutes', td.timestamp) AS bucket,
            AVG(td.value_num) AS avg_value
        FROM telemetry_data td
        JOIN machine_component_instance mci ON mci.id = td.component_instance_id
        JOIN tag_definition tdef            ON tdef.id = td.tag_definition_id
        WHERE mci.machine_id  = :machine_id
          AND tdef.key        = :tag_key
          AND td.company_id   = :company_id
          AND td.timestamp   >= :start_utc
          AND td.timestamp    <  :end_utc
        GROUP BY bucket
        ORDER BY bucket
    """)

    return db.execute(sql, {
        "machine_id": machine_id,
        "tag_key":    tag_key,
        "company_id": company_id,
        "start_utc":  start_utc,
        "end_utc":    end_utc,
    }).mappings().fetchall()


def _get_machine_name(db: Session, company_id: int, machine_id: int) -> str | None:
    row = db.execute(
        text("SELECT name FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": company_id},
    ).mappings().first()
    return row["name"] if row else None


def _get_tag_meta(db: Session, company_id: int, tag_key: str):
    """Return {"name": ..., "unit": ...} for a tag_definition.key, or None."""
    row = db.execute(
        text("SELECT name, unit FROM tag_definition WHERE key = :tag_key AND company_id = :company_id"),
        {"tag_key": tag_key, "company_id": company_id},
    ).mappings().first()
    return dict(row) if row else None


@router.get("/machines/{machine_id}/sensor-log")
def get_machine_sensor_log(
    machine_id: int,
    tag: str = Query(..., description="Tag key, e.g. 'temperature' or 'pressure'"),
    date: str = Query(..., description="Operational day start date YYYY-MM-DD (IST)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    5-minute average readings for one (machine, tag) pair over one operational
    day (09:00 IST -> 09:00 IST next day).
    """
    company_id = current_user["company_id"]

    if _get_machine_name(db, company_id, machine_id) is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    try:
        op_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    rows = _fetch_sensor_log_rows(db, company_id, machine_id, tag, op_date)

    return {
        "machine_id": machine_id,
        "tag": tag,
        "date": date,
        "shift_start_ist": "09:00",
        "shift_end_ist": "09:00 (+1 day)",
        "readings": [
            {
                # 24-hour clock — matches the trend chart's axis format.
                "time": (r["bucket"] + _IST_OFFSET).strftime("%H:%M"),
                "timestamp": r["bucket"].isoformat(),
                "avg_value": round(float(r["avg_value"]), 1),
            }
            for r in rows
        ],
    }


@router.get("/machines/{machine_id}/sensor-log/pdf")
def get_machine_sensor_log_pdf(
    machine_id: int,
    tag: str = Query(..., description="Tag key, e.g. 'temperature' or 'pressure'"),
    date: str = Query(..., description="Operational day start date YYYY-MM-DD (IST)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    Same data as /machines/{machine_id}/sensor-log, rendered as a downloadable
    PDF report. Title, unit, and filename are all derived from the machine and
    tag_definition rows — nothing sensor-specific is hardcoded.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    company_id = current_user["company_id"]

    machine_name = _get_machine_name(db, company_id, machine_id)
    if machine_name is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    tag_meta = _get_tag_meta(db, company_id, tag)
    if tag_meta is None:
        raise HTTPException(404, f"Unknown sensor tag '{tag}'.")
    tag_name, tag_unit = tag_meta["name"], tag_meta["unit"]

    try:
        op_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    rows = _fetch_sensor_log_rows(db, company_id, machine_id, tag, op_date)

    if not rows:
        raise HTTPException(404, "No data for this date.")

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
    story.append(Paragraph(f"Mevion — {tag_name} Log", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{machine_name} · Operational day {op_date.strftime('%d %b %Y')} "
        f"(09:00 to 09:00 next day, IST)",
        subtitle_style
    ))
    story.append(Spacer(1, 16))

    # Summary stats
    values = [float(r["avg_value"]) for r in rows]
    summary_data = [
        ["Readings", "Avg", "Min", "Max"],
        [str(len(values)), f"{sum(values)/len(values):.1f}{tag_unit}",
         f"{min(values):.1f}{tag_unit}", f"{max(values):.1f}{tag_unit}"],
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

    # Main log table — two columns side by side to fit more rows per page
    # (5-min intervals over 24h = up to 288 rows; single column would be very long)
    value_header = f"Avg {tag_unit}"
    table_data = [["Time", value_header, "", "Time", value_header]]
    half = (len(rows) + 1) // 2
    left_rows = rows[:half]
    right_rows = rows[half:]

    for i in range(half):
        left_time = (left_rows[i]["bucket"] + _IST_OFFSET).strftime("%H:%M")
        left_val  = f"{float(left_rows[i]['avg_value']):.1f}"
        if i < len(right_rows):
            right_time = (right_rows[i]["bucket"] + _IST_OFFSET).strftime("%H:%M")
            right_val  = f"{float(right_rows[i]['avg_value']):.1f}"
        else:
            right_time, right_val = "", ""
        table_data.append([left_time, left_val, "", right_time, right_val])

    log_table = Table(table_data, colWidths=[1.1*inch, 0.8*inch, 0.3*inch, 1.1*inch, 0.8*inch])
    log_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#dc2626')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (1,-1), 0.5, colors.HexColor('#e5e7eb')),
        ('GRID', (3,0), (4,-1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0,1), (1,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ('ROWBACKGROUNDS', (3,1), (4,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(log_table)

    doc.build(story)
    buffer.seek(0)

    machine_slug = machine_name.lower().replace(" ", "-")
    filename = f"mevion-{machine_slug}-{tag}-{date}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
