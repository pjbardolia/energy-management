"""
Pydantic schemas for runtime analytics endpoints.
"""

from datetime import datetime
from pydantic import BaseModel


class ShiftRuntimeResponse(BaseModel):
    """Runtime for one machine in the current shift."""
    machine_id:              int
    machine_name:            str
    shift:                   str    # "day" or "night"
    shift_start_ist:         str    # "09:00" or "21:00"
    shift_end_ist:           str    # "21:00" or "09:00"
    shift_duration_minutes:  int    # always 720 (12 hours)
    runtime_minutes:         float  # minutes machine was running (freq > 0)
    runtime_pct:             float  # runtime_minutes / shift_duration_minutes * 100
    sampled_minutes:         float  # total minutes we have data for

    model_config = {"from_attributes": True}


class DailyRuntimeRow(BaseModel):
    """Runtime for one machine on one operational day."""
    machine_id:      int
    machine_name:    str
    operational_day: datetime  # start of operational day (03:30 UTC = 09:00 IST)
    runtime_minutes: float
    runtime_pct:     float     # runtime / 1440 minutes (full day) * 100
    sampled_minutes: float

    model_config = {"from_attributes": True}


class MachineRangeSummary(BaseModel):
    """Summary stats for one machine over a date range."""
    machine_id:                  int
    machine_name:                str
    total_runtime_minutes:       float
    total_sampled_minutes:       float
    utilisation_pct:             float
    avg_runtime_per_day_minutes: float
    best_day_runtime_minutes:    float
    worst_day_runtime_minutes:   float
    days_with_data:              int

    model_config = {"from_attributes": True}


class FleetRangeResponse(BaseModel):
    """Full response for fleet runtime range query."""
    from_date:   str                   # ISO date string
    to_date:     str                   # ISO date string
    bucket:      str                   # "day" (only supported bucket for now)
    daily_rows:  list[DailyRuntimeRow]
    summaries:   list[MachineRangeSummary]

    model_config = {"from_attributes": True}


class MachineRangeResponse(BaseModel):
    """Full response for single machine runtime range query."""
    machine_id:   int
    machine_name: str
    from_date:    str
    to_date:      str
    daily_rows:   list[DailyRuntimeRow]
    summary:      MachineRangeSummary

    model_config = {"from_attributes": True}
