"""
Pydantic schemas for energy consumption endpoints.
"""

from pydantic import BaseModel
from datetime import datetime


class ShiftEnergyResponse(BaseModel):
    """kWh consumed by one machine in the current shift."""
    machine_id:             int
    machine_name:           str
    shift:                  str            # "day" or "night"
    shift_start_ist:        str            # "09:00" or "21:00"
    shift_end_ist:          str            # "21:00" or "09:00"
    kwh_consumed:           float          # kWh since shift start
    cost_inr:               float          # kwh_consumed × TARIFF_PER_KWH

    model_config = {"from_attributes": True}


class DailyEnergyRow(BaseModel):
    """kWh consumed by one machine on one operational day."""
    machine_id:             int
    machine_name:           str
    operational_day:        datetime       # 03:30 UTC = 09:00 IST
    kwh_consumed:           float
    cost_inr:               float

    model_config = {"from_attributes": True}


class MachineEnergySummary(BaseModel):
    """Summary stats for one machine over a date range."""
    machine_id:             int
    machine_name:           str
    total_kwh:              float
    total_cost_inr:         float
    avg_kwh_per_day:        float
    peak_day_kwh:           float
    days_with_data:         int

    model_config = {"from_attributes": True}


class FleetEnergyRangeResponse(BaseModel):
    """Full response for fleet energy range query."""
    from_date:              str
    to_date:                str
    tariff_per_kwh_inr:     float
    daily_rows:             list[DailyEnergyRow]
    summaries:              list[MachineEnergySummary]

    model_config = {"from_attributes": True}


class MachineEnergyRangeResponse(BaseModel):
    """Full response for single machine energy range query."""
    machine_id:             int
    machine_name:           str
    from_date:              str
    to_date:                str
    tariff_per_kwh_inr:     float
    daily_rows:             list[DailyEnergyRow]
    summary:                MachineEnergySummary

    model_config = {"from_attributes": True}
