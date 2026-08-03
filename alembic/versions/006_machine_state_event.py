"""006 machine_state_event

Adds the machine_state_event table — a dedicated event log of running/stopped
transitions per machine, written only when a transition actually occurs (not
one row per poll). This powers the Gantt timeline, heatmap calendar, and OEE
availability cards without re-scanning raw telemetry_data on every page view.

RUNNING vs STOPPED uses the exact same threshold already used everywhere else
in this codebase: frequency (tag_definition_id=6) > 0. See
routers/runtime.py::FREQUENCY_TAG_ID and the Fleet tile's getMachineState()
in frontend/src/App.jsx — this table does not introduce a second definition.

RLS: mirrors migration 003 exactly (same tenant_isolation policy pattern,
same current_setting('app.current_company_id', TRUE) session variable).
See 003_rls_tenant_isolation.py's module docstring for the current-limitation
note (application-layer WHERE filters do the real enforcement until Phase 5
introduces a non-superuser app role).

Revision ID: 006
Revises: 005
Create Date: 2026-08-05
"""

from alembic import op

revision      = '006'
down_revision = '005'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE machine_state AS ENUM ('running', 'stopped');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        CREATE TABLE IF NOT EXISTS machine_state_event (
            id              SERIAL PRIMARY KEY,
            machine_id      INTEGER NOT NULL REFERENCES machine(id),
            company_id      INTEGER NOT NULL REFERENCES company(id),
            state           machine_state NOT NULL,
            started_at      TIMESTAMPTZ NOT NULL,
            ended_at        TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS ix_machine_state_event_machine_time
            ON machine_state_event (machine_id, started_at);
    """)

    # Same tenant_isolation RLS pattern as every other table with a direct
    # company_id column (migration 003) — mirrored exactly, not improvised.
    op.execute("ALTER TABLE machine_state_event ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE machine_state_event FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON machine_state_event
        USING (
            company_id = NULLIF(
                current_setting('app.current_company_id', TRUE), ''
            )::integer
        );
    """)


def downgrade():
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON machine_state_event;")
    op.execute("ALTER TABLE machine_state_event DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP TABLE IF EXISTS machine_state_event;")
    op.execute("DROP TYPE IF EXISTS machine_state;")
