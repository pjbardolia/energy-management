# Router for MachineComponentInstance CRUD endpoints.
#
# A MachineComponentInstance is one physical component (motor, sensor, VFD…)
# attached to a specific machine.  It belongs to a ComponentType (what kind
# of component) and a Machine (which machine it is attached to).
#
# Phase 4d changes:
#   - All endpoints now require a valid JWT (get_tenant_db enforces this).
#   - GET /machine-components filters by company_id (stored directly on the
#     MachineComponentInstance row, matching the machine's company).

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, get_tenant_db
from schemas.machine_component import MachineComponentCreate, MachineComponentResponse
from models import MachineComponentInstance


router = APIRouter()


@router.post("/machine-components", response_model=MachineComponentResponse, status_code=201)
def create_machine_component(
    component: MachineComponentCreate,
    db: Session = Depends(get_tenant_db),
):
    # Build the ORM row.
    # component_type_id is a FK to component_type.id — the model no longer
    # accepts a free-text 'component_type' string (that caused the 500 error).
    db_component = MachineComponentInstance(
        name=component.name,
        component_type_id=component.component_type_id,  # FK, not a string field
        machine_id=component.machine_id,
        company_id=component.company_id,
    )

    db.add(db_component)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning so the session is clean for the next request.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not create machine component — check that company_id, "
                "machine_id, and component_type_id all exist."
            ),
        )

    db.refresh(db_component)
    return db_component


@router.get("/machine-components", response_model=list[MachineComponentResponse])
def get_machine_components(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # company_id is stored directly on MachineComponentInstance so we can
    # filter without a join.  This matches the column populated at INSERT time.
    return (
        db.query(MachineComponentInstance)
        .filter(MachineComponentInstance.company_id == current_user["company_id"])
        .all()
    )
