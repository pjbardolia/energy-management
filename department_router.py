# Router for Department CRUD endpoints.
#
# A Department represents a physical area or functional group within a factory
# (e.g. "Dyeing", "Finishing", "Utilities").  Machines are assigned to
# departments at the Machine level, not at the MachineType level.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from schemas.department import DepartmentCreate, DepartmentResponse
from models import Department


router = APIRouter()


@router.post("/departments", response_model=DepartmentResponse, status_code=201)
def create_department(department: DepartmentCreate, db: Session = Depends(get_db)):
    db_department = Department(
        name=department.name,
        description=department.description,
        company_id=department.company_id
    )

    db.add(db_department)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning so the poisoned transaction does not
        # break the next operation on this session.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create department — check that company_id exists."
        )

    db.refresh(db_department)
    return db_department


@router.get("/departments", response_model=list[DepartmentResponse])
def get_departments(db: Session = Depends(get_db)):
    return db.query(Department).all()
