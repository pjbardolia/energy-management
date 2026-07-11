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

    # Machines where the most-recent "frequency" tag reading is > 0
    running: int

    # total_machines - running
    stopped: int

    # Sum of the most-recent "power" tag reading across all components
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

    # All available tag values averaged within the bucket, keyed by tag slug.
    # Symmetric with the live endpoint's tags dict — frontend renders both the
    # same way.  Tags with no readings in the bucket are omitted from the dict.
    # Example: {"frequency": 30.5, "power": 22.1, "current": 6.2}
    tags: dict[str, Optional[float]]

    class Config:
        from_attributes = True


class MachineHistoryResponse(BaseModel):
    # The machine whose history this is
    machine_id: int

    # The hours window that was requested (echoed back for the client)
    hours: int

    # Time-ordered list of bucketed readings
    data: list[HistoryBucketResponse]
