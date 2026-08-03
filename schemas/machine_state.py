"""
Pydantic schemas for machine state-timeline / utilization endpoints.
"""

from datetime import datetime
from pydantic import BaseModel


class StateInterval(BaseModel):
    """One running/stopped interval. The currently-open interval (no ended_at
    in the DB) is already capped at "now" by the router before serialisation."""
    state:      str    # "running" | "stopped"
    started_at: datetime
    ended_at:   datetime

    model_config = {"from_attributes": True}


class MachineStateTimelineResponse(BaseModel):
    """State intervals for one machine over a time range."""
    machine_id:   int
    machine_name: str
    range_from:   str
    range_to:     str
    intervals:    list[StateInterval]

    model_config = {"from_attributes": True}


class FleetStateTimelineResponse(BaseModel):
    """State intervals for every machine over a time range, grouped by machine."""
    range_from: str
    range_to:   str
    machines:   list[MachineStateTimelineResponse]

    model_config = {"from_attributes": True}


class DailyUtilizationRow(BaseModel):
    """Running vs elapsed minutes for one machine on one operational day."""
    operational_day: datetime  # start of operational day (03:30 UTC = 09:00 IST)
    running_minutes: float
    elapsed_minutes: float     # total minutes covered by ANY state_event interval
                               # this day — "scheduled minutes" isn't a concept in
                               # this system yet, so elapsed/calendar time is the
                               # denominator (matches routers/runtime.py's runtime_pct).
    utilization_pct: float     # running_minutes / elapsed_minutes * 100

    model_config = {"from_attributes": True}


class MachineUtilizationDailyResponse(BaseModel):
    """Daily utilization for one machine over a date range."""
    machine_id:   int
    machine_name: str
    from_date:    str
    to_date:      str
    daily_rows:   list[DailyUtilizationRow]

    model_config = {"from_attributes": True}


class FleetUtilizationDailyResponse(BaseModel):
    """Daily utilization for every machine over a date range, grouped by
    machine — powers the heatmap calendar and OEE availability cards without
    one request per machine."""
    from_date: str
    to_date:   str
    machines:  list[MachineUtilizationDailyResponse]

    model_config = {"from_attributes": True}
