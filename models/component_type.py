from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class ComponentType(Base):

    __tablename__ = "component_type"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    name = Column(
        String,
        nullable=False
    )

    # Optional description of what this component type does
    description = Column(
        String,
        nullable=True
    )

    # Multi-tenant isolation: each company defines its own component type catalogue.
    # e.g. company A calls it "VFD", company B calls it "Variable Speed Drive" —
    # both are valid and independent.
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Navigate from this component type up to the company that owns it
    company = relationship(
        "Company",
        back_populates="component_types"
    )

    # All physical component instances of this type
    component_instances = relationship(
        "MachineComponentInstance",
        back_populates="component_type"
    )

    # All junction rows declaring which tags (measurements) this component type produces
    component_type_tags = relationship(
        "ComponentTypeTag",
        back_populates="component_type"
    )
