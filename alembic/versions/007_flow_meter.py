"""007 flow meter — Jet 11 Eureka EU1000 flowmeter

Adds the DB entities for the Jet 11 water flowmeter (Eureka EU1000, dedicated
RS485 bus on /dev/ttyUSB2, ASCII framing, slave=1 — see gateway/config.json
and gateway_service.py's EUREKA_EU1000 spec).

Jet 11 is confirmed (directly by the factory owner) to not exist anywhere in
the current machine table — this is a genuinely new machine row, not a
duplicate or a rename of an existing jet.

IDs used below were all confirmed against the live production DB via
SELECT MAX(id) before this migration was written:
    component_type.id             = 4   (max was 3, Pressure Transmitter)
    tag_definition.id             = 10, 11  (max was 9, pressure)
    machine.id                    = 28  (max was 27, Jet 25)
    machine_component_instance.id = 31  (max was 30, Jet 27 Pressure Transmitter)

component_type_tag rows are the one exception — that table's id is never
referenced anywhere outside itself (unlike machine_component_instance.id,
which is a hardcoded gateway/config.json contract), so those two link rows
are inserted without a literal id and guarded by a NOT EXISTS check instead
of ON CONFLICT (id).

Revision ID: 007
Revises: 006
Create Date: 2026-08-06
"""

from alembic import op

revision      = '007'
down_revision = '006'
branch_labels = None
depends_on    = None


def upgrade():
    op.execute("""
        -- Component type: Flowmeter
        INSERT INTO component_type (id, name, description, company_id)
        VALUES (4, 'Flowmeter', 'Magnetic flowmeter transmitter (instantaneous flow + totalizer)', 1)
        ON CONFLICT (id) DO UPDATE SET
            name        = EXCLUDED.name,
            description = EXCLUDED.description,
            company_id  = EXCLUDED.company_id;

        -- Tag definitions: instantaneous flow (m3/h) and totalizer (L)
        INSERT INTO tag_definition (id, name, key, unit, description, data_type, company_id)
        VALUES
            (10, 'Instantaneous Flow', 'flow_instantaneous', 'm3/h',
             'Live flow rate from a magnetic flowmeter transmitter', 'float', 1),
            (11, 'Water Totalizer', 'flow_totalizer', 'L',
             'Cumulative water totalizer reading (integer + fraction parts summed)', 'float', 1)
        ON CONFLICT (id) DO UPDATE SET
            name        = EXCLUDED.name,
            key         = EXCLUDED.key,
            unit        = EXCLUDED.unit,
            description = EXCLUDED.description,
            data_type   = EXCLUDED.data_type,
            company_id  = EXCLUDED.company_id;

        -- New machine: Jet 11 (confirmed not a duplicate of any existing jet)
        INSERT INTO machine (id, name, machine_type_id, description, company_id, department_id)
        VALUES (28, 'Jet 11', 1,
                'Soft-flow jet dyeing machine — Eureka EU1000 flowmeter on dedicated /dev/ttyUSB2 bus, slave ID 1',
                1, 1)
        ON CONFLICT (id) DO UPDATE SET
            name            = EXCLUDED.name,
            machine_type_id = EXCLUDED.machine_type_id,
            description     = EXCLUDED.description,
            company_id      = EXCLUDED.company_id,
            department_id   = EXCLUDED.department_id;

        -- Machine component instance: the flowmeter itself
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        VALUES (31, 'Water Flowmeter', 4, 28, 1)
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Component type -> tag links (Flowmeter produces both flow tags).
        -- No literal id here — component_type_tag.id is a plain internal
        -- surrogate key, never referenced outside this table, so a
        -- NOT EXISTS guard is used instead of ON CONFLICT (id).
        INSERT INTO component_type_tag (component_type_id, tag_definition_id, company_id)
        SELECT 4, 10, 1
        WHERE NOT EXISTS (
            SELECT 1 FROM component_type_tag
            WHERE component_type_id = 4 AND tag_definition_id = 10 AND company_id = 1
        );

        INSERT INTO component_type_tag (component_type_id, tag_definition_id, company_id)
        SELECT 4, 11, 1
        WHERE NOT EXISTS (
            SELECT 1 FROM component_type_tag
            WHERE component_type_id = 4 AND tag_definition_id = 11 AND company_id = 1
        );

        -- Advance sequences past the highest IDs just inserted, same as
        -- scripts/seed_ssppl.sql / scripts/seed_new_machines.sql — safe even
        -- if other rows exist with higher IDs (COALESCE/MAX guards against
        -- an empty-table edge case that can't actually occur here).
        SELECT setval('component_type_id_seq',
            (SELECT COALESCE(MAX(id), 4) FROM component_type));
        SELECT setval('tag_definition_id_seq',
            (SELECT COALESCE(MAX(id), 11) FROM tag_definition));
        SELECT setval('machine_id_seq',
            (SELECT COALESCE(MAX(id), 28) FROM machine));
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 31) FROM machine_component_instance));
        SELECT setval('component_type_tag_id_seq',
            (SELECT COALESCE(MAX(id), 1) FROM component_type_tag));
    """)


def downgrade():
    op.execute("""
        DELETE FROM component_type_tag
        WHERE component_type_id = 4 AND tag_definition_id IN (10, 11) AND company_id = 1;

        DELETE FROM machine_component_instance WHERE id = 31;

        DELETE FROM machine WHERE id = 28;

        DELETE FROM tag_definition WHERE id IN (10, 11);

        DELETE FROM component_type WHERE id = 4;
    """)
