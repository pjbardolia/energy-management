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

    # Machine type
    machine_type_id = Column(
    Integer,
    ForeignKey("machine_type.id"),
    nullable=False
    )

    # Machine description
    description = Column(
        String
    )

    # Company ID imported from company table
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )
    
    machine_type = relationship(
    "MachineType",
    back_populates="machines"
    )    

    # Relationship to MachineComponent table
    component_instances = relationship(
        "MachineComponentInstance",
        back_populates="machine"
    )
