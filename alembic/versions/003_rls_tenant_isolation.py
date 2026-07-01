"""003 RLS tenant isolation

Adds Row-Level Security (RLS) policies to tenant-scoped tables.  Each policy
reads the current company_id from the PostgreSQL session variable
app.current_company_id, which the get_tenant_db() dependency (auth.py) sets
at the start of every authenticated request.

All tables covered by this migration have a direct company_id column, so the
policy USING clause is a simple equality check.

EXCLUDED: telemetry_data
    telemetry_data is a TimescaleDB hypertable with columnstore compression
    enabled.  PostgreSQL RLS cannot be applied directly to compressed
    hypertables (TimescaleDB raises NotSupportedError on ALTER TABLE ...
    ENABLE ROW LEVEL SECURITY for such tables).  Tenant isolation for
    telemetry_data is enforced at the application layer via WHERE filter in
    data_router.py.  Hypertable-level RLS is deferred to a future phase when
    a dedicated app role is introduced and TimescaleDB adds support.

IMPORTANT — current development limitation:
    The FastAPI application connects to PostgreSQL as POSTGRES_USER, which is
    created as a superuser by the official timescaledb Docker image.
    PostgreSQL superusers bypass RLS unconditionally — FORCE ROW LEVEL SECURITY
    only affects table owners, not superusers.  These policies therefore have no
    enforcement effect in the current Docker Compose development environment.

    They are in place and correct; full enforcement is activated in Phase 5 when
    a dedicated non-superuser application role is introduced and DATABASE_URL is
    updated to use it.

    In the meantime, application-layer WHERE company_id filters in the routers
    (added in Phase 4d) provide the actual multi-tenant isolation.

The session variable uses NULLIF(..., '') to handle the case where
current_setting() returns an empty string (when SET LOCAL has not been called
for this transaction).  Casting '' to integer would raise an error; returning
NULL causes the policy to yield false (no rows), which is the safe default.

Revision ID: a1b2c3d4e5f6
Revises: 7c4d2e1f8a93
Create Date: 2026-07-01
"""

from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = '7c4d2e1f8a93'
branch_labels = None
depends_on = None

# Tables with a direct company_id column where RLS is supported.
# telemetry_data is intentionally excluded — it is a TimescaleDB hypertable
# with columnstore compression, which is incompatible with ALTER TABLE ...
# ENABLE ROW LEVEL SECURITY.  See module docstring for details.
_RLS_TABLES = [
    "department",
    "machine",
    "machine_type",
    "component_type",
    "tag_definition",
    "component_type_tag",
    "machine_component_instance",
]


def upgrade():
    for table in _RLS_TABLES:
        # ENABLE ROW LEVEL SECURITY — turns RLS on for this table.
        # Without FORCE, table owners (but not superusers) still see all rows.
        op.execute("ALTER TABLE {} ENABLE ROW LEVEL SECURITY;".format(table))

        # FORCE ROW LEVEL SECURITY — makes RLS apply to table owners too.
        # Superusers are still exempt (PostgreSQL design — Phase 5 switches the
        # app to a non-superuser role so this setting starts having effect).
        op.execute("ALTER TABLE {} FORCE ROW LEVEL SECURITY;".format(table))

        # CREATE POLICY — the actual filter applied to every SELECT, INSERT,
        # UPDATE, DELETE that the app performs on this table.
        #
        # NULLIF(current_setting(..., TRUE), '') handles two edge cases:
        #   - missing_ok=TRUE returns '' if the variable was never SET LOCAL
        #     (rather than raising an error).
        #   - NULLIF converts '' to NULL so the ::integer cast does not fail.
        # When the cast produces NULL (variable not set), the USING clause
        # evaluates to NULL which is treated as false — zero rows visible.
        op.execute("""
            CREATE POLICY tenant_isolation ON {table}
            USING (
                company_id = NULLIF(
                    current_setting('app.current_company_id', TRUE), ''
                )::integer
            );
        """.format(table=table))


def downgrade():
    # Reverse order so we drop policies before disabling RLS (safer).
    for table in reversed(_RLS_TABLES):
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON {};".format(table))
        op.execute("ALTER TABLE {} DISABLE ROW LEVEL SECURITY;".format(table))
