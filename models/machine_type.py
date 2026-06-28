# Import SQLAlchemy column types
from sqlalchemy import Column, Integer, String, ForeignKey

# Relationship helper
from sqlalchemy.orm import relationship

# Shared declarative base
from database import Base


class MachineType(Base):

    # PostgreSQL table name
    __tablename__ = "machine_type"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Machine type name, e.g. "Stenter", "Jigger", "Circulation Pump", "Compressor"
    name = Column(
        String,
        nullable=False
    )

    # Optional description of what this machine type does
    description = Column(
        String,
        nullable=True
    )

    # --- Why company_id replaces department_id here (ADR Decision 3) ---
    #
    # The previous design put department_id on MachineType, which forced every
    # machine of a given type into exactly one department.  That breaks
    # multi-industry use: a "Pump" legitimately appears in Cooling, Utilities,
    # and Reaction departments at the same time.
    #
    # MachineType is now a pure reusable catalogue — it answers only
    # "what kind of machine is this?".  The question "where does this specific
    # machine live?" is answered by Machine.department_id, set per physical
    # machine (not per type).
    #
    # company_id here means each tenant maintains their own machine-type
    # catalogue (multi-tenant isolation, ADR Decision 4).
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Navigate from this machine type up to the company that owns it
    company = relationship(
        "Company",
        back_populates="machine_types"
    )

    # All physical machine instances of this type across this company
    machines = relationship(
        "Machine",
        back_populates="machine_type"
    )
