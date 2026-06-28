# Import SQLAlchemy column types
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer

# Import datetime module
from datetime import datetime

# Import Base class
from database import Base

# Import relationship function
from sqlalchemy.orm import relationship


class TelemetryData(Base):

    # PostgreSQL table name
    __tablename__ = "telemetry_data"

    # Primary Key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Timestamp of reading
    timestamp = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )

    # Which physical component generated this value
    component_instance_id = Column(
        Integer,
        ForeignKey("machine_component_instance.id"),
        nullable=False
    )

    # Which tag this value belongs to
    tag_definition_id = Column(
        Integer,
        ForeignKey("tag_definition.id"),
        nullable=False
    )

    # Actual measured value
    value = Column(
        Float,
        nullable=False
    )

    # Relationship to component instance
    component_instance = relationship(
        "MachineComponentInstance",
        back_populates="telemetry_records"
    )

    # Relationship to tag definition
    tag_definition = relationship(
        "TagDefinition",
        back_populates="telemetry_records"
    )
