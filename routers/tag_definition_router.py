# Router for TagDefinition CRUD endpoints.
#
# A TagDefinition describes one type of measurement, e.g. "Output Frequency"
# (unit: Hz, data_type: float).  Tags are scoped per company.  TelemetryData
# rows reference a TagDefinition via TelemetryData.tag_definition_id to
# identify what was measured.
#
# Phase 4d changes:
#   - All endpoints now require a valid JWT (get_tenant_db enforces this).
#   - GET /tag-definitions filters by the authenticated user's company_id.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, get_tenant_db
from schemas.tag_definition import TagDefinitionCreate, TagDefinitionResponse
from models import TagDefinition


router = APIRouter()


@router.post("/tag-definitions", response_model=TagDefinitionResponse, status_code=201)
def create_tag_definition(
    tag: TagDefinitionCreate,
    db: Session = Depends(get_tenant_db),
):
    db_tag = TagDefinition(
        name=tag.name,
        key=tag.key,        # stable slug — unique per (company_id, key)
        unit=tag.unit,
        description=tag.description,
        data_type=tag.data_type,
        company_id=tag.company_id,
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
            detail=(
                "Could not create tag definition — check that company_id exists "
                "and that the key is unique for this company."
            ),
        )

    db.refresh(db_tag)
    return db_tag


@router.get("/tag-definitions", response_model=list[TagDefinitionResponse])
def get_tag_definitions(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # WHERE filter scopes results to the authenticated tenant.
    return (
        db.query(TagDefinition)
        .filter(TagDefinition.company_id == current_user["company_id"])
        .all()
    )
