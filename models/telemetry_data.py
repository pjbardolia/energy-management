# Import SQLAlchemy column types
from sqlalchemy import Column, DateTime, Float, Integer, Text

# Import datetime for the default timestamp
from datetime import datetime

# Shared declarative base
from database import Base

# Relationship helper
from sqlalchemy.orm import relationship


class TelemetryData(Base):

    # PostgreSQL table name
    __tablename__ = "telemetry_data"

    # -----------------------------------------------------------------------
    # PHASE 4c — TimescaleDB hypertable
    #
    # Migration 002_timescale_hypertable converted this table to a TimescaleDB
    # hypertable partitioned by `timestamp`.  Two constraints changed:
    #
    #   1. PRIMARY KEY is now composite (id, timestamp).  TimescaleDB requires
    #      the partition column to appear in the PK.  id leads so that any
    #      future FK references into this table stay on the smallest column.
    #
    #   2. FK constraints on component_instance_id, tag_definition_id, and
    #      company_id were DROPPED.  TimescaleDB does not allow FK constraints
    #      on hypertables.  The columns remain as plain INTEGER NOT NULL —
    #      their values are intact and used for RLS (Phase 4d).  Referential
    #      integrity is now enforced at the application layer, not the DB layer.
    #
    # The SQLAlchemy model below reflects the post-migration state:
    #   - No ForeignKey() wrappers on the three affected columns.
    #   - The ORM relationships are kept so navigation still works in Python;
    #     SQLAlchemy uses them for in-process joins, not DB-level FK checks.
    # -----------------------------------------------------------------------

    # Composite primary key — id leads, timestamp is the partition column.
    # Declaring both as primary_key=True tells SQLAlchemy the PK is composite.
    # autoincrement=True must be set explicitly for composite PKs.
    # SQLAlchemy 1.1+ disables autoincrement on all columns when the PK is
    # composite — it does not infer it even if a SERIAL sequence exists in the
    # DB.  Without this, id is inserted as NULL and the row cannot be found.
    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True
    )

    # Hypertable partition column — TimescaleDB slices the table into chunks
    # by time range on this column.  Indexed here and via the hypertable
    # partitioning; almost all telemetry queries filter by time range.
    timestamp = Column(
        DateTime,
        primary_key=True,   # part of the composite PK required by TimescaleDB
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    # Which physical component (machine_component_instance row) generated
    # this reading.  FK constraint dropped in migration 002 (TimescaleDB
    # hypertable restriction) — kept as plain Integer, value still populated.
    component_instance_id = Column(
        Integer,    # no ForeignKey() — FK was dropped for TimescaleDB compatibility
        nullable=False
    )

    # Which measurement type (tag_definition row) this reading belongs to.
    # FK constraint dropped in migration 002 — kept as plain Integer.
    tag_definition_id = Column(
        Integer,    # no ForeignKey() — FK was dropped for TimescaleDB compatibility
        nullable=False
    )

    # --- Two-column value design (ADR Decision 2) ---
    #
    # float/int/bool tags → value_num (DOUBLE PRECISION / Float)
    # text tags           → value_text (TEXT)
    # The tag_definition.data_type field tells the application which to use.

    # Numeric value for float / int / bool tags.
    # NULL when the tag's data_type is 'text'.
    value_num = Column(
        Float,         # PostgreSQL: float8 = DOUBLE PRECISION (64-bit)
        nullable=True  # NULL for text-type tags
    )

    # Text value for fault codes, status strings, batch IDs, etc.
    # NULL for the ~99% of readings that are numeric.
    value_text = Column(
        Text,
        nullable=True  # NULL for numeric-type tags
    )

    # Multi-tenant isolation: every telemetry row is stamped with the company
    # that owns it.  Used for RLS in Phase 4d (current_setting filter).
    # FK constraint dropped in migration 002 — kept as plain Integer.
    company_id = Column(
        Integer,    # no ForeignKey() — FK was dropped for TimescaleDB compatibility
        nullable=False
    )

    # ORM relationships — used for in-process navigation in Python.
    # These do NOT imply DB-level FK constraints (those were dropped).
    # SQLAlchemy uses them for lazy-loading and joins within a session.

    # Navigate from a reading to the component that produced it.
    # primaryjoin with foreign() mirrors the annotation on the parent side
    # (MachineComponentInstance.telemetry_records) — both ends must agree on
    # which column is the "FK side" when there is no DB-level FK constraint.
    component_instance = relationship(
        "MachineComponentInstance",
        primaryjoin="foreign(TelemetryData.component_instance_id) == MachineComponentInstance.id",
        back_populates="telemetry_records"
    )

    # Navigate from a reading to its tag definition (to get unit, data_type, etc.)
    tag_definition = relationship(
        "TagDefinition",
        primaryjoin="foreign(TelemetryData.tag_definition_id) == TagDefinition.id",
        back_populates="telemetry_records"
    )
