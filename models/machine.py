# Import SQLAlchemy column types
from sqlalchemy import Column, Integer, String, ForeignKey

# Import Base class
from database import Base

# Import relationship function from SQLAlchemy
from sqlalchemy.orm import relationship


# Create Machine model
class Machine(Base):

    # PostgreSQL table name
    __tablename__ = "machine"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Machine name
    name = Column(
        String,
        nullable=False
    )

    # FK to machine_type: "what kind of machine is this?" (e.g. Stenter, Pump)
    machine_type_id = Column(
        Integer,
        ForeignKey("machine_type.id"),
        nullable=False
    )

    # Optional human description, e.g. "10-chamber gas stenter, commissioned 2019"
    description = Column(
        String,
        nullable=True
    )

    # FK to company: which tenant owns this machine (multi-tenant, ADR Decision 4)
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # --- Why department_id is on Machine, not MachineType (ADR Decision 3) ---
    # "What kind of machine" (machine_type_id) and "where it physically lives"
    # (department_id) are independent facts.  Putting department on MachineType
    # forced every machine of that type into one department — broken for
    # multi-industry where a "Pump" type appears in Cooling, Utilities, and
    # Reaction departments simultaneously.  Each physical machine now declares
    # its own department independently of its type.
    department_id = Column(
        Integer,
        ForeignKey("department.id"),
        nullable=False
    )

    # Navigate from this machine to its type (e.g. machine.machine_type.name)
    machine_type = relationship(
        "MachineType",
        back_populates="machines"
    )

    # Navigate from this machine to the department it lives in
    department = relationship(
        "Department",
        back_populates="machines"
    )

    # All component instances physically attached to this machine
    component_instances = relationship(
        "MachineComponentInstance",
        back_populates="machine"
    )
