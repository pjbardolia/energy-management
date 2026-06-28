from pydantic import BaseModel


class MachineComponentCreate(BaseModel):
    name: str
    component_type: str
    machine_id: int


class MachineComponentResponse(BaseModel):
    id: int
    name: str
    component_type: str
    machine_id: int

    class Config:
        from_attributes = True
