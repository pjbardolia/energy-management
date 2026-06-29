"""Initial schema — all tables as they exist at end of Phase 2/3.

Revision ID: 3f8a1c2e9b47
Revises: 
Create Date: 2026-06-29
"""
from alembic import op

revision = "3f8a1c2e9b47"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tag_data_type AS ENUM ('float', 'int', 'bool', 'text');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;

        CREATE TABLE IF NOT EXISTS company (
            id SERIAL PRIMARY KEY,
            company_name VARCHAR NOT NULL,
            address VARCHAR
        );
        CREATE INDEX IF NOT EXISTS ix_company_id ON company (id);

        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR NOT NULL UNIQUE,
            password_hash VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_users_id ON users (id);
        CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);

        CREATE TABLE IF NOT EXISTS department (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            description VARCHAR,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_department_id ON department (id);

        CREATE TABLE IF NOT EXISTS machine_type (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            description VARCHAR,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_machine_type_id ON machine_type (id);

        CREATE TABLE IF NOT EXISTS machine (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            machine_type_id INTEGER NOT NULL REFERENCES machine_type(id),
            description VARCHAR,
            company_id INTEGER NOT NULL REFERENCES company(id),
            department_id INTEGER NOT NULL REFERENCES department(id)
        );
        CREATE INDEX IF NOT EXISTS ix_machine_id ON machine (id);

        CREATE TABLE IF NOT EXISTS component_type (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            description VARCHAR,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_component_type_id ON component_type (id);

        CREATE TABLE IF NOT EXISTS machine_component_instance (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            component_type_id INTEGER NOT NULL REFERENCES component_type(id),
            machine_id INTEGER NOT NULL REFERENCES machine(id),
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_machine_component_instance_id 
            ON machine_component_instance (id);

        CREATE TABLE IF NOT EXISTS tag_definition (
            id SERIAL PRIMARY KEY,
            name VARCHAR NOT NULL,
            unit VARCHAR,
            description VARCHAR,
            data_type tag_data_type NOT NULL,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_tag_definition_id ON tag_definition (id);

        CREATE TABLE IF NOT EXISTS component_type_tag (
            id SERIAL PRIMARY KEY,
            component_type_id INTEGER NOT NULL REFERENCES component_type(id),
            tag_definition_id INTEGER NOT NULL REFERENCES tag_definition(id),
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        CREATE INDEX IF NOT EXISTS ix_component_type_tag_id ON component_type_tag (id);

        CREATE TABLE IF NOT EXISTS telemetry_data (
            id SERIAL NOT NULL,
            timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            component_instance_id INTEGER NOT NULL 
                REFERENCES machine_component_instance(id),
            tag_definition_id INTEGER NOT NULL REFERENCES tag_definition(id),
            value_num DOUBLE PRECISION,
            value_text TEXT,
            company_id INTEGER NOT NULL REFERENCES company(id)
        );
        ALTER TABLE telemetry_data ADD PRIMARY KEY (id);
        CREATE INDEX IF NOT EXISTS ix_telemetry_data_id ON telemetry_data (id);
        CREATE INDEX IF NOT EXISTS ix_telemetry_data_timestamp 
            ON telemetry_data (timestamp);
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS telemetry_data;
        DROP TABLE IF EXISTS component_type_tag;
        DROP TABLE IF EXISTS tag_definition;
        DROP TABLE IF EXISTS machine_component_instance;
        DROP TABLE IF EXISTS component_type;
        DROP TABLE IF EXISTS machine;
        DROP TABLE IF EXISTS machine_type;
        DROP TABLE IF EXISTS department;
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS company;
        DROP TYPE IF EXISTS tag_data_type;
    """)
