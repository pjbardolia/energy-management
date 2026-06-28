# Import SQLAlchemy column types
from sqlalchemy import Column, Integer, String, ForeignKey

# Import Base class from database.py
from database import Base

# Import relationship function from SQLAlchemy
from sqlalchemy.orm import relationship


# Create Department model
class Department(Base):

    # PostgreSQL table name
    __tablename__ = "department"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Department name
    name = Column(
        String,
        nullable=False
    )

    # Department description
    description = Column(
        String
    )

    # Company ID imported from company table
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Relationship to Company table
    company = relationship(
        "Company",
        back_populates="departments"
    )

    # Relationship to Machine table
    machine_types = relationship(
    "MachineType",
    back_populates="department"
)
