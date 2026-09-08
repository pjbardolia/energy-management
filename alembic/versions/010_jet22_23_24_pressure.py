"""010 jet22 23 24 pressure — new pressure transmitters on Jet 27's AI8CH

Jet 27's existing Waveshare AI8CH module (slave_id=2, bus /dev/ttyUSB0) is
being reused again — same module that already carries Jet 27 (CH1), Jet 25
(CH2), and Jet 26 (CH3, migration 008) — to also carry Jet 22, Jet 23, and
Jet 24's new pressure transmitters:
    AI4 (channel 4, register 0x0003) -> Jet 24
    AI5 (channel 5, register 0x0004) -> Jet 23
    AI6 (channel 6, register 0x0005) -> Jet 22
No gateway_service.py change is needed — read_ai8ch_pressure()'s channel
parameter (added for Jet 25/26 in migration 008's cycle) already handles any
channel generically; only config.json needs new device entries.

No new component_type or tag_definition rows: reuses the exact same
"Pressure Transmitter" component_type (id=3) and "pressure" tag_definition
(id=9) that Jet 27/25/26 already use — component_type_tag already links
component_type_id=3 -> tag_definition_id=9, so no new mapping row is needed
either.

IDs used below were all confirmed against the live production DB via
SELECT MAX(id) before this migration was written:
    machine.id (Jet 22)                = 24  (scripts/seed_new_machines.sql)
    machine.id (Jet 23)                = 25  (scripts/seed_new_machines.sql)
    machine.id (Jet 24)                = 26  (scripts/seed_new_machines.sql)
    component_type.id (Pressure Transmitter) = 3
    machine_component_instance.id      = 35, 36, 37  (max was 34, Jet 12 flowmeter)

Revision ID: 010
Revises: 009
Create Date: 2026-09-08
"""

from alembic import op

revision      = '010'
down_revision = '009'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- New machine component instances: Jet 22, Jet 23, and Jet 24
        -- pressure transmitters, reusing the existing Pressure Transmitter
        -- component_type (id=3) — same type as Jet 27/25/26's.
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES
            (35, 'Jet 22 Pressure Transmitter', 3, 24, 1),
            (36, 'Jet 23 Pressure Transmitter', 3, 25, 1),
            (37, 'Jet 24 Pressure Transmitter', 3, 26, 1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance the sequence past the highest ID just inserted, same
        -- pattern as every prior migration/seed script in this project.
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 37) FROM machine_component_instance));
    """)


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance WHERE id IN (35, 36, 37);
    """)
