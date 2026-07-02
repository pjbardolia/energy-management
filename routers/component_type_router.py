# Router for ComponentType and its nested tag-link sub-resources.
#
# ComponentType is a catalogue entry that answers "what kind of component is this?"
# e.g. "VFD", "Motor", "Circulation Pump", "Temperature Sensor".  It is scoped
# per company so each tenant maintains their own catalogue.  Physical component
# instances reference a ComponentType via MachineComponentInstance.component_type_id.
#
# Sub-resource endpoints on this router:
#   POST /component-types/{id}/tags  — link one or more TagDefinitions to a ComponentType
#   GET  /component-types/{id}/tags  — list all TagDefinitions linked to a ComponentType
#
# Phase 4d changes:
#   - All endpoints now require a valid JWT (get_tenant_db enforces this).
#   - GET /component-types filters by the authenticated user's company_id.
#   - GET /component-types/{id}/tags filters the junction table by company_id
#     so tenants cannot read each other's tag links.

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from auth import get_current_user, get_tenant_db
from schemas.component_type import ComponentTypeCreate, ComponentTypeResponse
from schemas.component_type_tag import (
    ComponentTypeTagBatchCreate,
    ComponentTypeTagBatchResult,
    ComponentTypeTagResponse,
)
from models import ComponentType, ComponentTypeTag


router = APIRouter()


# ── Component type CRUD ──────────────────────────────────────────────────────

@router.post("/component-types", response_model=ComponentTypeResponse, status_code=201)
def create_component_type(
    component_type: ComponentTypeCreate,
    db: Session = Depends(get_tenant_db),
):
    db_ct = ComponentType(
        name=component_type.name,
        description=component_type.description,
        company_id=component_type.company_id,
    )

    db.add(db_ct)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning — clears the poisoned transaction so the
        # session stays usable for the next request.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create component type — check that company_id exists.",
        )

    db.refresh(db_ct)
    return db_ct


@router.get("/component-types", response_model=list[ComponentTypeResponse])
def get_component_types(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # WHERE filter scopes results to the authenticated tenant.
    return (
        db.query(ComponentType)
        .filter(ComponentType.company_id == current_user["company_id"])
        .all()
    )


# ── Tag-link sub-resources ───────────────────────────────────────────────────

@router.post(
    "/component-types/{component_type_id}/tags",
    response_model=ComponentTypeTagBatchResult,
    status_code=201,
)
def link_tags_to_component_type(
    component_type_id: int,
    payload: ComponentTypeTagBatchCreate,
    db: Session = Depends(get_tenant_db),
):
    # Verify the parent component type exists before touching junction rows.
    # db.get() is a primary-key lookup — fastest possible read.
    ct = db.get(ComponentType, component_type_id)
    if ct is None:
        raise HTTPException(status_code=404, detail="Component type not found.")

    # TODO Phase 5: verify every tag_definition_id in payload.tag_definition_ids
    # belongs to payload.company_id (cross-tenant guard).  Without this check a
    # caller could link a tag from a different tenant's catalogue.

    created = []
    skipped = []

    for tag_id in payload.tag_definition_ids:
        # Idempotency: check for a pre-existing link before attempting INSERT.
        # Cheaper than catching an IntegrityError from a UNIQUE violation and
        # avoids an unnecessary transaction abort per tag.
        existing = (
            db.query(ComponentTypeTag)
            .filter(
                ComponentTypeTag.component_type_id == component_type_id,
                ComponentTypeTag.tag_definition_id == tag_id,
                ComponentTypeTag.company_id == payload.company_id,
            )
            .first()
        )

        if existing:
            # Already linked — add to skipped and move on.
            skipped.append(ComponentTypeTagResponse.model_validate(existing))
            continue

        link = ComponentTypeTag(
            component_type_id=component_type_id,
            tag_definition_id=tag_id,
            company_id=payload.company_id,
        )
        db.add(link)

        try:
            db.commit()
        except IntegrityError:
            # Roll back before continuing so the session stays clean for the
            # remaining tags in this batch.  Most likely cause: tag_definition_id
            # does not exist, or company_id is wrong.
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=(
                    "Could not link tag_definition_id={} — check that it exists "
                    "and belongs to company_id={}.".format(tag_id, payload.company_id)
                ),
            )

        db.refresh(link)
        created.append(ComponentTypeTagResponse.model_validate(link))

    # Always 201 — consistent regardless of how many tags were new vs skipped.
    # The caller sent a creation request; 201 confirms it was processed.
    return ComponentTypeTagBatchResult(created=created, skipped=skipped)


@router.get(
    "/component-types/{component_type_id}/tags",
    response_model=list[ComponentTypeTagResponse],
)
def get_tags_for_component_type(
    component_type_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_tenant_db),
):
    # Verify the component type exists so callers get a clear 404 rather than
    # an empty list when they pass a wrong ID.
    ct = db.get(ComponentType, component_type_id)
    if ct is None:
        raise HTTPException(status_code=404, detail="Component type not found.")

    return (
        db.query(ComponentTypeTag)
        .filter(
            ComponentTypeTag.component_type_id == component_type_id,
            # company_id filter ensures tenants can only read their own links.
            ComponentTypeTag.company_id == current_user["company_id"],
        )
        .all()
    )
