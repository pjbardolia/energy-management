import enum  # Python standard library enum support

# SQLAlchemy column types, including Enum for the data_type field
from sqlalchemy import Column, Integer, String, ForeignKey, Enum as SAEnum

# Relationship helper for navigating between ORM objects
from sqlalchemy.orm import relationship

# Shared declarative base — all models must use this same Base
from database import Base


# --- TagDataType enum ---
# This enum declares what kind of value a tag produces.
# It controls:
#   1. Which TelemetryData column stores the reading (value_num vs value_text)
#   2. How dashboards render the value (e.g. bool → "Off/On", int → no decimals)
#
# Using str as a mixin means FastAPI serialises it as a plain JSON string
# ("float") rather than {"name": "float", "value": "float"}.
class TagDataType(str, enum.Enum):
    float = "float"   # Decimal sensor readings: frequency, voltage, current, power
    int   = "int"     # Whole-number readings: counters, RPM rounded to integers
    bool  = "bool"    # On/Off state — stored as 1.0 (on) or 0.0 (off) in value_num
    text  = "text"    # Fault codes, status strings, batch IDs — stored in value_text


class TagDefinition(Base):

    # PostgreSQL table name
    __tablename__ = "tag_definition"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Human-readable measurement name, e.g. "Output Frequency", "DC Bus Voltage".
    # Operators may rename this label; the stable API contract is the 'key' column.
    name = Column(
        String,
        nullable=False
    )

    # Machine-readable slug — the stable contract with gateways and frontends.
    # Examples: "frequency", "dc_voltage", "output_voltage"
    # Unique per company via migration 004 index (uq_tag_definition_company_key).
    # Written once on creation; treat as immutable after the first telemetry row
    # references this tag.
    key = Column(
        String,
        nullable=False
    )

    # SI or display unit, e.g. "Hz", "V", "A", "kW" — None is fine for text-type tags
    unit = Column(
        String,
        nullable=True
    )

    # Optional longer explanation of what this measurement represents
    description = Column(
        String,
        nullable=True
    )

    # Declares which value column to write to and how to render the reading.
    # SAEnum creates a PostgreSQL ENUM type named 'tag_data_type' in the DB.
    # 'float' / 'int' / 'bool' → TelemetryData.value_num
    # 'text'                   → TelemetryData.value_text
    data_type = Column(
        SAEnum(TagDataType, name="tag_data_type"),
        nullable=False
    )

    # Multi-tenant isolation: each company maintains its own tag catalogue.
    # Without this, one tenant's "Output Frequency" tag could be confused with
    # another tenant's tag that happens to share the same database row ID.
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Navigate from a tag definition up to the company that owns it
    company = relationship(
        "Company",
        back_populates="tag_definitions"
    )

    # All junction rows that link this tag to component types
    # (used to declare which measurements a component type produces)
    component_type_tags = relationship(
        "ComponentTypeTag",
        back_populates="tag_definition"
    )

    # All telemetry readings that are filed under this tag
    # primaryjoin with foreign() is required because TelemetryData.tag_definition_id
    # has no ForeignKey() declaration (dropped in Phase 4c for TimescaleDB hypertable
    # compatibility).  foreign() marks that column as the "FK side" of the join so
    # SQLAlchemy can resolve the relationship without a DB-level constraint.
    telemetry_records = relationship(
        "TelemetryData",
        primaryjoin="TagDefinition.id == foreign(TelemetryData.tag_definition_id)",
        back_populates="tag_definition"
    )
