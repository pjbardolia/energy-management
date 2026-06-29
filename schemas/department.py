from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    # min_length=1: empty string is not a valid department name.
    name: str = Field(..., min_length=1)
    description: str
    company_id: int


class DepartmentResponse(BaseModel):
    id: int
    name: str
    description: str
    company_id: int

    class Config:
        from_attributes = True
