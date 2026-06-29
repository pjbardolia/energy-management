# Router for MachineType CRUD endpoints.
#
# MachineType is a catalogue entry that answers "what kind of machine is this?"
# e.g. "Stenter", "Jigger", "Compressor".  It is scoped per company so each
# tenant maintains their own catalogue.  Physical machines reference a
# MachineType via Machine.machine_type_id.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from schemas.machine_type import MachineTypeCreate, MachineTypeResponse
from models import MachineType


router = APIRouter()


@router.post("/machine-types", response_model=MachineTypeResponse, status_code=201)
def create_machine_type(machine_type: MachineTypeCreate, db: Session = Depends(get_db)):
    db_machine_type = MachineType(
        name=machine_type.name,
        description=machine_type.description,
        company_id=machine_type.company_id
    )

    db.add(db_machine_type)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning — clears the failed transaction so the
        # session remains usable for future requests.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create machine type — check that company_id exists."
        )

    db.refresh(db_machine_type)
    return db_machine_type


@router.get("/machine-types", response_model=list[MachineTypeResponse])
def get_machine_types(db: Session = Depends(get_db)):
    # Returns every machine type across all companies.
    # Phase 3 will add ?company_id= filtering.
    return db.query(MachineType).all()
