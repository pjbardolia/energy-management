from typing import Optional
from pydantic import BaseModel


class MachineTypeCreate(BaseModel):
    # e.g. "Stenter", "Jigger", "Circulation Pump", "Compressor"
    name: str

    # Optional description of what this machine type does
    description: Optional[str] = None

    # Which company's catalogue this type belongs to (multi-tenant)
    company_id: int


class MachineTypeResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly
