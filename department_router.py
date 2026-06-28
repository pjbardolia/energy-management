from fastapi import APIRouter
from sqlalchemy.orm import Session

from database import SessionLocal
from schemas.department import DepartmentCreate, DepartmentResponse
from models import Department


router = APIRouter()


@router.post("/departments", response_model=DepartmentResponse)
def create_department(department: DepartmentCreate):

    db: Session = SessionLocal()

    db_department = Department(
        name=department.name,
        description=department.description,
        company_id=department.company_id
    )

    db.add(db_department)
    db.commit()
    db.refresh(db_department)

    return db_department


@router.get("/departments")
def get_departments():

    db: Session = SessionLocal()

    departments = db.query(Department).all()

    return departments
