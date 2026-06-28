# Import SQLAlchemy column types
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer

# Import datetime module
from datetime import datetime

# Import Base class
from database import Base

# Import relationship function
from sqlalchemy.orm import relationship


# Create Data model
class Data(Base):

    # PostgreSQL table name
    __tablename__ = "data"

    # Primary Key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Date and time when data was recorded
    timestamp = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # Which physical component generated this value
    component_instance_id = Column(
        Integer,
        ForeignKey("machine_component_instance.id"),
        nullable=False
    )

    # What parameter was recorded
    # Examples:
    # Frequency
    # Current
    # Voltage
    # RPM
    # Pressure
    # Temperature
    tag_definition_id = Column(
        Integer,
        ForeignKey("tag_definition.id"),
        nullable=False
    )

    # Actual value recorded
    value = Column(
        Float,
        nullable=False
    )

    # Relationship to MachineComponentInstance table
    component_instance = relationship(
        "MachineComponentInstance",
        back_populates="data_records"
    )

    # Relationship to TagDefinition table
    tag_definition = relationship(
        "TagDefinition",
        back_populates="data_records"
    )
