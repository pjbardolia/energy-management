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

    description = Column(
        String
    )

    component_instances = relationship(
        "MachineComponentInstance",
        back_populates="component_type"
    )
    
    component_type_tags = relationship(
    "ComponentTypeTag",
    back_populates="component_type"
    )
