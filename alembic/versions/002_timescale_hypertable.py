"""Convert telemetry_data to a TimescaleDB hypertable.

Steps (in order):
  1. Enable the TimescaleDB extension.
  2. Drop the three FK constraints on telemetry_data — TimescaleDB cannot
     convert a table that has outgoing FK constraints.  The columns stay as
     plain INTEGER NOT NULL (values preserved, DB enforcement removed).
  3. Replace the single-column PK (id) with a composite PK (id, timestamp) —
     TimescaleDB requires the partition column to appear in the primary key.
  4. Call create_hypertable() to partition by timestamp.
  5. Enable columnar compression with a 7-day policy.
     compress_segmentby='company_id' keeps each tenant's data in its own
     compressed segment so one tenant's query never decompresses another's.

See ADR Decision 1 in docs/architecture-decisions.md for the rationale.

Revision ID: 7c4d2e1f8a93
Revises: 3f8a1c2e9b47
Create Date: 2026-06-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "7c4d2e1f8a93"
down_revision: Union[str, None] = "3f8a1c2e9b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Enable TimescaleDB
    #
    # CASCADE installs any sub-extensions TimescaleDB requires.
    # IF NOT EXISTS makes this safe to re-run.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

    # ------------------------------------------------------------------
    # 2. Drop FK constraints on telemetry_data
    #
    # TimescaleDB refuses to create a hypertable on a table that holds
    # outgoing foreign key constraints.  The three constraints below were
    # created by migration 001 when sa.ForeignKey() was passed inside
    # op.create_table().  PostgreSQL names them automatically using the
    # pattern {table}_{column}_fkey.
    #
    # The columns themselves (component_instance_id, tag_definition_id,
    # company_id) remain as plain INTEGER NOT NULL — their values are still
    # used for RLS in Phase 4d.  Only the DB-level FK enforcement is removed.
    # Referential integrity is now the application's responsibility.
    # ------------------------------------------------------------------
    op.drop_constraint(
        "telemetry_data_component_instance_id_fkey",
        "telemetry_data",
        type_="foreignkey",
    )
    op.drop_constraint(
        "telemetry_data_tag_definition_id_fkey",
        "telemetry_data",
        type_="foreignkey",
    )
    op.drop_constraint(
        "telemetry_data_company_id_fkey",
        "telemetry_data",
        type_="foreignkey",
    )

    # ------------------------------------------------------------------
    # 3. Swap single-column PK → composite PK (id, timestamp)
    #
    # TimescaleDB requires the partition column (timestamp) to be part of
    # the primary key.  We keep id as the leading column so that any future
    # FK references into this table (there are none today) stay on the
    # smallest, most selective column.
    # ------------------------------------------------------------------
    op.drop_constraint("telemetry_data_pkey", "telemetry_data", type_="primary")
    op.create_primary_key("telemetry_data_pkey", "telemetry_data", ["id", "timestamp"])

    # ------------------------------------------------------------------
    # 4. Convert to a TimescaleDB hypertable partitioned by timestamp
    #
    # if_not_exists => TRUE makes this idempotent — safe to re-run if the
    # migration was interrupted after this point.
    # ------------------------------------------------------------------
    op.execute(
        "SELECT create_hypertable('telemetry_data', 'timestamp', if_not_exists => TRUE);"
    )

    # ------------------------------------------------------------------
    # 5. Enable columnar compression + 7-day policy
    #
    # compress_segmentby = 'company_id':
    #   Each compressed chunk is split by tenant, so decompressing one
    #   tenant's data never reads another tenant's rows.  Critical for
    #   multi-tenant isolation at query time.
    #
    # compress_orderby = 'timestamp DESC':
    #   Aligns with the most common access pattern — recent readings first.
    #   TimescaleDB sorts rows within a segment before compressing, so this
    #   choice directly improves range-query performance on recent data.
    #
    # add_compression_policy compresses chunks older than 7 days on a
    # background job schedule (runs automatically, no manual intervention).
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE telemetry_data
        SET (
            timescaledb.compress,
            timescaledb.compress_orderby   = 'timestamp DESC',
            timescaledb.compress_segmentby = 'company_id'
        );
        """
    )
    op.execute(
        """
        SELECT add_compression_policy(
            'telemetry_data',
            INTERVAL '7 days',
            if_not_exists => TRUE
        );
        """
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # WARNING: downgrade is a DESTRUCTIVE, DATA-LOSING operation.
    #
    # TimescaleDB has no "undo hypertable" command — the only way to revert
    # is to drop all data chunks, which deletes all telemetry rows.  This
    # downgrade path exists for completeness (e.g. on an empty dev database)
    # but MUST NOT be run on a database with real sensor data.
    # ------------------------------------------------------------------

    # Remove the compression policy first so TimescaleDB stops compressing.
    op.execute(
        "SELECT remove_compression_policy('telemetry_data', if_exists => TRUE);"
    )

    # Decompress all chunks before we can touch the table structure.
    op.execute(
        "SELECT decompress_chunk(c) FROM show_chunks('telemetry_data') c;"
    )

    # Drop all data chunks — this deletes every telemetry row.
    op.execute(
        "SELECT drop_chunks('telemetry_data', older_than => NOW() + INTERVAL '1000 years');"
    )

    # Revert composite PK back to single-column PK on id.
    op.drop_constraint("telemetry_data_pkey", "telemetry_data", type_="primary")
    op.create_primary_key("telemetry_data_pkey", "telemetry_data", ["id"])

    # Restore the three FK constraints that were dropped in upgrade().
    op.create_foreign_key(
        "telemetry_data_component_instance_id_fkey",
        "telemetry_data", "machine_component_instance",
        ["component_instance_id"], ["id"],
    )
    op.create_foreign_key(
        "telemetry_data_tag_definition_id_fkey",
        "telemetry_data", "tag_definition",
        ["tag_definition_id"], ["id"],
    )
    op.create_foreign_key(
        "telemetry_data_company_id_fkey",
        "telemetry_data", "company",
        ["company_id"], ["id"],
    )

    # Drop the TimescaleDB extension last.
    # CASCADE drops any dependent objects (continuous aggregates, etc.)
    # if they were created outside migrations.
    op.execute("DROP EXTENSION IF EXISTS timescaledb CASCADE;")
