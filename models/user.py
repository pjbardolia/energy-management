# Import SQLAlchemy column types
from sqlalchemy import Column, Integer, String, ForeignKey

# Import relationship
from sqlalchemy.orm import relationship

# Import Base class
from database import Base


# Create User model
class User(Base):

    # PostgreSQL table name
    __tablename__ = "users"

    # Primary key
    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Username
    username = Column(
        String,
        unique=True,
        nullable=False
    )

    # Password hash
    password_hash = Column(
        String,
        nullable=False
    )

    # Role
    role = Column(
        String,
        nullable=False
    )

    # Company ID imported from company table
    company_id = Column(
        Integer,
        ForeignKey("company.id"),
        nullable=False
    )

    # Relationship to company table
    company = relationship(
        "Company",
        back_populates="users"
    )
