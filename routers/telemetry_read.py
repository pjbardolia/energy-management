# Read-oriented telemetry endpoints for the dashboard frontend.
#
# Three endpoints live here:
#
#   GET /machines/live
#       Most-recent readings for every machine, pivoted by tag_name into a
#       tags dict.  Ready for the fleet dashboard to render without further
#       client-side aggregation.  Used to replace buildFleet() in Phase 5c.
#
#   GET /machines/{machine_id}/live
#       Same pivot for a single machine.  Used by the detail page header.
#
#   GET /fleet/summary
#       Derived from the same DISTINCT ON query as /machines/live.
#       Returns total/running/stopped machine counts and total power in kW.
#       Used to replace the KPI bar in FleetDashboard in Phase 5c.
#
#   GET /machines/{machine_id}/history
#       All seven tag values per time bucket for one machine over a requested
#       window (1–24 hours).  Uses TimescaleDB time_bucket() with conditional
#       aggregation — one row per time step.
#       Used to replace buildHistory() in Phase 5c.
#
# Write endpoint (POST /data) stays in data_router.py — not touched here.

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
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

    Uses PostgreSQL DISTINCT ON with three JOINs to attach machine_name and
    tag_name to each row so callers don't need additional queries.

    The query is fully parameterised — company_id is bound, never interpolated.

    Returns a list of RowMapping objects; each field is accessible by name
    (e.g. row.machine_name, row.value_num).
    """
    sql = text("""
        SELECT DISTINCT ON (td.component_instance_id, td.tag_definition_id)
            td.component_instance_id,
            td.tag_definition_id,
            td.value_num,
            td.value_text,
            td.timestamp,
            m.id          AS machine_id,
            m.name        AS machine_name,
            tdef.name     AS tag_name
        FROM telemetry_data td
        JOIN machine_component_instance mci
          ON mci.id = td.component_instance_id
        JOIN machine m
          ON m.id = mci.machine_id
        JOIN tag_definition tdef
          ON tdef.id = td.tag_definition_id
        WHERE td.company_id = :company_id
        ORDER BY
            td.component_instance_id,
            td.tag_definition_id,
            td.timestamp DESC
    """)

    result = db.execute(sql, {"company_id": company_id})

    # mappings() returns each row as a dict-like RowMapping so field access
    # works by name (row.machine_name) rather than positional index.
    return result.mappings().all()


def _pivot_rows(rows) -> list[dict]:
    """Group flat tag rows by machine_id, pivot tag_name → value_num into tags dict.

    Input: list of RowMapping objects from _get_latest_rows() — one row per
           (component, tag) combination, with machine_id and tag_name attached.
    Output: one dict per machine with all its latest tag values in a 'tags' sub-dict.
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
        machines[mid]["tags"][row["tag_name"]] = row["value_num"]
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
    """Return the most-recent readings for every machine, pivoted by tag name.

    Each machine appears once; all its latest tag values are collapsed into a
    single tags dict keyed by tag_name (e.g. {"frequency": 30.5, "power": 22.1}).
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
    """Return the most-recent readings for a single machine, pivoted by tag name.

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
    - running        : machines that have a frequency row (tag_id 6) with value_num > 0
    - stopped        : total_machines - running
    - total_power_kw : sum of value_num across all power rows (tag_id 7)
    - last_updated   : max timestamp across all rows

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

        tag_id = row["tag_definition_id"]

        # tag_definition_id 6 = frequency (Hz)
        if tag_id == 6 and row["value_num"] is not None:
            machine_id = row["machine_id"]
            existing = freq_by_machine.get(machine_id, 0.0)
            freq_by_machine[machine_id] = max(existing, row["value_num"])

        # tag_definition_id 7 = power (kW)
        if tag_id == 7 and row["value_num"] is not None:
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

    All seven tag values are returned per time bucket in a single query using
    conditional aggregation — one row per time step instead of seven calls.

    Path param:
        machine_id — the machine.id to query.

    Query params:
        hours  — window length in hours (1–24, default 1).

    Ownership check:
        machine_component_instance is queried for a row matching both machine_id
        and company_id, resolving to the component_instance_id used in telemetry.
        A valid machine_id belonging to a different tenant returns 404.

    Phase 5c: replace buildHistory() in App.jsx with a fetch to this endpoint.
    """
    # --- Resolve machine_id → component_instance_id (ownership check included) ---
    # company_id filter means a valid machine_id from another tenant returns 404,
    # not leaking the fact that the ID exists.
    row = db.execute(
        text(
            "SELECT id FROM machine_component_instance "
            "WHERE machine_id = :machine_id AND company_id = :company_id"
        ),
        {"machine_id": machine_id, "company_id": current_user["company_id"]},
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Machine {} not found.".format(machine_id),
        )

    cid = row[0]   # the component_instance_id stored in telemetry_data

    # --- Time window and bucket size ---
    since = datetime.utcnow() - timedelta(hours=hours)
    interval = _bucket_interval(hours)

    # --- Conditional aggregation query ---
    # time_bucket() is a TimescaleDB function that rounds a timestamp down to
    # the nearest bucket boundary (e.g. 10:03:47 → 10:03:00 for 1-minute buckets).
    # CASE WHEN ... END inside AVG() selects only the relevant rows for each tag;
    # rows for other tags contribute NULL which AVG() ignores automatically.
    #
    # The interval string is chosen from a fixed set (_bucket_interval) — it is
    # not user-supplied so embedding it in the SQL text is safe.
    sql = text("""
        SELECT
            time_bucket('{interval}', timestamp) AS bucket,
            AVG(CASE WHEN tag_definition_id = 6 THEN value_num END) AS frequency,
            AVG(CASE WHEN tag_definition_id = 3 THEN value_num END) AS current,
            AVG(CASE WHEN tag_definition_id = 7 THEN value_num END) AS power,
            AVG(CASE WHEN tag_definition_id = 1 THEN value_num END) AS rpm,
            AVG(CASE WHEN tag_definition_id = 2 THEN value_num END) AS torque,
            AVG(CASE WHEN tag_definition_id = 5 THEN value_num END) AS output_voltage,
            AVG(CASE WHEN tag_definition_id = 4 THEN value_num END) AS dc_voltage
        FROM telemetry_data
        WHERE component_instance_id = :cid
          AND company_id             = :company_id
          AND timestamp             >= :since
        GROUP BY bucket
        ORDER BY bucket ASC
    """.format(interval=interval))

    rows = db.execute(sql, {
        "cid":        cid,
        "company_id": current_user["company_id"],
        "since":      since,
    }).mappings().all()

    # Map each RowMapping to the bucket schema
    buckets = [HistoryBucketResponse(**dict(row)) for row in rows]

    return MachineHistoryResponse(
        machine_id=machine_id,
        hours=hours,
        data=buckets,
    )
