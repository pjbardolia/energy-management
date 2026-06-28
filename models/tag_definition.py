from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class TagDefinition(Base):

    __tablename__ = "tag_definition"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    name = Column(
        String,
        nullable=False
    )

    unit = Column(
        String
    )

    description = Column(
        String
    )

    component_type_tags = relationship(
        "ComponentTypeTag",
        back_populates="tag_definition"
    )

    
    telemetry_records = relationship(
    "TelemetryData",
    back_populates="tag_definition"
    )
