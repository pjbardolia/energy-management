# Import SQLAlchemy column types
from sqlalchemy import Column, Integer, String, ForeignKey

# Import Base class from database.py
from database import Base

# Import relationship function from SQLAlchemy
from sqlalchemy.orm import relationship

# Create MachineComponent model
class MachineComponentInstance(Base):

    # Name of table inside PostgreSQL
    __tablename__ = "machine_component_instance"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Component name
    # Examples:
    # Circulation Pump
    # Main Chain Motor
    # Mangle Motor
    # Pressure Sensor
    # Temperature Sensor
    name = Column(
        String,
        nullable=False
    )

    # Component type
    component_type_id = Column(
    Integer,
    ForeignKey("component_type.id"),
    nullable=False
    )

    # Machine ID imported from machine table
    machine_id = Column(
        Integer,
        ForeignKey("machine.id"),
        nullable=False
    )

    # Relationship to machine table
    # Allows:
    # component.machine
    machine = relationship(
        "Machine",
        back_populates="component_instances"
    )
 
    component_type = relationship(
    "ComponentType",
    back_populates="component_instances"
    )
    # Relationship to data table
    # Allows:
    # component.data_records
    
    telemetry_records = relationship(
    "TelemetryData",
    back_populates="component_instance"
    )
