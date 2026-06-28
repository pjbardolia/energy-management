# Import SQLAlchemy column types
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text

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
    # PHASE 4 NOTE — TimescaleDB hypertable
    #
    # In Phase 4 this table is converted to a TimescaleDB hypertable partitioned
    # by `timestamp`.  Two constraints change at that point:
    #
    #   1. PRIMARY KEY must include the partition column, so the PK will become
    #      composite: (id, timestamp) or (timestamp, id).  The single-column PK
    #      below is correct for plain Postgres (Phases 1–3) and will be altered
    #      in Phase 4 via an Alembic migration.
    #
    #   2. TimescaleDB does NOT support foreign keys on hypertables.  The FK
    #      declarations on component_instance_id, tag_definition_id, and
    #      company_id will be dropped in Phase 4; referential integrity will
    #      then be enforced at the application layer instead of the DB layer.
    #
    # Nothing in this model needs to change before Phase 4.
    # -----------------------------------------------------------------------

    # Primary key (single column, valid for plain Postgres; see Phase 4 note above)
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # When the reading was taken.
    # Indexed here because almost all telemetry queries filter by time range.
    # Becomes the hypertable partition key in Phase 4.
    timestamp = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    # Which physical component (machine_component_instance row) generated this reading.
    # Phase 4: FK dropped, becomes plain Integer — see note above.
    component_instance_id = Column(
        Integer,
        ForeignKey("machine_component_instance.id"),
        nullable=False
    )

    # Which measurement type (tag_definition row) this reading belongs to.
    # Phase 4: FK dropped, becomes plain Integer — see note above.
    tag_definition_id = Column(
        Integer,
        ForeignKey("tag_definition.id"),
        nullable=False
    )

    # --- Two-column value design (ADR Decision 2) ---
    #
    # Rationale: a single FLOAT column would work today (all current data is
    # numeric) but would require a billion-row migration the first time a
    # text signal appears (fault codes, status strings, batch IDs).
    # Two nullable columns cost almost nothing extra:
    #   - value_text is NULL ~99 % of the time and compresses to ~nothing
    #     under TimescaleDB columnar compression.
    #   - Application logic uses tag_definition.data_type to decide which
    #     column to write/read (float/int/bool → value_num, text → value_text).

    # Numeric value for float / int / bool tags.
    # Float() maps to float8 / DOUBLE PRECISION in PostgreSQL — the standard
    # compact, fast, compressible type for high-volume sensor data.
    # bool tags store 1.0 (on/true) or 0.0 (off/false).
    # NULL when the tag's data_type is 'text'.
    value_num = Column(
        Float,          # PostgreSQL: float8 = DOUBLE PRECISION (64-bit)
        nullable=True   # NULL for text-type tags
    )

    # Text value for fault codes, status strings, batch/lot IDs, etc.
    # NULL for the ~99 % of readings that are numeric.
    # Text() maps to the unbounded TEXT type in PostgreSQL.
    value_text = Column(
        Text,
        nullable=True   # NULL for numeric-type tags
    )

    # Multi-tenant isolation: every telemetry row is stamped with the company
    # that owns the component and tag that produced it.
    # Leads the secondary index (company_id, timestamp) so each tenant only
    # reads its own recent chunks — critical at 50 000-factory scale.
    # Phase 4: FK dropped, becomes plain Integer — see note above.
    company_id = Column(
        Integer,
        ForeignKey("company.id"),  # becomes plain Integer in Phase 4
        nullable=False
    )

    # Navigate from a reading to the component that produced it
    component_instance = relationship(
        "MachineComponentInstance",
        back_populates="telemetry_records"
    )

    # Navigate from a reading to its tag definition (to get unit, data_type, etc.)
    tag_definition = relationship(
        "TagDefinition",
        back_populates="telemetry_records"
    )
