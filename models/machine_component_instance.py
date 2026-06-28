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

    # FK to machine: which machine this component is physically attached to
    machine_id = Column(
        Integer,
        ForeignKey("machine.id"),
        nullable=False
    )

    # Multi-tenant isolation: physical component instances belong to one company
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Navigate from this component instance up to the machine it belongs to
    machine = relationship(
        "Machine",
        back_populates="component_instances"
    )

    # Navigate from this component instance to its type definition
    component_type = relationship(
        "ComponentType",
        back_populates="component_instances"
    )

    # All telemetry readings produced by this component instance
    telemetry_records = relationship(
        "TelemetryData",
        back_populates="component_instance"
    )
