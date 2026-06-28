from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, unique=True)
    address = Column(String)

    users = relationship(
        "User",
        back_populates="company"
    )
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    hashed_password = Column(String)
    role = Column(String)
    company_id = Column(Integer, ForeignKey("companies.id"))

    company = relationship(
        "Company",
        back_populates="users"
    )
