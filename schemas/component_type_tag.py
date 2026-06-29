# Schemas for ComponentTypeTag — the junction between ComponentType and TagDefinition.
#
# ComponentTypeTag answers: "which measurement tags does a component type produce?"
# Example: a "VFD" component type declares tags for Output Frequency, DC Bus Voltage,
# Output Current, and Output Power.
#
# The endpoint shape is nested (sub-resource):
#   POST /component-types/{id}/tags  → link one or more tags to this component type
#   GET  /component-types/{id}/tags  → list all tags linked to this component type

from pydantic import BaseModel


# ── Single junction row representation (used in responses) ──────────────────

class ComponentTypeTagResponse(BaseModel):
    # Surrogate PK of the junction row
    id: int

    component_type_id: int
    tag_definition_id: int
    company_id: int

    class Config:
        from_attributes = True  # lets Pydantic read SQLAlchemy ORM objects directly


# ── Request body for POST /component-types/{id}/tags ────────────────────────

class ComponentTypeTagBatchCreate(BaseModel):
    # One or more tag_definition IDs to link to the component type in the URL.
    # Sending an ID that is already linked is safe — it is silently skipped
    # (idempotent), but the endpoint always returns 201 regardless.
    tag_definition_ids: list[int]

    # Multi-tenant isolation — must match the company that owns both the
    # component type and every tag definition being linked.
    company_id: int


# ── Response body for POST /component-types/{id}/tags ───────────────────────

class ComponentTypeTagBatchResult(BaseModel):
    # Junction rows that were newly INSERTed this request
    created: list[ComponentTypeTagResponse]

    # Junction rows that already existed — returned so the caller can see
    # the full end state without a separate GET.
    skipped: list[ComponentTypeTagResponse]
