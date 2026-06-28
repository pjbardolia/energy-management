from typing import Optional
from pydantic import BaseModel

# Import the TagDataType enum from the model layer.
# Sharing one enum definition prevents the model and API from drifting apart —
# if a new data type is added to the enum, both layers update automatically.
from models.tag_definition import TagDataType


class TagDefinitionCreate(BaseModel):
    # Human-readable measurement name, e.g. "Output Frequency", "DC Bus Voltage"
    name: str

    # SI or display unit, e.g. "Hz", "V", "A", "kW" — None is fine for text-type tags
    unit: Optional[str] = None

    # Optional longer description of what this measurement represents
    description: Optional[str] = None

    # Declares which value column to use and how to render the reading.
    # float / int / bool → TelemetryData.value_num
    # text              → TelemetryData.value_text
    data_type: TagDataType

    # Which company's catalogue this tag belongs to (multi-tenant)
    company_id: int


class TagDefinitionResponse(BaseModel):
    id: int
    name: str
    unit: Optional[str] = None
    description: Optional[str] = None
    data_type: TagDataType
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly
