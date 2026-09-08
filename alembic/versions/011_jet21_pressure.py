"""011 jet21 pressure — new pressure transmitter on Jet 27's AI8CH

Jet 27's existing Waveshare AI8CH module (slave_id=2, bus /dev/ttyUSB0) is
being reused again — same module that already carries Jet 27 (CH1), Jet 25
(CH2), Jet 26 (CH3, migration 008), and Jet 24/23/22 (CH4/5/6, migration
010) — to also carry Jet 21's new pressure transmitter:
    AI7 (channel 7, register 0x0006) -> Jet 21
No gateway_service.py change is needed — read_ai8ch_pressure()'s channel
parameter already handles any channel generically; only config.json needs
a new device entry.

No new component_type or tag_definition rows: reuses the exact same
"Pressure Transmitter" component_type (id=3) and "pressure" tag_definition
(id=9) that Jet 27/25/26/24/23/22 already use — component_type_tag already
links component_type_id=3 -> tag_definition_id=9, so no new mapping row is
needed either.

IDs used below were all confirmed against the live production DB via
SELECT MAX(id) before this migration was written:
    machine.id (Jet 21)                = 12  (scripts/seed_ssppl.sql)
    component_type.id (Pressure Transmitter) = 3
    machine_component_instance.id      = 38  (max was 37, Jet 24 pressure)

Revision ID: 011
Revises: 010
Create Date: 2026-09-09
"""

from alembic import op

revision      = '011'
down_revision = '010'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- New machine component instance: Jet 21 pressure transmitter,
        -- reusing the existing Pressure Transmitter component_type (id=3)
        -- — same type as Jet 27/25/26/24/23/22's.
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES (38, 'Jet 21 Pressure Transmitter', 3, 12, 1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance the sequence past the highest ID just inserted, same
        -- pattern as every prior migration/seed script in this project.
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 38) FROM machine_component_instance));
    """)


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance WHERE id = 38;
    """)
