# Router for Machine CRUD endpoints.
#
# A Machine represents one physical machine on a factory floor.
# It belongs to a MachineType (what kind of machine) and a Department
# (where it physically lives).  Both are FK references — callers must
# create those rows first and pass their IDs here.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from schemas.machine import MachineCreate, MachineResponse
from models import Machine


router = APIRouter()


@router.post("/machines", response_model=MachineResponse, status_code=201)
def create_machine(machine: MachineCreate, db: Session = Depends(get_db)):
    # Build the ORM row from the validated request body.
    # machine_type_id is a FK to machine_type.id — the model no longer
    # accepts a free-text 'machine_type' string (that caused the 500 error).
    db_machine = Machine(
        name=machine.name,
        machine_type_id=machine.machine_type_id,  # FK, not a string field
        description=machine.description,
        company_id=machine.company_id,
        department_id=machine.department_id
    )

    db.add(db_machine)

    try:
        db.commit()
    except IntegrityError:
        # Roll back FIRST so the poisoned transaction does not block the
        # next request that reuses this session from the pool.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not create machine — check that company_id, "
                "department_id, and machine_type_id all exist."
            )
        )

    db.refresh(db_machine)
    return db_machine


@router.get("/machines", response_model=list[MachineResponse])
def get_machines(db: Session = Depends(get_db)):
    # Returns every machine row visible in this database.
    # Phase 3 will add ?company_id= filtering for multi-tenant scoping.
    return db.query(Machine).all()
