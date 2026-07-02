# Router for Department CRUD endpoints.
#
# A Department represents a physical area or functional group within a factory
# (e.g. "Dyeing", "Finishing", "Utilities").  Machines are assigned to
# departments at the Machine level, not at the MachineType level.
#
# Phase 4d changes:
#   - All endpoints now require a valid JWT (get_tenant_db enforces this).
#   - GET /departments filters rows by the authenticated user's company_id
#     so tenants cannot see each other's departments.
#   - get_tenant_db() also sets the PostgreSQL session variable
#     app.current_company_id which activates the RLS policy from migration 003.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, get_tenant_db
from schemas.department import DepartmentCreate, DepartmentResponse
from models import Department


router = APIRouter()


@router.post("/departments", response_model=DepartmentResponse, status_code=201)
def create_department(
    department: DepartmentCreate,
    db: Session = Depends(get_tenant_db),
):
    # company_id still comes from the request body (not the JWT) so the API
    # shape is unchanged for Phase 4d.  Phase 5 will derive it from the JWT.
    db_department = Department(
        name=department.name,
        description=department.description,
        company_id=department.company_id,
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
            detail="Could not create department — check that company_id exists.",
        )

    db.refresh(db_department)
    return db_department


@router.get("/departments", response_model=list[DepartmentResponse])
def get_departments(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # WHERE filter enforces tenant isolation at the application layer.
    # The RLS policy on the department table provides a second layer of
    # protection for non-superuser roles (Phase 5).
    return (
        db.query(Department)
        .filter(Department.company_id == current_user["company_id"])
        .all()
    )
