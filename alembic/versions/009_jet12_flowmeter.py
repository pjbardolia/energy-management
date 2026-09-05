"""009 jet12 flowmeter — new machine + flowmeter for Jet 12

Jet 12 is confirmed (via live query — SELECT id, name FROM machine WHERE
name = 'Jet 12' returned 0 rows) to not exist anywhere in the current
machine table — this is a genuinely new machine row, same situation
migration 007 handled for Jet 11.

Unlike migration 007, no new component_type or tag_definition rows are
needed here: Jet 12's flowmeter (Arrowmech AEFM-100, register 1280, uint32
low-word-first — see gateway/gateway_service.py's _ARROWMECH_AEFM100_SPEC)
reuses the exact same "Flowmeter" component_type (id=4) and "flow_totalizer"
tag_definition (id=11) that Jet 11's flowmeter already uses.
component_type_tag already links component_type_id=4 ->
tag_definition_id=11 from migration 007, so no new mapping row is needed
either. flow_instantaneous is not wired up for Jet 12 (not tested at any
address on this device, same as Jet 11's RTU spec), so no use of
tag_definition_id=10 here.

IDs used below were all confirmed against the live production DB via
SELECT MAX(id) before this migration was written:
    machine.id                    = 29  (max was 28, Jet 11)
    machine_component_instance.id = 34  (max was 33, Jet 26 Pressure Transmitter)
    component_type_id             = 4   (Flowmeter, unchanged from migration 007)
    tag_definition_id             = 11  (flow_totalizer, unchanged from migration 007)

Revision ID: 009
Revises: 008
Create Date: 2026-09-05
"""

from alembic import op

revision      = '009'
down_revision = '008'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- New machine: Jet 12 (confirmed not a duplicate of any existing jet
        -- via live query before this migration was written).
        INSERT INTO machine (id, name, machine_type_id, description, company_id, department_id)
        VALUES (29, 'Jet 12', 1,
                'Soft-flow jet dyeing machine — Arrowmech AEFM-100 flowmeter on '
                'dedicated /dev/ttyUSB2 bus (shared with Jet 11), slave ID 2',
                1, 1)
        ON CONFLICT (id) DO UPDATE SET
            name            = EXCLUDED.name,
            machine_type_id = EXCLUDED.machine_type_id,
            description     = EXCLUDED.description,
            company_id      = EXCLUDED.company_id,
            department_id   = EXCLUDED.department_id;

        -- Machine component instance: the flowmeter itself, reusing the
        -- existing Flowmeter component_type (id=4) from migration 007.
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES (34, 'Jet 12 Flowmeter', 4, 29, 1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance sequences past the highest IDs just inserted, same pattern
        -- as every prior migration/seed script in this project.
        SELECT setval('machine_id_seq',
            (SELECT COALESCE(MAX(id), 29) FROM machine));
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 34) FROM machine_component_instance));
    """)


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance WHERE id = 34;

        DELETE FROM machine WHERE id = 29;
    """)
