-- backfill_jet11_totalizer_unit_fix.sql
--
-- ONE-OFF DATA CORRECTION (not a schema change — deliberately not an
-- Alembic migration; run manually, once, against production).
--
-- ROOT CAUSE
--   Jet 11's flowmeter (component_instance_id=31), after the 2026-09-04
--   switch to RTU mode (register 1284, see gateway/gateway_service.py's
--   _EUREKA_EU1000_RTU_SPEC), was writing raw register values straight to
--   telemetry_data without converting them. The register stores CUBIC
--   METERS (confirmed against the physical display: "12365.3 m3"), but
--   tag_definition_id=11 ("flow_totalizer") is unit='L' (confirmed live,
--   matches migration 007). Every row written in this window is therefore
--   1000x too small. Fixed in gateway_service.py on 2026-09-05 (candidate
--   total now multiplied by 1000 before being stored).
--
-- SCOPE — confirmed directly from the data, not assumed:
--   Lower bound 2026-09-04 11:36:32 = first clean RTU-mode row (the row
--     immediately before it, 2026-08-18 11:52:41 = 45,494,394, is old
--     ASCII-mode garbage from the meter's previous config — a LAG() delta
--     query confirmed the one-time -45,481,797 drop as the exact
--     garbage->clean transition, so that garbage row and everything before
--     it is correctly excluded by this lower bound).
--   Upper bound 2026-09-05 09:06:12 = first row written by the *already
--     fixed* gateway code (confirmed: the row immediately before it, at
--     09:05:31, is 12632.7890625 — the same physical reading as the fixed
--     row's 12632789.0625, just still un-converted). Rows at/after this
--     timestamp are already correct and must NOT be touched.
--
-- SAFETY
--   The value_num < 100000 guard below is redundant with the timestamp
--   window (every affected raw value is in the ~12,000-13,000 range; every
--   correctly-scaled value is in the ~12-13 million range) but is kept as
--   a second, independent condition so that accidentally re-running this
--   script is a no-op the second time, rather than silently re-multiplying
--   already-corrected rows by another 1000x.
--
-- HOW TO RUN (from project root)
--   docker compose exec db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--       -f /scripts/backfill_jet11_totalizer_unit_fix.sql
--   (or pipe: cat scripts/backfill_jet11_totalizer_unit_fix.sql | docker compose exec -T db psql ...)
--
-- Review Step 1's output before running Step 2. Step 2 is NOT idempotent
-- in general — only the value_num guard makes a second run harmless.

-- =============================================================================
-- Step 1 — verify scope BEFORE writing. Expect: a few thousand rows,
-- min/max value_num in the ~12,000-13,000 range (m3-scale, unconverted),
-- min/max timestamp matching the confirmed boundaries above.
-- =============================================================================

SELECT
    COUNT(*)         AS row_count,
    MIN(timestamp)   AS earliest_ts,
    MAX(timestamp)   AS latest_ts,
    MIN(value_num)   AS min_value,
    MAX(value_num)   AS max_value
FROM telemetry_data
WHERE component_instance_id = 31
  AND tag_definition_id     = 11
  AND timestamp             >= '2026-09-04 11:36:32'
  AND timestamp             <  '2026-09-05 09:06:12'
  AND value_num             <  100000;

-- =============================================================================
-- Step 2 — the correction. Only run after reviewing Step 1's output above.
-- =============================================================================

UPDATE telemetry_data
SET value_num = value_num * 1000
WHERE component_instance_id = 31
  AND tag_definition_id     = 11
  AND timestamp             >= '2026-09-04 11:36:32'
  AND timestamp             <  '2026-09-05 09:06:12'
  AND value_num              < 100000;

-- =============================================================================
-- Step 3 — verify the result. Expect: same row_count as Step 1, min/max
-- value_num now in the ~12-13 million range, matching the already-correct
-- rows on either side of this window (45,494,394-ish before, 12,632,789-ish
-- after).
-- =============================================================================

SELECT
    COUNT(*)         AS row_count,
    MIN(timestamp)   AS earliest_ts,
    MAX(timestamp)   AS latest_ts,
    MIN(value_num)   AS min_value,
    MAX(value_num)   AS max_value
FROM telemetry_data
WHERE component_instance_id = 31
  AND tag_definition_id     = 11
  AND timestamp             >= '2026-09-04 11:36:32'
  AND timestamp             <  '2026-09-05 09:06:12';
