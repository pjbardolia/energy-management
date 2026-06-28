from sqlalchemy import Column, Integer, String, ForeignKey
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    role = Column(String)

    company_id = Column(
        Integer,
        ForeignKey("companies.id")
    )
