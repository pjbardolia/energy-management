"""Add gateway_heartbeat table

Revision ID: 005
Revises: 004
Create Date: 2026-07-14
"""

from alembic import op

revision      = '005'
down_revision = '004'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        CREATE TABLE gateway_heartbeat (
            id                SERIAL PRIMARY KEY,
            company_id        INTEGER NOT NULL,
            last_seen         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            poll_duration_sec FLOAT,
            machines_polled   INTEGER,
            machines_failed   INTEGER,
            CONSTRAINT uq_gateway_heartbeat_company UNIQUE (company_id)
        );
        CREATE INDEX ix_gateway_heartbeat_company_id ON gateway_heartbeat (company_id);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS gateway_heartbeat;")
