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
from schemas.telemetry import DataCreate, DataResponse
from models import TelemetryData


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
