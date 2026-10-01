"""012 jet28 33 pressure — six pressure transmitters on a second AI8CH module

A second physical Waveshare AI8CH module, same bus (/dev/ttyUSB0) as
everything else, configured at slave_id=3 (confirmed via direct Modbus
query, not assumed — avoids colliding with Jet 27 Temp at slave_id=1 and
the original pressure module at slave_id=2). Six channels wired, confirmed
from physical wiring:
    AI1 -> Jet 28, AI2 -> Jet 29, AI3 -> Jet 30,
    AI4 -> Jet 31, AI5 -> Jet 32, AI6 -> Jet 33
(AI7, AI8 unused/spare on this module.) No gateway_service.py change —
read_ai8ch_pressure()'s channel/slave_id parameters already handle this
generically.

Unlike Jet 12, all six machines already exist (confirmed live — SELECT id,
name FROM machine WHERE name IN (...) returned exactly these six rows, no
drift from the seed scripts): Jet 28=15, Jet 29=16, Jet 30=18, Jet 31=17,
Jet 32=4, Jet 33=3 (scripts/seed_ssppl.sql and scripts/seed_new_machines.sql).
So only new machine_component_instance rows are needed here, not new
machine rows.

No new component_type or tag_definition rows: reuses the exact same
"Pressure Transmitter" component_type (id=3) and "pressure" tag_definition
(id=9) that every prior pressure transmitter in this project already uses
— component_type_tag already links component_type_id=3 -> tag_definition_id=9,
so no new mapping row is needed either.

IDs used below were all confirmed against the live production DB before
this migration was written:
    machine.id (Jet 28/29/30/31/32/33) = 15/16/18/17/4/3
    component_type.id (Pressure Transmitter) = 3
    machine_component_instance.id      = 39-44  (max was 38, Jet 21 pressure)

Revision ID: 012
Revises: 011
Create Date: 2026-10-01
"""

from alembic import op

revision      = '012'
down_revision = '011'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- New machine component instances: Jet 28-33 pressure transmitters,
        -- reusing the existing Pressure Transmitter component_type (id=3).
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES
            (39, 'Jet 28 Pressure Transmitter', 3, 15, 1),
            (40, 'Jet 29 Pressure Transmitter', 3, 16, 1),
            (41, 'Jet 30 Pressure Transmitter', 3, 18, 1),
            (42, 'Jet 31 Pressure Transmitter', 3, 17, 1),
            (43, 'Jet 32 Pressure Transmitter', 3, 4,  1),
            (44, 'Jet 33 Pressure Transmitter', 3, 3,  1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance the sequence past the highest ID just inserted, same
        -- pattern as every prior migration/seed script in this project.
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 44) FROM machine_component_instance));
    """)


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance WHERE id IN (39, 40, 41, 42, 43, 44);
    """)
