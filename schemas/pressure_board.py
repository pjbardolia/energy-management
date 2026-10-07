"""
Pydantic schemas for the Temperature & Pressure board endpoint.
New file — no existing schema is touched.
"""

from datetime import datetime
from pydantic import BaseModel


class BoardReading(BaseModel):
    """Last-ever reading of one sensor, regardless of age (so a sensor that
    went silent still reports when it was last seen)."""
    value:     float | None
    timestamp: datetime | None


class BoardSparkPoint(BaseModel):
    t: datetime
    v: float


class PressureBoardMachine(BaseModel):
    machine_id:   int
    machine_name: str
    pressure:     BoardReading | None      # None = machine has no pressure sensor
    temperature:  BoardReading | None      # None = machine has no temperature sensor

    # Hysteresis state replayed over the window (alert > limit, clear <= clear
    # threshold, hold in between) — same rule as check_pressure_alerts().
    alarm_active:          bool
    above_since:           datetime | None  # start of the current over-limit run
    above_since_truncated: bool             # run began at/before the window start

    shift_peak: float | None                # max pressure in the current shift
    sparkline:  list[BoardSparkPoint]       # last hour, 1-minute resolution


class PressureBoardResponse(BaseModel):
    generated_at:    datetime
    shift:           str        # "A" (09:00-21:00 IST) or "B" (21:00-09:00 IST)
    shift_start:     datetime
    shift_end:       datetime
    alert_threshold: float
    clear_threshold: float
    machines:        list[PressureBoardMachine]
