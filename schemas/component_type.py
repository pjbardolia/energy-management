from typing import Optional
from pydantic import BaseModel


class ComponentTypeCreate(BaseModel):
    # e.g. "VFD", "Motor", "Circulation Pump", "Temperature Sensor"
    name: str

    # Optional description of what this component type does
    description: Optional[str] = None

    # Which company's catalogue this type belongs to (multi-tenant)
    company_id: int


class ComponentTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly
