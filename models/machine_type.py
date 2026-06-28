from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class MachineType(Base):

    __tablename__ = "machine_type"

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

    department_id = Column(
        Integer,
        ForeignKey("department.id"),
        nullable=False
    )

    department = relationship(
        "Department",
        back_populates="machine_types"
    )

    machines = relationship(
        "Machine",
        back_populates="machine_type"
    )
