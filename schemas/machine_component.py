from pydantic import BaseModel, Field


class MachineComponentCreate(BaseModel):
    # Human name for this physical component, e.g. "Circulation Pump 1"
    # min_length=1: reject empty string — a blank component name is never valid.
    name: str = Field(..., min_length=1)

    # ID of the component_type row — replaces the old free-text 'component_type' string.
    # Caller must create a ComponentType first (Phase 2 endpoint) and pass its ID.
    component_type_id: int

    # ID of the machine this component is physically attached to
    machine_id: int

    # Which company owns this component (multi-tenant, ADR Decision 4)
    company_id: int


class MachineComponentResponse(BaseModel):
    id: int
    name: str
    component_type_id: int
    machine_id: int
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly
