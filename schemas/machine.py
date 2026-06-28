from typing import Optional
from pydantic import BaseModel


class MachineCreate(BaseModel):
    # Human name for this physical machine, e.g. "Stenter Line 1"
    name: str

    # Optional description, e.g. "10-chamber gas stenter, commissioned 2019"
    description: Optional[str] = None

    # ID of the machine_type row — replaces the old free-text 'machine_type' string.
    # The caller must create a MachineType first (Phase 2 endpoint) and pass its ID.
    machine_type_id: int

    # ID of the department this machine physically lives in (ADR Decision 3).
    # "What kind of machine" (machine_type_id) and "where it lives" (department_id)
    # are now independent — a Pump can belong to Cooling, Utilities, or Reaction.
    department_id: int

    # Which company owns this machine (multi-tenant, ADR Decision 4)
    company_id: int


class MachineResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    machine_type_id: int
    department_id: int
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly
