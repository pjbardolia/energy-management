"""
Pydantic schemas for gateway heartbeat endpoints.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class HeartbeatCreate(BaseModel):
    """Payload posted by the Pi gateway after every poll cycle."""
    poll_duration_sec: Optional[float] = None
    machines_polled:   Optional[int]   = None
    machines_failed:   Optional[int]   = None


class GatewayStatusResponse(BaseModel):
    """Returned by GET /gateway/status to the frontend."""
    last_seen:         Optional[datetime]
    seconds_ago:       Optional[int]
    is_online:         bool              # True if last_seen is within 2 minutes
    poll_duration_sec: Optional[float]
    machines_polled:   Optional[int]
    machines_failed:   Optional[int]

    model_config = {"from_attributes": True}
