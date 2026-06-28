from fastapi import APIRouter
from sqlalchemy.orm import Session

from database import SessionLocal
from schemas.telemetry import DataCreate, DataResponse
from models import TelemetryData


router = APIRouter()


@router.post("/data", response_model=DataResponse)
def create_data(data: DataCreate):

    db: Session = SessionLocal()

    db_data = TelemetryData(
        timestamp=data.timestamp,
        machine_id=data.machine_id,
        component_id=data.component_id,
        output_frequency=data.output_frequency,
        reference_frequency=data.reference_frequency,
        dc_bus_voltage=data.dc_bus_voltage,
        output_voltage=data.output_voltage,
        output_current=data.output_current,
        rotation_speed=data.rotation_speed,
        output_power=data.output_power,
        output_torque=data.output_torque,
        temperature=data.temperature,
        pressure=data.pressure
    )

    db.add(db_data)
    db.commit()
    db.refresh(db_data)

    return db_data


@router.get("/data")
def get_data():

    db: Session = SessionLocal()

    data = db.query(TelemetryData).all()

    return data
