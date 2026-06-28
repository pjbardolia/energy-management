from datetime import datetime

from pydantic import BaseModel


class DataCreate(BaseModel):
    timestamp: datetime
    machine_id: int
    component_id: int
    output_frequency: float
    reference_frequency: float
    dc_bus_voltage: float
    output_voltage: float
    output_current: float
    rotation_speed: float
    output_power: float
    output_torque: float
    temperature: float
    pressure: float


class DataResponse(BaseModel):
    id: int
    timestamp: datetime
    machine_id: int
    component_id: int
    output_frequency: float
    reference_frequency: float
    dc_bus_voltage: float
    output_voltage: float
    output_current: float
    rotation_speed: float
    output_power: float
    output_torque: float
    temperature: float
    pressure: float

    class Config:
        from_attributes = True
