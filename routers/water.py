"""
Water consumption endpoint.

GET /machines/{machine_id}/water/consumption?date=YYYY-MM-DD

Computed on-demand from raw telemetry_data — no new table, no scheduled
snapshot job (same reasoning as the Machine Log feature: a dedicated
write-every-30-min job is a second thing that can silently fail; deriving
from data already being continuously collected has no such failure mode).

The flowmeter's totalizer is cumulative, not a rate — so unlike energy.py's
kWh (SUM of power readings), consumption here is always
(nearest reading at/before window end) - (nearest reading at/before window
start). "Nearest at/before" is looked up by scanning telemetry rows already
loaded for the day in Python (bisect on a sorted timestamp list) rather than
one SQL round-trip per boundary — same "small number of simple queries + a
Python loop" style already used in routers/machine_state.py for utilization
bucketing, rather than a generate_series/LATERAL SQL query.

Operational day: 09:00 IST -> 09:00 IST next day (03:30 UTC -> 03:30 UTC),
same convention as routers/energy.py and routers/machine_state.py.

The flowmeter tag is resolved by key ("flow_totalizer"), not a hardcoded
tag_definition_id — tag_definition.key is documented as the stable
cross-company contract (see models/tag_definition.py), and ids are assigned
per-company by whatever migration/seed created them (see
alembic/versions/007_flow_meter.py for SSPPL's ids, which are not assumed
here). Same for the flowmeter component: resolved as "whichever component
instance on this machine produces a flow_totalizer tag", not a hardcoded
component_type_id — a machine with no such component 404s.
"""

import bisect
import io
from datetime import datetime, timedelta, date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from auth import get_current_user, get_tenant_db
from schemas.water import (
    WaterWindowTotal,
    WaterConsumptionTotals,
    WaterIntervalBucket,
    WaterConsumptionResponse,
    WaterReportPeriod,
    WaterReportResponse,
)

router = APIRouter(prefix="/machines", tags=["water"])

IST_OFFSET        = timedelta(hours=5, minutes=30)
DAY_SHIFT_START_H = 9    # 09:00 IST — same convention as routers/energy.py
DAY_SHIFT_END_H   = 21   # 21:00 IST
BUCKET_MINUTES    = 30

FLOW_TOTALIZER_TAG_KEY = "flow_totalizer"

VALID_REPORT_GRANULARITIES = {"daily", "weekly", "monthly", "yearly"}
MAX_REPORT_PERIODS = 500  # same defensive-cap spirit as energy.py's 366-day range check


def _op_day_bounds_utc(op_date: date_type) -> tuple[datetime, datetime, datetime]:
    """Operational day (09:00 IST -> 09:00 IST next day) as UTC bounds, plus
    the 21:00 IST shift-A/B split.

    Returned as NAIVE datetimes (no tzinfo), deliberately — telemetry_data.timestamp
    is `TIMESTAMP WITHOUT TIME ZONE` (confirmed via \\d telemetry_data) storing
    UTC-naive values, and psycopg2 hands rows back as naive datetime objects.
    This file compares those row timestamps directly against these boundaries
    in Python (_ReadingSeries.at_or_before(), the is_today/bucket checks below)
    — mixing naive and tz-aware there raises "can't compare offset-naive and
    offset-aware datetimes". Unlike machine_state_event (TIMESTAMPTZ, so
    routers/machine_state.py's Python-side interval math is safely aware-vs-aware),
    telemetry_data has no tzinfo to be aware of, so naive-UTC throughout is the
    correct type to match here, not a workaround.
    """
    start_ist = datetime(op_date.year, op_date.month, op_date.day, DAY_SHIFT_START_H, 0, 0)
    mid_ist   = datetime(op_date.year, op_date.month, op_date.day, DAY_SHIFT_END_H, 0, 0)
    start_utc = start_ist - IST_OFFSET
    mid_utc   = mid_ist - IST_OFFSET
    end_utc   = start_utc + timedelta(hours=24)
    return start_utc, mid_utc, end_utc


class _ReadingSeries:
    """A sorted list of (timestamp, value) telemetry readings, supporting
    "nearest reading at or before T" lookups via bisect.

    Built from two simple queries rather than one query per boundary:
    one "carry-in" row (the last reading strictly before the day started,
    so bucket 0 / since_9am have something to diff against even if the very
    first reading of the day arrives mid-bucket) plus the full range of rows
    inside the day. Both are plain indexed range scans on
    (component_instance_id, tag_definition_id, timestamp) — no LATERAL join,
    no per-boundary round trip.
    """

    def __init__(self, rows: list[tuple[datetime, float]]):
        self._timestamps = [r[0] for r in rows]
        self._values     = [r[1] for r in rows]

    def at_or_before(self, t: datetime) -> float | None:
        idx = bisect.bisect_right(self._timestamps, t) - 1
        if idx < 0:
            return None
        return self._values[idx]


def _load_reading_series(
    db: Session, component_instance_id: int, tag_definition_id: int,
    day_start: datetime, day_end: datetime,
) -> _ReadingSeries:
    carry_in = db.execute(text("""
        SELECT timestamp, value_num
        FROM telemetry_data
        WHERE component_instance_id = :cid
          AND tag_definition_id     = :tag_id
          AND timestamp            <= :day_start
        ORDER BY timestamp DESC
        LIMIT 1
    """), {"cid": component_instance_id, "tag_id": tag_definition_id, "day_start": day_start}).fetchone()

    in_range = db.execute(text("""
        SELECT timestamp, value_num
        FROM telemetry_data
        WHERE component_instance_id = :cid
          AND tag_definition_id     = :tag_id
          AND timestamp             > :day_start
          AND timestamp            <= :day_end
        ORDER BY timestamp
    """), {"cid": component_instance_id, "tag_id": tag_definition_id, "day_start": day_start, "day_end": day_end}).fetchall()

    rows = [(carry_in.timestamp, float(carry_in.value_num))] if carry_in is not None else []
    rows += [(r.timestamp, float(r.value_num)) for r in in_range]
    return _ReadingSeries(rows)


def _get_machine(db: Session, company_id: int, machine_id: int):
    return db.execute(
        text("SELECT id, name FROM machine WHERE id = :machine_id AND company_id = :company_id"),
        {"machine_id": machine_id, "company_id": company_id},
    ).mappings().first()


def _get_flowmeter(db: Session, company_id: int, machine_id: int):
    """Resolve "the flowmeter on this machine" as whichever component
    instance produces the flow_totalizer tag — not a hardcoded
    component_type_id, so this works for any future machine with a
    flowmeter, not just Jet 11/Jet 12. Shared by every endpoint in this
    file that needs a machine's flowmeter (consumption, report, report PDF)."""
    return db.execute(text("""
        SELECT ci.id AS component_instance_id, td.id AS tag_definition_id
        FROM machine_component_instance ci
        JOIN component_type_tag ctt
          ON ctt.component_type_id = ci.component_type_id
         AND ctt.company_id        = ci.company_id
        JOIN tag_definition td
          ON td.id = ctt.tag_definition_id
        WHERE ci.machine_id  = :machine_id
          AND ci.company_id  = :company_id
          AND td.key         = :tag_key
        LIMIT 1
    """), {
        "machine_id": machine_id, "company_id": company_id, "tag_key": FLOW_TOTALIZER_TAG_KEY,
    }).mappings().first()


def _ist_9am_utc(d: date_type) -> datetime:
    """Naive-UTC instant for 09:00 IST on calendar date d — the single
    boundary every period type (day/week/month/year) below is anchored to,
    so periods always tile as a whole number of operational days with no
    gaps or overlaps. Same math as _op_day_bounds_utc()'s start_utc, factored
    out as a single boundary rather than a (start, mid, end) triple."""
    ist = datetime(d.year, d.month, d.day, DAY_SHIFT_START_H, 0, 0)
    return ist - IST_OFFSET


def _period_boundaries(granularity: str, start_date: date_type, end_date: date_type):
    """Yield (period_start_utc, period_end_utc) naive-UTC pairs covering
    [start_date, end_date] at the given granularity. Every boundary is a
    09:00 IST instant (_ist_9am_utc) so every period is a whole number of
    operational days, exactly like the daily view's own day_start/day_end.

    granularity:
        "daily"   — one operational day per period (same boundary as
            /water/consumption).
        "weekly"  — Monday-start ISO week, snapped back to the Monday
            on/before start_date. NEW CONVENTION — no prior precedent
            exists anywhere in this codebase (checked: only rolling
            trailing-7-day windows exist, e.g. OEECardsView's "This Week"
            toggle in App.jsx, which is not a calendar week).
        "monthly" — calendar month, 1st-of-month to 1st-of-next-month.
            NEW CONVENTION, same caveat.
        "yearly"  — calendar year, Jan 1 to Jan 1. NEW CONVENTION, same
            caveat.
    """
    if granularity == "daily":
        d = start_date
        while d <= end_date:
            yield _ist_9am_utc(d), _ist_9am_utc(d + timedelta(days=1))
            d += timedelta(days=1)

    elif granularity == "weekly":
        d = start_date - timedelta(days=start_date.weekday())  # snap back to Monday
        while d <= end_date:
            nxt = d + timedelta(days=7)
            yield _ist_9am_utc(d), _ist_9am_utc(nxt)
            d = nxt

    elif granularity == "monthly":
        y, m = start_date.year, start_date.month
        while date_type(y, m, 1) <= end_date:
            ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
            yield _ist_9am_utc(date_type(y, m, 1)), _ist_9am_utc(date_type(ny, nm, 1))
            y, m = ny, nm

    elif granularity == "yearly":
        y = start_date.year
        while date_type(y, 1, 1) <= end_date:
            yield _ist_9am_utc(date_type(y, 1, 1)), _ist_9am_utc(date_type(y + 1, 1, 1))
            y += 1

    else:
        raise ValueError(f"Unknown granularity: {granularity}")


def _window_total(
    series: _ReadingSeries, start: datetime, end: datetime, now_utc: datetime,
) -> WaterWindowTotal:
    # A fixed window (shift A/B, full day) isn't a real total until it's
    # actually elapsed — otherwise "nearest reading at/before a boundary
    # that's still in the future" silently resolves to whatever the latest
    # reading happens to be, producing a misleading "ok" total (often 0.0,
    # since start and end both land on that same latest reading) for a
    # window that hasn't started, or a partial number passed off as
    # complete for one that's only partway through. since_9am is exempt
    # from this: its caller already caps `end` at now_utc for a live/partial
    # total by design, so end > now_utc never happens for it.
    if end > now_utc:
        return WaterWindowTotal(window_start=start, window_end=end, liters=None, status="future")

    start_val = series.at_or_before(start)
    end_val   = series.at_or_before(end)
    if start_val is None or end_val is None:
        return WaterWindowTotal(window_start=start, window_end=end, liters=None, status="no_data")

    delta = end_val - start_val
    # A real totalizer never decreases — a negative delta means either a
    # torn/implausible read that slipped past the gateway's own plausibility
    # check, or a genuine meter reset (see the "KNOWN LIMITATION" comment in
    # gateway_service.py's EU1000 post-processing). We deliberately do not
    # try to distinguish the two here — not reliably possible from this
    # endpoint's data alone, and not worth the complexity until a reset is
    # actually observed in production. Flag it instead of showing a
    # nonsensical negative number or a falsely-zero one.
    if delta < 0:
        return WaterWindowTotal(window_start=start, window_end=end, liters=None, status="anomaly")

    return WaterWindowTotal(window_start=start, window_end=end, liters=delta, status="ok")


@router.get("/{machine_id}/water/consumption", response_model=WaterConsumptionResponse)
def get_machine_water_consumption(
    machine_id: int,
    date: str = Query(..., description="Operational day YYYY-MM-DD (IST)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    Water consumption for one machine's flowmeter over one operational day:
    four summary totals (since-9am, shift A, shift B, full day) plus a
    30-minute interval breakdown. See module docstring for the derivation.
    """
    company_id = current_user["company_id"]

    machine = _get_machine(db, company_id, machine_id)
    if machine is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    flowmeter = _get_flowmeter(db, company_id, machine_id)
    if flowmeter is None:
        raise HTTPException(404, f"Machine {machine_id} has no flowmeter component.")

    try:
        op_date = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    day_start, shift_split, day_end = _op_day_bounds_utc(op_date)
    now_utc = datetime.utcnow()  # naive UTC — matches telemetry_data.timestamp, see _op_day_bounds_utc()
    is_today = day_start <= now_utc < day_end

    series = _load_reading_series(
        db, flowmeter["component_instance_id"], flowmeter["tag_definition_id"],
        day_start, day_end,
    )

    since_9am_end = now_utc if is_today else day_end
    totals = WaterConsumptionTotals(
        since_9am=_window_total(series, day_start, since_9am_end, now_utc),
        shift_a=_window_total(series, day_start, shift_split, now_utc),
        shift_b=_window_total(series, shift_split, day_end, now_utc),
        full_day=_window_total(series, day_start, day_end, now_utc),
    )

    intervals: list[WaterIntervalBucket] = []
    bucket_delta = timedelta(minutes=BUCKET_MINUTES)
    n_buckets = int(timedelta(hours=24) / bucket_delta)
    for i in range(n_buckets):
        b_start = day_start + i * bucket_delta
        b_end   = b_start + bucket_delta

        if b_start >= now_utc:
            intervals.append(WaterIntervalBucket(
                bucket_start=b_start, bucket_end=b_end, liters=None, status="future",
            ))
            continue

        start_val = series.at_or_before(b_start)
        end_val   = series.at_or_before(b_end)
        if start_val is None or end_val is None:
            intervals.append(WaterIntervalBucket(
                bucket_start=b_start, bucket_end=b_end, liters=None, status="no_data",
            ))
            continue

        delta = end_val - start_val
        # See _window_total()'s comment — a negative delta is flagged, not
        # shown as a negative or clamped-to-zero number.
        if delta < 0:
            intervals.append(WaterIntervalBucket(
                bucket_start=b_start, bucket_end=b_end, liters=None, status="anomaly",
            ))
        else:
            intervals.append(WaterIntervalBucket(
                bucket_start=b_start, bucket_end=b_end, liters=delta, status="ok",
            ))

    return WaterConsumptionResponse(
        machine_id=machine_id,
        machine_name=machine["name"],
        date=date,
        totals=totals,
        intervals=intervals,
    )


def _build_water_report(
    db: Session, company_id: int, machine_id: int,
    granularity: str, start: str, end: str,
):
    """Shared computation for the JSON report endpoint and its PDF export.
    Returns (machine_name, boundaries, periods) or raises HTTPException.
    """
    if granularity not in VALID_REPORT_GRANULARITIES:
        raise HTTPException(
            400,
            f"Invalid granularity '{granularity}'. Use one of: {sorted(VALID_REPORT_GRANULARITIES)}.",
        )

    machine = _get_machine(db, company_id, machine_id)
    if machine is None:
        raise HTTPException(404, f"Machine {machine_id} not found.")

    flowmeter = _get_flowmeter(db, company_id, machine_id)
    if flowmeter is None:
        raise HTTPException(404, f"Machine {machine_id} has no flowmeter component.")

    try:
        start_date = date_type.fromisoformat(start)
        end_date   = date_type.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")
    if end_date < start_date:
        raise HTTPException(400, "'end' must be >= 'start'.")

    boundaries = list(_period_boundaries(granularity, start_date, end_date))
    if not boundaries:
        raise HTTPException(400, "No periods in the requested range.")
    if len(boundaries) > MAX_REPORT_PERIODS:
        raise HTTPException(
            400,
            f"Requested range produces {len(boundaries)} {granularity} periods, "
            f"exceeding the {MAX_REPORT_PERIODS}-period limit. Narrow the date range "
            f"or use a coarser granularity.",
        )

    overall_start = boundaries[0][0]
    overall_end   = boundaries[-1][1]
    now_utc = datetime.utcnow()

    series = _load_reading_series(
        db, flowmeter["component_instance_id"], flowmeter["tag_definition_id"],
        overall_start, overall_end,
    )

    periods = []
    for p_start, p_end in boundaries:
        w = _window_total(series, p_start, p_end, now_utc)
        periods.append(WaterReportPeriod(
            period_start=p_start, period_end=p_end, liters=w.liters, status=w.status,
        ))

    return machine["name"], periods


@router.get("/{machine_id}/water/report", response_model=WaterReportResponse)
def get_machine_water_report(
    machine_id: int,
    granularity: str = Query(..., description="daily | weekly | monthly | yearly"),
    start: str = Query(..., description="Range start date YYYY-MM-DD"),
    end: str = Query(..., description="Range end date YYYY-MM-DD (inclusive)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    Water consumption aggregated into daily/weekly/monthly/yearly periods
    over [start, end]. Reuses the exact same per-period computation as
    /water/consumption (_window_total, loaded once via a single
    _load_reading_series() call spanning the whole range rather than one
    query per period) — see module docstring and _period_boundaries()'s
    docstring for the boundary convention and status semantics.

    Unlike /water/consumption's since_9am, an in-progress period here is
    always "future", never a live/partial number — a report implies
    completed periods, and showing a partial "this month so far" total
    risks being misread as final.

    Early periods predating a since-fixed gateway bug (e.g. Jet 11 before
    2026-09-04's unit-mismatch fix) may correctly show "anomaly" — this is
    expected, not a bug in this endpoint: see gateway/gateway_service.py's
    _EUREKA_EU1000_RTU_SPEC comment and the Jet 11 backfill script.
    """
    company_id = current_user["company_id"]
    machine_name, periods = _build_water_report(db, company_id, machine_id, granularity, start, end)

    return WaterReportResponse(
        machine_id=machine_id,
        machine_name=machine_name,
        granularity=granularity,
        start=start,
        end=end,
        periods=periods,
    )


@router.get("/{machine_id}/water/report/pdf")
def get_machine_water_report_pdf(
    machine_id: int,
    granularity: str = Query(..., description="daily | weekly | monthly | yearly"),
    start: str = Query(..., description="Range start date YYYY-MM-DD"),
    end: str = Query(..., description="Range end date YYYY-MM-DD (inclusive)"),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """
    Same data as /water/report, rendered as a downloadable PDF — same
    reportlab structure as telemetry_read.py::get_machine_sensor_log_pdf and
    machine_state.py::get_machine_state_log_pdf (title/subtitle, summary
    table, striped period table).
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    company_id = current_user["company_id"]
    machine_name, periods = _build_water_report(db, company_id, machine_id, granularity, start, end)

    if not periods:
        raise HTTPException(404, "No periods in the requested range.")

    ok_periods = [p for p in periods if p.status == "ok"]

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
    story.append(Paragraph(f"Mevion — Water Consumption Report ({granularity.capitalize()})", title_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{machine_name} · {start} to {end}",
        subtitle_style
    ))
    story.append(Spacer(1, 16))

    # Summary stats — total liters and period counts by status
    total_liters = sum(p.liters for p in ok_periods)
    summary_data = [
        ["Periods", "Complete (ok)", "Total Liters", "Flagged (anomaly/no data)"],
        [
            str(len(periods)),
            str(len(ok_periods)),
            f"{total_liters:,.1f} L",
            str(len([p for p in periods if p.status in ("anomaly", "no_data")])),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[1.3*inch, 1.5*inch, 1.6*inch, 2.1*inch])
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

    # Main period table
    status_labels = {
        "ok": "",
        "future": "Not yet complete",
        "no_data": "No data",
        "anomaly": "Anomaly",
    }
    table_data = [["Period Start", "Period End", "Liters", "Status"]]
    for p in periods:
        liters_str = f"{p.liters:,.1f}" if p.status == "ok" else "—"
        table_data.append([
            p.period_start.strftime("%d %b %Y"),
            p.period_end.strftime("%d %b %Y"),
            liters_str,
            status_labels.get(p.status, p.status),
        ])

    period_table = Table(table_data, colWidths=[1.6*inch, 1.6*inch, 1.3*inch, 2*inch])
    period_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#dc2626')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(period_table)

    doc.build(story)
    buffer.seek(0)

    machine_slug = machine_name.lower().replace(" ", "-")
    filename = f"mevion-{machine_slug}-water-report-{granularity}-{start}-{end}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
