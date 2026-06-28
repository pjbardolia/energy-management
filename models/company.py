from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from database import Base


class Company(Base):

    __tablename__ = "company"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_name = Column(
        String,
        nullable=False
    )

    address = Column(
        String
    )

    # All users that belong to this company
    users = relationship(
        "User",
        back_populates="company"
    )

    # All departments in this company
    departments = relationship(
        "Department",
        back_populates="company"
    )

    # This company's machine-type catalogue (e.g. Stenter, Pump, Compressor)
    machine_types = relationship(
        "MachineType",
        back_populates="company"
    )

    # This company's component-type catalogue (e.g. VFD, Motor, Sensor)
    component_types = relationship(
        "ComponentType",
        back_populates="company"
    )

    # This company's tag-definition catalogue (e.g. Output Frequency, DC Bus Voltage)
    tag_definitions = relationship(
        "TagDefinition",
        back_populates="company"
    )

    # Note: MachineComponentInstance, ComponentTypeTag, and TelemetryData also carry
    # company_id for Row-Level Security (Phase 4), but we don't add back-references
    # here — those collections would be enormous and are accessed through the
    # hierarchy (company → departments → machines → components → telemetry).
