"""Initial schema — all tables as they exist at end of Phase 2/3.

This migration captures the full normalized schema:
  company → department, machine_type, machine, component_type,
  machine_component_instance, tag_definition, component_type_tag,
  telemetry_data, users

It is the "baseline" migration.  All future schema changes will be new
migration files that layer on top of this one.

Revision ID: 3f8a1c2e9b47
Revises: (none — this is the first migration)
Create Date: 2026-06-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic
revision: str = "3f8a1c2e9b47"
down_revision: Union[str, None] = None   # first migration — no parent
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 0. PostgreSQL ENUM type for TagDefinition.data_type
    #
    # Must be created before the tag_definition table because the column
    # references this type by name.  sa.Enum with create_constraint=True
    # (the default) issues a CREATE TYPE … AS ENUM statement.
    # ------------------------------------------------------------------
    tag_data_type_enum = sa.Enum(
        "float", "int", "bool", "text",
        name="tag_data_type"   # PostgreSQL type name in the DB
    )
    tag_data_type_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 1. company — tenant root; no foreign keys
    # ------------------------------------------------------------------
    op.create_table(
        "company",
        sa.Column("id",           sa.Integer(),   primary_key=True),
        sa.Column("company_name", sa.String(),    nullable=False),
        sa.Column("address",      sa.String(),    nullable=True),
    )
    # SQLAlchemy adds ix_{table}_{col} when index=True is set on a column.
    op.create_index("ix_company_id", "company", ["id"])

    # ------------------------------------------------------------------
    # 2. users — belongs to a company
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id",            sa.Integer(),  primary_key=True),
        sa.Column("username",      sa.String(),   nullable=False, unique=True),
        sa.Column("password_hash", sa.String(),   nullable=False),
        sa.Column("role",          sa.String(),   nullable=False),
        sa.Column("company_id",    sa.Integer(),  sa.ForeignKey("company.id"), nullable=False),
    )
    op.create_index("ix_users_id",       "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ------------------------------------------------------------------
    # 3. department — organisational unit within a company
    # ------------------------------------------------------------------
    op.create_table(
        "department",
        sa.Column("id",          sa.Integer(), primary_key=True),
        sa.Column("name",        sa.String(),  nullable=False),
        sa.Column("description", sa.String(),  nullable=True),
        sa.Column("company_id",  sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
    )
    op.create_index("ix_department_id", "department", ["id"])

    # ------------------------------------------------------------------
    # 4. machine_type — reusable catalogue of machine kinds per company
    # (e.g. "Stenter", "Jigger").  No department_id — see ADR Decision 3.
    # ------------------------------------------------------------------
    op.create_table(
        "machine_type",
        sa.Column("id",          sa.Integer(), primary_key=True),
        sa.Column("name",        sa.String(),  nullable=False),
        sa.Column("description", sa.String(),  nullable=True),
        sa.Column("company_id",  sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
    )
    op.create_index("ix_machine_type_id", "machine_type", ["id"])

    # ------------------------------------------------------------------
    # 5. machine — one physical machine on the factory floor
    # ------------------------------------------------------------------
    op.create_table(
        "machine",
        sa.Column("id",              sa.Integer(), primary_key=True),
        sa.Column("name",            sa.String(),  nullable=False),
        sa.Column("machine_type_id", sa.Integer(), sa.ForeignKey("machine_type.id"), nullable=False),
        sa.Column("description",     sa.String(),  nullable=True),
        sa.Column("company_id",      sa.Integer(), sa.ForeignKey("company.id"),      nullable=False),
        sa.Column("department_id",   sa.Integer(), sa.ForeignKey("department.id"),   nullable=False),
    )
    op.create_index("ix_machine_id", "machine", ["id"])

    # ------------------------------------------------------------------
    # 6. component_type — reusable catalogue of component kinds per company
    # (e.g. "VFD", "Motor", "Circulation Pump")
    # ------------------------------------------------------------------
    op.create_table(
        "component_type",
        sa.Column("id",          sa.Integer(), primary_key=True),
        sa.Column("name",        sa.String(),  nullable=False),
        sa.Column("description", sa.String(),  nullable=True),
        sa.Column("company_id",  sa.Integer(), sa.ForeignKey("company.id"), nullable=False),
    )
    op.create_index("ix_component_type_id", "component_type", ["id"])

    # ------------------------------------------------------------------
    # 7. machine_component_instance — one physical component attached to
    # a specific machine (e.g. "Reel Motor on Jet 33")
    # ------------------------------------------------------------------
    op.create_table(
        "machine_component_instance",
        sa.Column("id",                sa.Integer(), primary_key=True),
        sa.Column("name",              sa.String(),  nullable=False),
        sa.Column("component_type_id", sa.Integer(), sa.ForeignKey("component_type.id"), nullable=False),
        sa.Column("machine_id",        sa.Integer(), sa.ForeignKey("machine.id"),        nullable=False),
        sa.Column("company_id",        sa.Integer(), sa.ForeignKey("company.id"),         nullable=False),
    )
    op.create_index("ix_machine_component_instance_id", "machine_component_instance", ["id"])

    # ------------------------------------------------------------------
    # 8. tag_definition — a named measurement type (e.g. "Output Frequency",
    # unit "Hz", data_type "float").  References the tag_data_type enum.
    # ------------------------------------------------------------------
    op.create_table(
        "tag_definition",
        sa.Column("id",          sa.Integer(),                                nullable=False,  primary_key=True),
        sa.Column("name",        sa.String(),                                 nullable=False),
        sa.Column("unit",        sa.String(),                                 nullable=True),
        sa.Column("description", sa.String(),                                 nullable=True),
        sa.Column("data_type",   sa.Enum("float", "int", "bool", "text",
                                         name="tag_data_type"),               nullable=False),
        sa.Column("company_id",  sa.Integer(), sa.ForeignKey("company.id"),   nullable=False),
    )
    op.create_index("ix_tag_definition_id", "tag_definition", ["id"])

    # ------------------------------------------------------------------
    # 9. component_type_tag — junction: which tags does a component type
    # produce?  (e.g. VFD → Output Frequency, DC Bus Voltage, …)
    # ------------------------------------------------------------------
    op.create_table(
        "component_type_tag",
        sa.Column("id",                sa.Integer(), primary_key=True),
        sa.Column("component_type_id", sa.Integer(), sa.ForeignKey("component_type.id"),  nullable=False),
        sa.Column("tag_definition_id", sa.Integer(), sa.ForeignKey("tag_definition.id"),  nullable=False),
        sa.Column("company_id",        sa.Integer(), sa.ForeignKey("company.id"),          nullable=False),
    )
    op.create_index("ix_component_type_tag_id", "component_type_tag", ["id"])

    # ------------------------------------------------------------------
    # 10. telemetry_data — one sensor reading (component + tag + timestamp
    # + value).  See ADR Decision 2 for the two-column value design.
    #
    # Phase 4c note: this table will be converted to a TimescaleDB hypertable.
    # At that point:
    #   - FK constraints on component_instance_id, tag_definition_id, company_id
    #     are dropped (TimescaleDB does not allow FKs on hypertables).
    #   - The single-column PK on id becomes composite (id, timestamp).
    # Those changes arrive in migration 002_timescale_hypertable.py.
    # ------------------------------------------------------------------
    op.create_table(
        "telemetry_data",
        sa.Column("id",                   sa.Integer(),  primary_key=True),
        sa.Column("timestamp",            sa.DateTime(), nullable=False),
        sa.Column("component_instance_id",sa.Integer(), sa.ForeignKey("machine_component_instance.id"), nullable=False),
        sa.Column("tag_definition_id",    sa.Integer(), sa.ForeignKey("tag_definition.id"),             nullable=False),
        sa.Column("value_num",            sa.Float(),    nullable=True),
        sa.Column("value_text",           sa.Text(),     nullable=True),
        sa.Column("company_id",           sa.Integer(), sa.ForeignKey("company.id"),                    nullable=False),
    )
    op.create_index("ix_telemetry_data_id",        "telemetry_data", ["id"])
    op.create_index("ix_telemetry_data_timestamp", "telemetry_data", ["timestamp"])


def downgrade() -> None:
    # Drop in reverse dependency order so FK constraints don't block the drops.
    op.drop_table("telemetry_data")
    op.drop_table("component_type_tag")
    op.drop_table("tag_definition")
    op.drop_table("machine_component_instance")
    op.drop_table("component_type")
    op.drop_table("machine")
    op.drop_table("machine_type")
    op.drop_table("department")
    op.drop_table("users")
    op.drop_table("company")

    # Drop the PostgreSQL ENUM type last — it has no dependents once the
    # tag_definition table is gone.
    sa.Enum(name="tag_data_type").drop(op.get_bind(), checkfirst=True)
