-- seed_new_machines.sql — Add Bus 2 + Bus 3 hardware to the database
--
-- PURPOSE
--   Extends the existing SSPPL machine hierarchy with:
--     • tag_definition id=8  : temperature (°C) — for Electrosil Fx-438 on Bus 3
--     • component_type id=2  : Temperature Sensor
--     • machine ids 17–27    : 11 new jet dyeing machines on Bus 2
--     • machine_component_instance ids 18–29 : Reel Motors for Bus 2 jets +
--                                              Dyebath Temperature Sensor for Jet 27
--     • component_type_tag id=9 : Temperature Sensor → temperature tag link
--
-- GATEWAY CONTRACT (DO NOT CHANGE THESE IDs)
--   Bus 2 component_instance_ids (gateway/config.json):
--     Jet 31=18, Jet 30=19, Jet 15=20, Jet 05=21, Jet 06=22, Jet 07=23,
--     Jet 08=24, Jet 22=25, Jet 23=26, Jet 24=27, Jet 25=28
--   Bus 3 component_instance_ids:
--     Jet 27 dyebath (temp sensor)=29
--   temperature tag_definition_id=8
--
-- PREREQUISITES
--   seed_ssppl.sql must have been run first (provides company, machine_type,
--   component_type id=1, tag_definitions 1–7, machines 3–16, instances 4–17).
--
-- HOW TO RUN (from project root)
--   docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--       -f /scripts/seed_new_machines.sql
--   (or pipe: cat scripts/seed_new_machines.sql | docker compose exec -T db psql ...)
--
-- IDEMPOTENT
--   Every INSERT uses ON CONFLICT DO UPDATE so the script is safe to re-run.

-- =============================================================================
-- 1. New tag definition: temperature (id=8)
-- =============================================================================

INSERT INTO tag_definition (id, name, key, unit, description, data_type, company_id)
VALUES (8, 'Process Temperature', 'temperature', '°C',
        'Dyebath temperature measured by PID temperature controller', 'float', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    key         = EXCLUDED.key,
    unit        = EXCLUDED.unit,
    description = EXCLUDED.description,
    data_type   = EXCLUDED.data_type,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 2. New component type: Temperature Sensor (id=2)
-- =============================================================================

INSERT INTO component_type (id, name, description, company_id)
VALUES (2, 'Temperature Sensor',
        'PID temperature controller reading dyebath process value', 1)
ON CONFLICT (id) DO UPDATE SET
    name        = EXCLUDED.name,
    description = EXCLUDED.description,
    company_id  = EXCLUDED.company_id;

-- =============================================================================
-- 3. New machines — 11 jets on Bus 2 (ids 17–27)
--    machine_type_id=1 (Jet Dyeing Machine), department_id=1 (Dyeing)
-- =============================================================================

INSERT INTO machine (id, name, machine_type_id, description, company_id, department_id)
VALUES
    (17, 'Jet 31', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 1  (Delta CP2000)',  1, 1),
    (18, 'Jet 30', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 2  (Delta CP2000)',  1, 1),
    (19, 'Jet 15', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 3  (INVT CHF100A)', 1, 1),
    (20, 'Jet 05', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 4  (INVT CHF100A)', 1, 1),
    (21, 'Jet 06', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 5  (INVT CHF100A)', 1, 1),
    (22, 'Jet 07', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 6  (INVT CHF100A)', 1, 1),
    (23, 'Jet 08', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 7  (INVT CHF100A)', 1, 1),
    (24, 'Jet 22', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 8  (INVT CHF100A)', 1, 1),
    (25, 'Jet 23', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 9  (Yaskawa F7)',   1, 1),
    (26, 'Jet 24', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 10 (Yaskawa F7)',   1, 1),
    (27, 'Jet 25', 1, 'Soft-flow jet dyeing machine — Bus 2 slave ID 11 (Yaskawa F7)',   1, 1)
ON CONFLICT (id) DO UPDATE SET
    name            = EXCLUDED.name,
    machine_type_id = EXCLUDED.machine_type_id,
    description     = EXCLUDED.description,
    company_id      = EXCLUDED.company_id,
    department_id   = EXCLUDED.department_id;

-- =============================================================================
-- 4. Machine component instances — Reel Motors for Bus 2 (ids 18–28)
--    component_type_id=1 (Reel Motor / VFD)
-- =============================================================================

INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
VALUES
    (18, 'Reel Motor', 1, 17, 1),  -- Jet 31 — Bus 2 slave 1  (Delta CP2000)
    (19, 'Reel Motor', 1, 18, 1),  -- Jet 30 — Bus 2 slave 2  (Delta CP2000)
    (20, 'Reel Motor', 1, 19, 1),  -- Jet 15 — Bus 2 slave 3  (INVT CHF100A)
    (21, 'Reel Motor', 1, 20, 1),  -- Jet 05 — Bus 2 slave 4  (INVT CHF100A)
    (22, 'Reel Motor', 1, 21, 1),  -- Jet 06 — Bus 2 slave 5  (INVT CHF100A)
    (23, 'Reel Motor', 1, 22, 1),  -- Jet 07 — Bus 2 slave 6  (INVT CHF100A)
    (24, 'Reel Motor', 1, 23, 1),  -- Jet 08 — Bus 2 slave 7  (INVT CHF100A)
    (25, 'Reel Motor', 1, 24, 1),  -- Jet 22 — Bus 2 slave 8  (INVT CHF100A)
    (26, 'Reel Motor', 1, 25, 1),  -- Jet 23 — Bus 2 slave 9  (Yaskawa F7)
    (27, 'Reel Motor', 1, 26, 1),  -- Jet 24 — Bus 2 slave 10 (Yaskawa F7)
    (28, 'Reel Motor', 1, 27, 1)   -- Jet 25 — Bus 2 slave 11 (Yaskawa F7)
ON CONFLICT (id) DO UPDATE SET
    name              = EXCLUDED.name,
    component_type_id = EXCLUDED.component_type_id,
    machine_id        = EXCLUDED.machine_id,
    company_id        = EXCLUDED.company_id;

-- =============================================================================
-- 5. Dyebath temperature sensor on Jet 27 (id=29)
--    Jet 27 machine already exists (machine id=14, from seed_ssppl.sql).
--    This adds a second component instance — the Electrosil Fx-438 PID controller
--    mounted on Bus 3.  component_type_id=2 (Temperature Sensor).
-- =============================================================================

INSERT INTO machine_component_instance (id, name, component_type_id, machine_id, company_id)
VALUES (29, 'Dyebath Temperature Sensor', 2, 14, 1)
ON CONFLICT (id) DO UPDATE SET
    name              = EXCLUDED.name,
    component_type_id = EXCLUDED.component_type_id,
    machine_id        = EXCLUDED.machine_id,
    company_id        = EXCLUDED.company_id;

-- =============================================================================
-- 6. Component type → tag links
--    Temperature Sensor (id=2) → temperature tag (id=8)
-- =============================================================================

INSERT INTO component_type_tag (id, component_type_id, tag_definition_id, company_id)
VALUES (9, 2, 8, 1)  -- Temperature Sensor → temperature
ON CONFLICT (id) DO UPDATE SET
    component_type_id = EXCLUDED.component_type_id,
    tag_definition_id = EXCLUDED.tag_definition_id,
    company_id        = EXCLUDED.company_id;

-- =============================================================================
-- 7. Sequence resets — advance sequences past highest inserted IDs
-- =============================================================================

SELECT setval('tag_definition_id_seq',
    (SELECT COALESCE(MAX(id), 8) FROM tag_definition));

SELECT setval('component_type_id_seq',
    (SELECT COALESCE(MAX(id), 2) FROM component_type));

SELECT setval('machine_id_seq',
    (SELECT COALESCE(MAX(id), 27) FROM machine));

SELECT setval('machine_component_instance_id_seq',
    (SELECT COALESCE(MAX(id), 29) FROM machine_component_instance));

SELECT setval('component_type_tag_id_seq',
    (SELECT COALESCE(MAX(id), 9) FROM component_type_tag));
