from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DataCreate(BaseModel):
    # When the reading was taken (ISO-8601 datetime, e.g. "2026-06-28T10:30:00")
    timestamp: datetime

    # ID of the machine_component_instance that produced this reading
    component_instance_id: int

    # ID of the tag_definition that describes what was measured
    # (the tag's data_type field tells you which value column to populate)
    tag_definition_id: int

    # Numeric value — populate when tag_definition.data_type is 'float', 'int', or 'bool'.
    # bool readings: 1.0 = True / On,  0.0 = False / Off.
    # Leave as None (null) when data_type is 'text'.
    value_num: Optional[float] = None

    # Text value — populate when tag_definition.data_type is 'text'.
    # Examples: fault codes ("E-021", "OVERTEMP"), status strings, batch/lot IDs.
    # Leave as None (null) when data_type is 'float', 'int', or 'bool'.
    value_text: Optional[str] = None

    # Which company this reading belongs to (multi-tenant isolation)
    company_id: int


class DataResponse(BaseModel):
    id: int
    timestamp: datetime
    component_instance_id: int
    tag_definition_id: int
    value_num: Optional[float] = None
    value_text: Optional[str] = None
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly


class TelemetryBatchItem(BaseModel):
    # Mirror DataCreate exactly, minus company_id — the server reads it from the JWT.
    timestamp: datetime
    component_instance_id: int
    tag_definition_id: int
    value_num: Optional[float] = None
    value_text: Optional[str] = None


class TelemetryBatchRequest(BaseModel):
    # min_length=1 → Pydantic returns 422 on an empty array before the endpoint runs.
    # max_length=500 caps payload size so a malformed client can't send 10 MB.
    readings: list[TelemetryBatchItem] = Field(..., min_length=1, max_length=500)


class TelemetryBatchResponse(BaseModel):
    accepted: int
    rejected: int
    errors: list[str] = []
