from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from database import Base


class Company(Base):

    __tablename__ = "company"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_name = Column(
        String,
        nullable=False
    )

    address = Column(
        String
    )

    users = relationship(
        "User",
        back_populates="company"
    )

    departments = relationship(
        "Department",
        back_populates="company"
    )
