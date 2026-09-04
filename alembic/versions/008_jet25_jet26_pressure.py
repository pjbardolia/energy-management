"""008 jet25 jet26 pressure — new pressure transmitters on Jet 27's AI8CH

Jet 27's existing Waveshare AI8CH module (slave_id=2, bus /dev/ttyUSB0) is
being reused to also carry Jet 25 and Jet 26's new pressure transmitters,
rather than installing a second module — see gateway/config.json's bus 2
devices list and gateway_service.py's channel-aware read_ai8ch_pressure().

Channel-to-register mapping was confirmed 2026-09-04 by physically probing
each AI8CH channel against live hardware (ai8ch_channel_scan.py) and
cross-checking against the on-site engineer's physical terminal labels:
    AI1 -> register 0x0000 -> Jet 27 (existing)
    AI2 -> register 0x0001 -> Jet 25 (new)
    AI3 -> register 0x0002 -> Jet 26 (new)
NOT assumed from a datasheet — same "verify against real hardware" method
used to find the Jet 11 flowmeter's totalizer register.

No new component_type or tag_definition rows: reuses the exact same
"Pressure Transmitter" component_type (id=3) and "pressure" tag_definition
(id=9) that Jet 27's transmitter already uses (confirmed live against
production, not assumed from migration 007's docstring) — component_type_tag
already links component_type_id=3 -> tag_definition_id=9, so no new mapping
row is needed either.

IDs used below were all confirmed against the live production DB before
this migration was written:
    machine.id (Jet 25)                = 27  (scripts/seed_new_machines.sql)
    machine.id (Jet 26)                = 13  (scripts/seed_ssppl.sql)
    component_type.id (Pressure Transmitter) = 3
    machine_component_instance.id      = 32, 33  (max was 31, Jet 11 flowmeter)

Revision ID: 008
Revises: 007
Create Date: 2026-09-04
"""

from alembic import op

revision      = '008'
down_revision = '007'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- New machine component instances: Jet 25 and Jet 26 pressure
        -- transmitters, reusing the existing Pressure Transmitter
        -- component_type (id=3) — same type as Jet 27's (id=30).
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES
            (32, 'Jet 25 Pressure Transmitter', 3, 27, 1),
            (33, 'Jet 26 Pressure Transmitter', 3, 13, 1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance the sequence past the highest ID just inserted, same
        -- pattern as every prior migration/seed script in this project.
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 33) FROM machine_component_instance));
    """)


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance WHERE id IN (32, 33);
    """)
