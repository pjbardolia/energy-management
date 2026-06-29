# Router for MachineComponentInstance CRUD endpoints.
#
# A MachineComponentInstance is one physical component (motor, sensor, VFD…)
# attached to a specific machine.  It belongs to a ComponentType (what kind
# of component) and a Machine (which machine it is attached to).

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from schemas.machine_component import (
    MachineComponentCreate,
    MachineComponentResponse
)
from models import MachineComponentInstance


router = APIRouter()


@router.post("/machine-components", response_model=MachineComponentResponse, status_code=201)
def create_machine_component(component: MachineComponentCreate, db: Session = Depends(get_db)):
    # Build the ORM row.
    # component_type_id is a FK to component_type.id — the model no longer
    # accepts a free-text 'component_type' string (that caused the 500 error).
    db_component = MachineComponentInstance(
        name=component.name,
        component_type_id=component.component_type_id,  # FK, not a string field
        machine_id=component.machine_id,
        company_id=component.company_id
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
            )
        )

    db.refresh(db_component)
    return db_component


@router.get("/machine-components", response_model=list[MachineComponentResponse])
def get_machine_components(db: Session = Depends(get_db)):
    # Returns every component instance row.
    # Phase 3 will add ?company_id= filtering.
    return db.query(MachineComponentInstance).all()
