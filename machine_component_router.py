from fastapi import APIRouter
from sqlalchemy.orm import Session

from database import SessionLocal
from schemas.machine_component import (
    MachineComponentCreate,
    MachineComponentResponse
)
from models import MachineComponentInstance


router = APIRouter()


@router.post("/machine-components", response_model=MachineComponentResponse)
def create_machine_component(component: MachineComponentCreate):

    db: Session = SessionLocal()

    db_component = MachineComponentInstance(
        name=component.name,
        component_type=component.component_type,
        machine_id=component.machine_id
    )

    db.add(db_component)
    db.commit()
    db.refresh(db_component)

    return db_component


@router.get("/machine-components")
def get_machine_components():

    db: Session = SessionLocal()

    components = db.query(MachineComponentInstance).all()

    return components
