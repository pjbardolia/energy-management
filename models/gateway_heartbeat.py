"""
Gateway heartbeat model.

One row per company — upserted on every POST /gateway/heartbeat.
Stores when the Pi last checked in and basic poll health metrics.
"""

from sqlalchemy import Column, Integer, Float, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class GatewayHeartbeat(Base):
    __tablename__ = "gateway_heartbeat"

    id                = Column(Integer, primary_key=True, index=True)
    company_id        = Column(Integer, nullable=False, index=True)
    # server_default=func.now() provides a sensible default on INSERT;
    # the router always supplies an explicit UTC value via the pg_insert upsert.
    last_seen         = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    poll_duration_sec = Column(Float, nullable=True)    # how long the full poll cycle took
    machines_polled   = Column(Integer, nullable=True)  # how many devices were attempted
    machines_failed   = Column(Integer, nullable=True)  # how many timed out / errored

    # One row per company — the upsert constraint that pg_insert ON CONFLICT targets.
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_gateway_heartbeat_company"),
    )
