from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class ComponentTypeTag(Base):

    __tablename__ = "component_type_tag"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    component_type_id = Column(
        Integer,
        ForeignKey("component_type.id"),
        nullable=False
    )

    tag_definition_id = Column(
        Integer,
        ForeignKey("tag_definition.id"),
        nullable=False
    )

    component_type = relationship(
        "ComponentType",
        back_populates="component_type_tags"
    )

    tag_definition = relationship(
        "TagDefinition",
        back_populates="component_type_tags"
    )

