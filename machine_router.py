from fastapi import APIRouter
from sqlalchemy.orm import Session

from database import SessionLocal
from schemas.machine import MachineCreate, MachineResponse
from models import Machine


router = APIRouter()


@router.post("/machines", response_model=MachineResponse)
def create_machine(machine: MachineCreate):

    db: Session = SessionLocal()

    db_machine = Machine(
        name=machine.name,
        machine_type=machine.machine_type,
        description=machine.description,
        company_id=machine.company_id,
        department_id=machine.department_id
    )

    db.add(db_machine)
    db.commit()
    db.refresh(db_machine)

    return db_machine


@router.get("/machines")
def get_machines():

    db: Session = SessionLocal()

    machines = db.query(Machine).all()

    return machines
