# Router for TagDefinition CRUD endpoints.
#
# A TagDefinition describes one type of measurement, e.g. "Output Frequency"
# (unit: Hz, data_type: float).  Tags are scoped per company.  TelemetryData
# rows reference a TagDefinition via TelemetryData.tag_definition_id to
# identify what was measured.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from schemas.tag_definition import TagDefinitionCreate, TagDefinitionResponse
from models import TagDefinition


router = APIRouter()


@router.post("/tag-definitions", response_model=TagDefinitionResponse, status_code=201)
def create_tag_definition(tag: TagDefinitionCreate, db: Session = Depends(get_db)):
    db_tag = TagDefinition(
        name=tag.name,
        unit=tag.unit,
        description=tag.description,
        data_type=tag.data_type,
        company_id=tag.company_id
    )

    db.add(db_tag)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning — clears the aborted transaction so the
        # session is safe for reuse.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create tag definition — check that company_id exists."
        )

    db.refresh(db_tag)
    return db_tag


@router.get("/tag-definitions", response_model=list[TagDefinitionResponse])
def get_tag_definitions(db: Session = Depends(get_db)):
    # Returns every tag definition across all companies.
    # Phase 3 will add ?company_id= and ?data_type= filters.
    return db.query(TagDefinition).all()
