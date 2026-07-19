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
    tag_key to each row so callers don't need additional queries.

    tag_key is the stable slug from tag_definition.key ("frequency", "power",
    …) — not the human-editable display name.  Callers use it as the key in
    the tags dict so frontend/gateway contracts are unaffected by name changes.

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
            tdef.key      AS tag_key
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
        machine_component_instance is queried for a row matching both machine_id
        and company_id.  A valid machine_id belonging to a different tenant
        returns 404, not leaking the fact that the ID exists.

    Phase 5c: replace buildHistory() in App.jsx with a fetch to this endpoint.
    """
    # --- Resolve machine_id → component_instance_id (ownership check included) ---
    ci_row = db.execute(
        text(
            "SELECT id FROM machine_component_instance "
            "WHERE machine_id = :machine_id AND company_id = :company_id"
        ),
        {"machine_id": machine_id, "company_id": current_user["company_id"]},
    ).fetchone()

    if ci_row is None:
        raise HTTPException(
            status_code=404,
            detail="Machine {} not found.".format(machine_id),
        )

    cid = ci_row[0]   # the component_instance_id stored in telemetry_data

    # --- Time window and bucket size ---
    since = datetime.utcnow() - timedelta(hours=hours)
    interval = _bucket_interval(hours)

    # --- Long-form query: one row per (bucket, tag_key) ---
    # JOIN to tag_definition gives the slug key so results are keyed by contract
    # slug ("frequency", "power", …) rather than integer tag_definition_id.
    # The interval string is chosen from a fixed lookup — not user-supplied —
    # so embedding it directly in the SQL text is safe.
    sql = text("""
        SELECT
            time_bucket('{interval}', td.timestamp) AS bucket,
            tdef.key                                AS tag_key,
            AVG(td.value_num)                       AS avg_value
        FROM telemetry_data td
        JOIN tag_definition tdef
          ON tdef.id = td.tag_definition_id
        WHERE td.component_instance_id = :cid
          AND td.company_id             = :company_id
          AND td.timestamp             >= :since
        GROUP BY bucket, tdef.key
        ORDER BY bucket ASC, tdef.key
    """.format(interval=interval))

    rows = db.execute(sql, {
        "cid":        cid,
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
# GET /sensors/temperature/current, /sensors/temperature/history
#
# The Electrosil Fx-438 dyebath temperature sensor is a standalone sensor
# device, not a VFD-driven machine component — Jet 27 (machine_id=14) now has
# TWO component instances (Reel Motor id=15, Temp Sensor id=29), so the
# existing /machines/{id}/history endpoint (which assumes one component per
# machine and grabs the first match via fetchone()) can't be reused cleanly.
# These endpoints go straight to component_instance_id=29 / tag_definition_id=8
# instead — hardcoded because this is currently the only such sensor.
# ---------------------------------------------------------------------------

_TEMPERATURE_TAG_ID       = 8
_TEMPERATURE_COMPONENT_ID = 29


@router.get("/sensors/temperature/current")
def get_temperature_current(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Latest temperature reading from the Electrosil Fx-438 dyebath sensor."""
    company_id = current_user["company_id"]
    row = db.execute(text("""
        SELECT td.value_num, td.timestamp
        FROM telemetry_data td
        WHERE td.tag_definition_id     = :tag_id
          AND td.company_id            = :company_id
          AND td.component_instance_id = :component_id
        ORDER BY td.timestamp DESC
        LIMIT 1
    """), {
        "tag_id":       _TEMPERATURE_TAG_ID,
        "company_id":   company_id,
        "component_id": _TEMPERATURE_COMPONENT_ID,
    }).mappings().first()

    if not row:
        return {"value": None, "timestamp": None}
    return {"value": float(row["value_num"]), "timestamp": row["timestamp"].isoformat()}


@router.get("/sensors/temperature/history")
def get_temperature_history(
    hours: int = Query(default=1, ge=1, le=168),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    """Temperature history for the last N hours (1–168) from the dyebath sensor."""
    company_id = current_user["company_id"]
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = db.execute(text("""
        SELECT td.value_num, td.timestamp
        FROM telemetry_data td
        WHERE td.tag_definition_id     = :tag_id
          AND td.company_id            = :company_id
          AND td.component_instance_id = :component_id
          AND td.timestamp             >= :since
        ORDER BY td.timestamp ASC
    """), {
        "tag_id":       _TEMPERATURE_TAG_ID,
        "company_id":   company_id,
        "component_id": _TEMPERATURE_COMPONENT_ID,
        "since":        since,
    }).mappings().all()

    return [
        {"value": float(r["value_num"]), "timestamp": r["timestamp"].isoformat()}
        for r in rows
    ]
