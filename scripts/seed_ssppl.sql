-- seed_ssppl.sql — Idempotent reseed for Shiv Shakti Prints Pvt. Ltd. (company_id = 1)
--
-- PURPOSE
--   Restore or verify the full SSPPL machine hierarchy to the exact state the
--   gateway expects.  Safe to run repeatedly — every INSERT uses ON CONFLICT DO UPDATE
--   so existing rows are updated to the canonical values rather than duplicated.
--
-- GATEWAY CONTRACT (DO NOT CHANGE THESE IDs)
--   The gateway reads config.json which hardcodes:
--     tag_definition_ids: rpm=1, torque=2, current=3, dc_voltage=4,
--                         output_voltage=5, frequency=6, power=7
--     component_instance_ids: Jet 33=4, Jet 32=5, Jet 16=6, Jet 01=7,
--                              Jet 02=8, Jet 03=9, Jet 04=10, Jet 20=11,
--                              Jet 19=12, Jet 21=13, Jet 26=14, Jet 27=15,
--                              Jet 28=16, Jet 29=17
--   Changing any of these IDs requires a matching update to gateway/config.json
--   and a gateway service restart.
--
-- HOW TO RUN (from project root)
--   docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--       -f /scripts/seed_ssppl.sql
--   (Mount the scripts/ directory into the db container if not already done,
--    or pipe the file: cat scripts/seed_ssppl.sql | docker compose exec -T db psql ...)
--
-- PREREQUISITES
--   Alembic migrations 001–004 must be applied (alembic upgrade head) before
--   running this seed, because migration 004 adds the 'key' column to tag_definition.

-- =============================================================================
-- 1. Company
-- =============================================================================

INSERT INTO company (id, company_name, address)
VALUES (1, 'Shiv Shakti Prints Pvt. Ltd.', 'Surat, Gujarat, India')
ON CONFLICT (id) DO UPDATE SET
    company_name = EXCLUDED.company_name,
    address      = EXCLUDED.address;

-- =============================================================================
-- 2. Department
-- =============================================================================

INSERT INTO department (id, name, description, company_id)
VALUES (1, 'Dyeing', 'Soft-flow jet dyeing department', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 3. Machine type
-- =============================================================================

INSERT INTO machine_type (id, name, description, company_id)
VALUES (1, 'Jet Dyeing Machine', 'Soft-flow jet dyeing machine', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 4. Component type
--    ADR on record: the VFD is not the component — the reel motor is.
--    The VFD's registers (frequency, current, etc.) are the motor's tags.
-- =============================================================================

INSERT INTO component_type (id, name, description, company_id)
VALUES (1, 'Reel Motor', 'Reel motor driven by a VFD; VFD registers are its tags', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 5. Tag definitions (IDs 1–7) — GATEWAY CONTRACT IDs, do not renumber
--    key  = stable slug referenced by gateway config and frontend charts
--    name = human-editable display label (operators may rename; key must not change)
-- =============================================================================

INSERT INTO tag_definition (id, name, key, unit, description, data_type, company_id)
VALUES
    (1, 'Rotation Speed',   'rpm',            'RPM', 'Motor shaft speed',           'float', 1),
    (2, 'Output Torque',    'torque',         '%',   'VFD output torque estimate',   'float', 1),
    (3, 'Output Current',   'current',        'A',   'VFD output current (RMS)',     'float', 1),
    (4, 'DC Bus Voltage',   'dc_voltage',     'V',   'VFD DC bus voltage',           'float', 1),
    (5, 'Output Voltage',   'output_voltage', 'V',   'VFD output line voltage',      'float', 1),
    (6, 'Output Frequency', 'frequency',      'Hz',  'VFD output frequency',         'float', 1),
    (7, 'Output Power',     'power',          'kW',  'VFD output power',             'float', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    key         = EXCLUDED.key,
    unit        = EXCLUDED.unit,
    description = EXCLUDED.description,
    data_type   = EXCLUDED.data_type,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 6. Machines (IDs 3–16) — GATEWAY CONTRACT IDs, do not renumber
--    Order matches gateway config slave_id sequence (Jet 33 = slave 1, etc.)
-- =============================================================================

INSERT INTO machine (id, name, machine_type_id, description, company_id, department_id)
VALUES
    (3,  'Jet 33', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 1',  1, 1),
    (4,  'Jet 32', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 2',  1, 1),
    (5,  'Jet 16', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 3',  1, 1),
    (6,  'Jet 01', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 4',  1, 1),
    (7,  'Jet 02', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 5',  1, 1),
    (8,  'Jet 03', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 6',  1, 1),
    (9,  'Jet 04', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 7',  1, 1),
    (10, 'Jet 20', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 8',  1, 1),
    (11, 'Jet 19', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 9',  1, 1),
    (12, 'Jet 21', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 10', 1, 1),
    (13, 'Jet 26', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 11', 1, 1),
    (14, 'Jet 27', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 12', 1, 1),
    (15, 'Jet 28', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 13', 1, 1),
    (16, 'Jet 29', 1, 'Soft-flow jet dyeing machine — Modbus slave ID 14', 1, 1)
ON CONFLICT (id) DO UPDATE SET
    name            = EXCLUDED.name,
    machine_type_id = EXCLUDED.machine_type_id,
    description     = EXCLUDED.description,
    company_id      = EXCLUDED.company_id,
    department_id   = EXCLUDED.department_id;

-- =============================================================================
-- 7. Machine component instances (IDs 4–17) — GATEWAY CONTRACT IDs, do not renumber
--    One Reel Motor instance per machine; gateway posts telemetry to these IDs.
-- =============================================================================

INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
VALUES
    (4,  'Reel Motor', 1, 3,  1),  -- Jet 33 — slave ID 1
    (5,  'Reel Motor', 1, 4,  1),  -- Jet 32 — slave ID 2
    (6,  'Reel Motor', 1, 5,  1),  -- Jet 16 — slave ID 3
    (7,  'Reel Motor', 1, 6,  1),  -- Jet 01 — slave ID 4
    (8,  'Reel Motor', 1, 7,  1),  -- Jet 02 — slave ID 5
    (9,  'Reel Motor', 1, 8,  1),  -- Jet 03 — slave ID 6
    (10, 'Reel Motor', 1, 9,  1),  -- Jet 04 — slave ID 7
    (11, 'Reel Motor', 1, 10, 1),  -- Jet 20 — slave ID 8
    (12, 'Reel Motor', 1, 11, 1),  -- Jet 19 — slave ID 9
    (13, 'Reel Motor', 1, 12, 1),  -- Jet 21 — slave ID 10
    (14, 'Reel Motor', 1, 13, 1),  -- Jet 26 — slave ID 11
    (15, 'Reel Motor', 1, 14, 1),  -- Jet 27 — slave ID 12
    (16, 'Reel Motor', 1, 15, 1),  -- Jet 28 — slave ID 13
    (17, 'Reel Motor', 1, 16, 1)   -- Jet 29 — slave ID 14
ON CONFLICT (id) DO UPDATE SET
    name              = EXCLUDED.name,
    component_type_id = EXCLUDED.component_type_id,
    machine_id        = EXCLUDED.machine_id,
    company_id        = EXCLUDED.company_id;

-- =============================================================================
-- 8. Component type → tag links (Reel Motor is linked to all 7 VFD tags)
-- =============================================================================

INSERT INTO component_type_tag (id, component_type_id, tag_definition_id, company_id)
VALUES
    (1, 1, 1, 1),  -- Reel Motor → rpm
    (2, 1, 2, 1),  -- Reel Motor → torque
    (3, 1, 3, 1),  -- Reel Motor → current
    (4, 1, 4, 1),  -- Reel Motor → dc_voltage
    (5, 1, 5, 1),  -- Reel Motor → output_voltage
    (6, 1, 6, 1),  -- Reel Motor → frequency
    (7, 1, 7, 1)   -- Reel Motor → power
ON CONFLICT (id) DO UPDATE SET
    component_type_id = EXCLUDED.component_type_id,
    tag_definition_id = EXCLUDED.tag_definition_id,
    company_id        = EXCLUDED.company_id;

-- =============================================================================
-- 9. Sequence resets — advance each sequence past the highest ID we just inserted
--    Uses MAX(id) so this is safe even if other rows exist with higher IDs.
--    COALESCE handles the (impossible post-insert) case where the table is empty.
-- =============================================================================

SELECT setval('company_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM company));

SELECT setval('department_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM department));

SELECT setval('machine_type_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM machine_type));

SELECT setval('component_type_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM component_type));

SELECT setval('tag_definition_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM tag_definition));

SELECT setval('machine_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM machine));

SELECT setval('machine_component_instance_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM machine_component_instance));

SELECT setval('component_type_tag_id_seq',
    (SELECT COALESCE(MAX(id), 1) FROM component_type_tag));

-- NOTE: the gateway user (gateway@ssppl.com) is NOT seeded here because this
-- file is committed to git and must never contain a password hash.
-- Create the gateway user via the API after running this seed:
--   POST /users {"username":"gateway@ssppl.com","password":"...","role":"admin","company_id":1}
