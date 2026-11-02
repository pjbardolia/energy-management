"""013 jet21-33 temperature — twelve iTherm FX-438 dyebath temperature meters

Twelve new iTherm FX-438 temperature meters (same model as Jet 27's, component
29) on the same bus as everything else (/dev/ttyUSB0, 9600 8-N-1), one per jet:
    slave 4 -> Jet 21    slave 7 -> Jet 24    slave 10 -> Jet 28    slave 13 -> Jet 31
    slave 5 -> Jet 22    slave 8 -> Jet 25    slave 11 -> Jet 29    slave 14 -> Jet 32
    slave 6 -> Jet 23    slave 9 -> Jet 26    slave 12 -> Jet 30    slave 15 -> Jet 33
Confirmed 2026-11-02 with a read-only probe after setting each meter's PAr to
n_81 (the factory default o_81 left them silent): all 12 answer at register
138, FC03. Unlike Jet 27's meter, these send tenths of a degree (1358 = 135.8 °C).
The gateway handles that with its own model key (ITHERM_FX438_TENTHS), so the
DB side is identical to Jet 27's: same "Temperature Sensor" component_type
(id=2) and "temperature" tag (id=8), already linked by component_type_tag id=9
(scripts/seed_new_machines.sql). No new component_type, tag_definition or
component_type_tag rows.

IDs: machine_component_instance 45-56, assuming the live MAX(id) is still 44
(Jet 33 pressure, migration 012). Unlike 008-012, this migration was written
BEFORE that live check, so it checks for itself and refuses to run (raising an
error, which rolls everything back and changes nothing) if:
  - any of ids 45-56 is already used by anything other than these sensors,
  - any of the 12 machines doesn't exist exactly once for company 1,
  - component_type 2 isn't the Temperature Sensor type.
Machine ids are looked up by name rather than hard-coded. Expected values, from
scripts/seed_ssppl.sql, scripts/seed_new_machines.sql and migrations
008/010/011/012:
    Jet 21=12  Jet 22=24  Jet 23=25  Jet 24=26  Jet 25=27  Jet 26=13
    Jet 28=15  Jet 29=16  Jet 30=18  Jet 31=17  Jet 32=4   Jet 33=3

Downgrade deletes only these twelve rows (matched by id, type and name). Once
the gateway has written temperature data for them, the downgrade fails on the
telemetry_data foreign key instead of deleting data — by design.

Revision ID: 013
Revises: 012
Create Date: 2026-11-03
"""

from alembic import op

revision      = '013'
down_revision = '012'
branch_labels = None
depends_on    = None


# (machine_component_instance.id, machine name) — the slave IDs are in the
# docstring above; config.json maps each slave to these component ids.
SENSORS = [
    (45, 'Jet 21'), (46, 'Jet 22'), (47, 'Jet 23'), (48, 'Jet 24'),
    (49, 'Jet 25'), (50, 'Jet 26'), (51, 'Jet 28'), (52, 'Jet 29'),
    (53, 'Jet 30'), (54, 'Jet 31'), (55, 'Jet 32'), (56, 'Jet 33'),
]
NAME_SUFFIX = ' Dyebath Temperature Sensor'

# SQL VALUES list built from SENSORS, e.g. (45, 'Jet 21'), (46, 'Jet 22'), ...
_VALUES = ",\n            ".join("({}, '{}')".format(cid, jet) for cid, jet in SENSORS)
_ID_LIST = ", ".join(str(cid) for cid, _ in SENSORS)


def upgrade():
    op.execute("""
        DO $$
        DECLARE
            r record;
        BEGIN
            -- 1. component_type 2 must be the Temperature Sensor type.
            IF NOT EXISTS (SELECT 1 FROM component_type
                           WHERE id = 2 AND name ILIKE '%%temperature%%') THEN
                RAISE EXCEPTION 'migration 013: component_type 2 is not the Temperature Sensor type. '
                                'Check: SELECT id, name FROM component_type;';
            END IF;

            -- 2. Every jet must exist exactly once for company 1.
            FOR r IN
                SELECT v.jet,
                       (SELECT count(*) FROM machine m
                        WHERE m.name = v.jet AND m.company_id = 1) AS n
                FROM (VALUES
            {values}
                ) AS v(id, jet)
            LOOP
                IF r.n <> 1 THEN
                    RAISE EXCEPTION 'migration 013: machine "%" found % time(s) for company 1, expected exactly 1.',
                                    r.jet, r.n;
                END IF;
            END LOOP;

            -- 3. Ids 45-56 must be free, or already hold exactly these sensors
            --    (so a re-run is harmless).
            FOR r IN
                SELECT ci.id, ci.name
                FROM machine_component_instance ci
                JOIN (VALUES
            {values}
                ) AS v(id, jet) ON v.id = ci.id
                WHERE ci.name <> v.jet || '{suffix}'
                   OR ci.component_type_id <> 2
            LOOP
                RAISE EXCEPTION 'migration 013: machine_component_instance id % is already used by "%". '
                                'Run SELECT MAX(id) FROM machine_component_instance; and renumber this migration.',
                                r.id, r.name;
            END LOOP;
        END $$;

        -- New machine component instances: one Dyebath Temperature Sensor per
        -- jet, reusing the existing Temperature Sensor component_type (id=2).
        INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
        SELECT v.id, v.jet || '{suffix}', 2, m.id, 1
        FROM (VALUES
            {values}
        ) AS v(id, jet)
        JOIN machine m ON m.name = v.jet AND m.company_id = 1
        ON CONFLICT (id) DO UPDATE SET
            name              = EXCLUDED.name,
            component_type_id = EXCLUDED.component_type_id,
            machine_id        = EXCLUDED.machine_id,
            company_id        = EXCLUDED.company_id;

        -- Advance the sequence past the highest ID just inserted, same
        -- pattern as every prior migration/seed script in this project.
        SELECT setval('machine_component_instance_id_seq',
            (SELECT COALESCE(MAX(id), 56) FROM machine_component_instance));
    """.format(values=_VALUES, suffix=NAME_SUFFIX))


def downgrade():
    op.execute("""
        DELETE FROM machine_component_instance
        WHERE id IN ({ids})
          AND component_type_id = 2
          AND name LIKE 'Jet %%{suffix}';
    """.format(ids=_ID_LIST, suffix=NAME_SUFFIX))
