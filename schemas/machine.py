from pydantic import BaseModel


class MachineCreate(BaseModel):
    name: str
    machine_type: str
    description: str
    company_id: int
    department_id: int


class MachineResponse(BaseModel):
    id: int
    name: str
    machine_type: str
    description: str
    company_id: int
    department_id: int

    class Config:
        from_attributes = True
