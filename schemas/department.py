from pydantic import BaseModel


class DepartmentCreate(BaseModel):
    name: str
    description: str
    company_id: int


class DepartmentResponse(BaseModel):
    id: int
    name: str
    description: str
    company_id: int

    class Config:
        from_attributes = True
