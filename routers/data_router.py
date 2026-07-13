# Router for TelemetryData CRUD endpoints.
#
# A TelemetryData row is one sensor reading: which component produced it,
# which measurement tag it belongs to, when it was taken, and the value.
# The value is stored in either value_num (float/int/bool tags) or
# value_text (text tags) — use the tag_definition.data_type to decide which.
#
# Phase 4d changes:
#   - All endpoints now require a valid JWT (get_tenant_db enforces this).
#   - GET /data filters by company_id (stored directly on TelemetryData rows).

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, get_tenant_db
from schemas.telemetry import (
    DataCreate, DataResponse,
    TelemetryBatchRequest, TelemetryBatchResponse,
)
from models import TelemetryData, MachineComponentInstance


router = APIRouter()


@router.post("/data", response_model=DataResponse, status_code=201)
def create_data(
    data: DataCreate,
    db: Session = Depends(get_tenant_db),
):
    # Build the ORM row using the normalized, generic schema.
    # The old wide-column approach (output_frequency, dc_bus_voltage, etc.)
    # was replaced in Phase 1 — each reading now stores exactly one value
    # under a specific tag_definition_id.
    db_data = TelemetryData(
        timestamp=data.timestamp,
        component_instance_id=data.component_instance_id,
        tag_definition_id=data.tag_definition_id,
        value_num=data.value_num,
        value_text=data.value_text,
        company_id=data.company_id,
    )

    db.add(db_data)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning so the session stays clean.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not create telemetry record — check that company_id, "
                "component_instance_id, and tag_definition_id all exist."
            ),
        )

    # db.refresh() is intentionally omitted here.  TimescaleDB hypertables
    # reroute every row to an internal chunk table at commit time, which
    # breaks SQLAlchemy's standard post-commit SELECT-by-PK that refresh()
    # uses.  All column values were set explicitly before the INSERT, and
    # SQLAlchemy populates the auto-generated `id` via the INSERT RETURNING
    # clause, so the instance already holds the complete row for the response.
    return db_data


@router.post("/data/batch", response_model=TelemetryBatchResponse, status_code=202)
def create_data_batch(
    batch: TelemetryBatchRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    company_id = current_user["company_id"]

    # ── Tenant check — one query for the entire batch ───────────────────────
    # Collect the distinct component IDs, resolve their owners in a single
    # SELECT, then reject the whole request if any belongs to another tenant.
    # company_id is always sourced from the JWT — never from the request body.
    batch_cids = {item.component_instance_id for item in batch.readings}
    owned_rows = db.query(
        MachineComponentInstance.id, MachineComponentInstance.company_id
    ).filter(MachineComponentInstance.id.in_(batch_cids)).all()

    owned_map = {row.id: row.company_id for row in owned_rows}
    errors = []
    for cid in batch_cids:
        if cid not in owned_map:
            errors.append("component_instance_id {} not found".format(cid))
        elif owned_map[cid] != company_id:
            # Immediately 403 — do not reveal which IDs exist in another tenant.
            raise HTTPException(
                status_code=403,
                detail="component_instance_id {} belongs to another tenant".format(cid),
            )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors[:10]})

    # ── Single transaction, single commit ───────────────────────────────────
    # Build all ORM objects, then flush in one shot.
    # db.refresh() is intentionally omitted — TimescaleDB's composite PK
    # (id, timestamp) breaks the SELECT-by-PK that refresh() issues internally.
    db_rows = [
        TelemetryData(
            timestamp=item.timestamp,
            component_instance_id=item.component_instance_id,
            tag_definition_id=item.tag_definition_id,
            value_num=item.value_num,
            value_text=item.value_text,
            company_id=company_id,   # from the token, never the body
        )
        for item in batch.readings
    ]
    db.add_all(db_rows)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Batch insert failed — check component_instance_id and tag_definition_id values.",
        )

    return TelemetryBatchResponse(accepted=len(db_rows), rejected=0)


@router.get("/data", response_model=list[DataResponse])
def get_data(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # company_id is stored directly on TelemetryData (it was kept even after
    # FK constraints were dropped for TimescaleDB compatibility in Phase 4c).
    # Filtering here avoids a cross-table join and matches the RLS policy.
    return (
        db.query(TelemetryData)
        .filter(TelemetryData.company_id == current_user["company_id"])
        .all()
    )
