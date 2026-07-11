from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LatestReadingResponse(BaseModel):
    # Which physical component produced this reading
    component_instance_id: int

    # Which measurement type this reading belongs to
    tag_definition_id: int

    # Machine that owns this component — populated by JOIN to machine table
    machine_id: int
    machine_name: str

    # Human-readable tag name — populated by JOIN to tag_definition table
    tag_name: str

    # Numeric value for float / int / bool tags; None for text tags
    value_num: Optional[float] = None

    # Text value for fault codes / status strings; None for numeric tags
    value_text: Optional[str] = None

    # When this reading was recorded
    timestamp: datetime

    class Config:
        # Allow construction from SQLAlchemy RowMapping as well as ORM objects
        from_attributes = True


class FleetSummaryResponse(BaseModel):
    # Total number of distinct machines that have sent telemetry
    total_machines: int

    # Machines where the most-recent frequency reading (tag_id=6) is > 0
    running: int

    # total_machines - running
    stopped: int

    # Sum of the most-recent power reading (tag_id=7) across all components
    total_power_kw: float

    # Timestamp of the most recent reading across the entire fleet
    last_updated: datetime


class MachineTagsResponse(BaseModel):
    machine_id: int
    machine_name: str
    component_instance_id: int
    last_updated: datetime
    tags: dict[str, Optional[float]]   # e.g. {"frequency": 30.5, "current": 6.2, ...}

    class Config:
        from_attributes = True


class HistoryBucketResponse(BaseModel):
    # Time bucket start — interval size depends on the requested window
    # (1 min for ≤1 h, 5 min for ≤6 h, 15 min for ≤24 h)
    bucket: datetime

    # All seven VFD tag values as averages within the bucket.
    # None when no reading of that type arrived in the bucket period.
    frequency:      Optional[float] = None   # tag_definition_id = 6  Hz
    current:        Optional[float] = None   # tag_definition_id = 3  A
    power:          Optional[float] = None   # tag_definition_id = 7  kW
    rpm:            Optional[float] = None   # tag_definition_id = 1  RPM
    torque:         Optional[float] = None   # tag_definition_id = 2  %
    output_voltage: Optional[float] = None   # tag_definition_id = 5  V
    dc_voltage:     Optional[float] = None   # tag_definition_id = 4  V

    class Config:
        from_attributes = True


class MachineHistoryResponse(BaseModel):
    # The machine whose history this is
    machine_id: int

    # The hours window that was requested (echoed back for the client)
    hours: int

    # Time-ordered list of bucketed readings
    data: list[HistoryBucketResponse]
