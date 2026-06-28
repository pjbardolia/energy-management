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

    # FK to tag_definition: which measurement this component type is expected to produce
    tag_definition_id = Column(
        Integer,
        ForeignKey("tag_definition.id"),
        nullable=False
    )

    # Multi-tenant isolation: junction rows belong to the same company that owns
    # both the component type and the tag definition they link together
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Navigate from this junction row to the component type it belongs to
    component_type = relationship(
        "ComponentType",
        back_populates="component_type_tags"
    )

    # Navigate from this junction row to the tag definition it declares
    tag_definition = relationship(
        "TagDefinition",
        back_populates="component_type_tags"
    )

