"""004 Tag key slug

Adds a 'key' column to tag_definition.  The key is a stable, machine-readable
slug (e.g. "frequency", "dc_voltage") that is the API/gateway/frontend contract.
The existing 'name' column remains the human-editable display label.

Why a separate column rather than deriving the slug on the fly from 'name'?
Because operators need to be able to rename "Output Frequency" to "Frequency (Hz)"
in the UI without breaking gateway polling code or frontend charts.  The key is
written once on creation and never changed.  ADR-010 covers this decision.

Backfill strategy:
    The seven gateway-contract tags are mapped explicitly by their current 'name'
    values — no regex derivation.  These names are the contract: if a name ever
    changed, add the old name to the CASE list below.
    An ELSE clause provides a simple fallback (spaces → underscores, lowercase)
    for any tag not in the explicit list.

Unique constraint:
    (company_id, key) — slugs are unique per tenant, not globally, so two
    tenants may both define a "frequency" tag with different IDs.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-11
"""

from alembic import op

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Step 1 — add column nullable first so the backfill can run before
    # we impose the NOT NULL constraint.
    op.execute("ALTER TABLE tag_definition ADD COLUMN key VARCHAR;")

    # Step 2 — backfill.
    # The CASE list covers every known tag name that gateways or frontends
    # reference by slug.  The ELSE clause handles any operator-defined tags
    # not in the list with a simple lowercase/underscore transformation.
    # Any tag that ends up with a duplicate slug within its company will fail
    # the unique-index step below — fix it manually before running the migration.
    op.execute("""
        UPDATE tag_definition
        SET key = CASE name
            WHEN 'Rotation Speed'   THEN 'rpm'
            WHEN 'Output Torque'    THEN 'torque'
            WHEN 'Output Current'   THEN 'current'
            WHEN 'DC Bus Voltage'   THEN 'dc_voltage'
            WHEN 'Output Voltage'   THEN 'output_voltage'
            WHEN 'Output Frequency' THEN 'frequency'
            WHEN 'Output Power'     THEN 'power'
            WHEN 'Temperature'      THEN 'temperature'
            WHEN 'Pressure'         THEN 'pressure'
            WHEN 'Fault Code'       THEN 'fault_code'
            ELSE lower(replace(name, ' ', '_'))
        END;
    """)

    # Step 3 — now that every row has a value, enforce NOT NULL.
    op.execute("ALTER TABLE tag_definition ALTER COLUMN key SET NOT NULL;")

    # Step 4 — unique index per (company, key) so two tags in the same company
    # cannot share a slug.  Two different companies may use the same slug.
    op.execute("""
        CREATE UNIQUE INDEX uq_tag_definition_company_key
            ON tag_definition (company_id, key);
    """)


def downgrade():
    # Drop the unique index first (before removing the column), then the column.
    op.execute("DROP INDEX IF EXISTS uq_tag_definition_company_key;")
    op.execute("ALTER TABLE tag_definition DROP COLUMN IF EXISTS key;")
