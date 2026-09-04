"""
Pydantic schemas for the water consumption endpoint.
"""

from datetime import datetime
from pydantic import BaseModel


class WaterWindowTotal(BaseModel):
    """Consumption over one fixed window, as (end reading - start reading)."""
    window_start: datetime
    window_end:   datetime
    liters:       float | None   # None unless status == "ok"
    status:       str            # "ok" | "future" | "no_data" | "anomaly"

    model_config = {"from_attributes": True}


class WaterConsumptionTotals(BaseModel):
    since_9am: WaterWindowTotal   # 09:00 -> now (if today) or 09:00 -> next 09:00 (past day)
    shift_a:   WaterWindowTotal   # 09:00-21:00 IST
    shift_b:   WaterWindowTotal   # 21:00-09:00 IST
    full_day:  WaterWindowTotal   # 09:00-09:00 IST (24h)

    model_config = {"from_attributes": True}


class WaterIntervalBucket(BaseModel):
    """Consumption within one 30-minute bucket of the operational day."""
    bucket_start: datetime
    bucket_end:   datetime
    liters:       float | None   # None unless status == "ok"
    status:       str            # "ok" | "future" | "no_data" | "anomaly"

    model_config = {"from_attributes": True}


class WaterConsumptionResponse(BaseModel):
    machine_id:   int
    machine_name: str
    date:         str
    totals:       WaterConsumptionTotals
    intervals:    list[WaterIntervalBucket]

    model_config = {"from_attributes": True}
