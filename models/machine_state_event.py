"""
Machine state-change event model.

One row per running/stopped interval per machine — written only on actual
transitions (see services/state_tracker.py), not one row per poll. A machine
running steadily all day produces exactly one open row; a currently-ongoing
interval has ended_at = NULL.

RUNNING vs STOPPED uses the same threshold as everywhere else in this
codebase: frequency (tag_definition_id=6) > 0. See routers/runtime.py and
the Fleet tile's getMachineState() in frontend/src/App.jsx.
"""

import enum

from sqlalchemy import Column, Integer, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.sql import func

from database import Base


class MachineState(str, enum.Enum):
    running = "running"
    stopped = "stopped"


class MachineStateEvent(Base):
    __tablename__ = "machine_state_event"

    id         = Column(Integer, primary_key=True, index=True)
    machine_id = Column(Integer, ForeignKey("machine.id"), nullable=False)

    # Multi-tenant isolation — same direct company_id column pattern as every
    # other RLS-covered table (tag_definition, machine_component_instance, ...).
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False)

    # SAEnum creates a PostgreSQL ENUM type named 'machine_state' — already
    # created explicitly in migration 006 (raw SQL, matching how
    # tag_data_type is created in 001_initial_schema.py).
    state = Column(SAEnum(MachineState, name="machine_state"), nullable=False)

    started_at = Column(DateTime(timezone=True), nullable=False)
    # NULL = this interval is still ongoing (the currently-open row).
    ended_at   = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
