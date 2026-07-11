# Phase 5c Plan — Read-side Telemetry API

Grounded in the actual codebase (main.py, database.py, auth.py, routers/data_router.py,
models/telemetry_data.py, models/machine.py, models/machine_component_instance.py,
models/tag_definition.py, schemas/telemetry.py, routers/machine_router.py).
Proposed only — no files edited by this pass.

## Key facts confirmed from code

- `get_tenant_db` (auth.py) yields a `Session` with `SET LOCAL app.current_company_id` set,
  but RLS is bypassed (superuser connection) — app-layer `WHERE company_id = ...` filters are
  the real isolation mechanism, matching the pattern in `data_router.py` / `machine_router.py`.
- `TelemetryData` (models/telemetry_data.py): composite PK `(id, timestamp)`, plain
  `Integer` columns for `component_instance_id`, `tag_definition_id`, `company_id` (no FK,
  hypertable). `value_num` is `Float`, nullable.
- `MachineComponentInstance` has `machine_id` FK → `Machine`; `Machine` has `company_id`,
  `name`. No `vfd_model` / `slave_id` columns exist anywhere in the schema — confirms handoff
  note that these must be `null` for now.
- `TagDefinition.name` holds the string tag names (`frequency`, `current`, etc.) — needed to
  turn `tag_definition_id` into the `tags.<name>` keys in the response.
- Existing routers use plain `APIRouter()` with no prefix, registered via
  `app.include_router(...)` in `main.py`, and use `get_current_user` + `get_tenant_db` as
  paired dependencies (see `machine_router.py`, `data_router.py`). Phase 5c should match this
  exactly — `get_current_user` for `current_user["company_id"]`, `get_tenant_db` for the
  session.
- `expire_on_commit=False` and no `db.refresh()` on hypertable rows is a hard rule (see
  comments in `database.py` and `data_router.py`) — irrelevant for read-only endpoints since
  nothing is inserted, but the raw-SQL `text()` queries must still go through the same
  `Session` object.

## Files to create

### `schemas/telemetry_read.py`

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

class MachineTags(BaseModel):
    frequency: Optional[float] = None
    current: Optional[float] = None
    power: Optional[float] = None
    rpm: Optional[float] = None
    torque: Optional[float] = None
    output_voltage: Optional[float] = None
    dc_voltage: Optional[float] = None

class MachineLiveResponse(BaseModel):
    machine_id: int
    machine_name: str
    component_instance_id: int
    vfd_model: Optional[str] = None
    slave_id: Optional[int] = None
    last_updated: Optional[datetime] = None
    tags: MachineTags

class HistoryPoint(BaseModel):
    bucket: datetime
    frequency: Optional[float] = None
    current: Optional[float] = None
    power: Optional[float] = None
    rpm: Optional[float] = None
    torque: Optional[float] = None
    output_voltage: Optional[float] = None
    dc_voltage: Optional[float] = None

class MachineHistoryResponse(BaseModel):
    machine_id: int
    machine_name: str
    interval: str
    data: list[HistoryPoint]

class FleetSummaryResponse(BaseModel):
    total_machines: int
    running: int
    stopped: int
    total_power_kw: float
    last_updated: Optional[datetime] = None
```

Tag names are hardcoded to the 7 known tags (matches the fixed `tag_definition` catalogue in
the handoff: rpm=1, torque=2, current=3, dc_voltage=4, output_voltage=5, frequency=6, power=7).
No dynamic tag discovery — keeps the read-side simple and matches the frontend's fixed 7-metric
tile layout.

### `routers/telemetry_read.py`

Router with no prefix (`APIRouter()`), registered in `main.py` alongside the other routers.
All four handlers take `current_user: dict = Depends(get_current_user)` and
`db: Session = Depends(get_tenant_db)`, mirroring `machine_router.py`.

**Endpoint 1 — `GET /machines/live`**

Two-step query (ORM `DISTINCT ON` in SQLAlchemy is awkward across a pivoted tag structure, so
use raw SQL for the per-tag pivot):

1. Raw SQL, `DISTINCT ON (td.component_instance_id, td.tag_definition_id)` ordered by
   `timestamp DESC`, joined to `tag_definition` for the name, filtered by
   `td.company_id = :company_id` and `tag_definition_id` in the known 7 IDs (or just join and
   let the tag name drive the pivot — no hardcoded IDs needed since `tag_definition.name` is
   read directly). This returns one row per `(component_instance_id, tag_name)` with its
   latest value + timestamp.
2. In Python, fetch all machines + component_instances for the company (ORM query, matches
   `machine_router.get_machines` pattern) to get `machine_id`, `machine_name`,
   `component_instance_id`.
3. Merge: group the raw-SQL rows by `component_instance_id` into a `{tag_name: value}` dict,
   attach to each machine, take `max(timestamp)` across that machine's rows as `last_updated`.
4. Machines with no telemetry rows yet still appear, with `tags` all `null` and
   `last_updated: null` — satisfies "no data yet" requirement, no 500s.

SQL sketch:

```sql
SELECT DISTINCT ON (td.component_instance_id, td.tag_definition_id)
    td.component_instance_id, tg.name AS tag_name, td.value_num, td.timestamp
FROM telemetry_data td
JOIN tag_definition tg ON tg.id = td.tag_definition_id
WHERE td.company_id = :company_id
ORDER BY td.component_instance_id, td.tag_definition_id, td.timestamp DESC
```

**Endpoint 2 — `GET /machines/{machine_id}/live`**

Same query filtered by one `component_instance_id`. First validate the machine belongs to the
company (`Machine.company_id == current_user["company_id"]`, else 404) and resolve its
`component_instance_id`. If a machine somehow has zero component instances, still return the
object with `tags` all null rather than erroring — but a missing machine row (wrong company or
nonexistent id) is a genuine 404.

**Endpoint 3 — `GET /machines/{machine_id}/history`**

- Query params: `start: Optional[datetime]` (default `now - 1h`), `end: Optional[datetime]`
  (default `now`), `interval: str = "1m"`.
- Validate machine ownership same as endpoint 2 → 404 if not found/wrong tenant.
- Map `interval` string to a Postgres interval literal for `time_bucket()`. Whitelist against
  `{"1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes", "1h": "1 hour"}` — reject anything
  else with 400 rather than interpolating raw user input into SQL.
- Raw SQL, `text()`, bound params for `component_instance_id`, `start`, `end`; the interval
  literal is inserted from the whitelist dict (not user input directly) since `time_bucket()`
  cannot take a bind parameter for its first argument in all drivers — safer to whitelist.

```sql
SELECT
    time_bucket(:interval_literal, td.timestamp) AS bucket,
    tg.name AS tag_name,
    AVG(td.value_num) AS avg_val
FROM telemetry_data td
JOIN tag_definition tg ON tg.id = td.tag_definition_id
WHERE td.company_id = :company_id
  AND td.component_instance_id = :component_instance_id
  AND td.timestamp BETWEEN :start AND :end
GROUP BY bucket, tag_name
ORDER BY bucket DESC
```

Pivot rows into `bucket -> {tag: avg_val}` in Python, sort by bucket, return as `data` list.
No matching rows → `data: []`, not an error.

**Endpoint 4 — `GET /fleet/summary`**

- `total_machines`: `COUNT(*)` from `Machine` filtered by `company_id` (ORM, matches
  `machine_router.get_machines`).
- `running` / `stopped`: raw SQL — for each machine's component instance, take the latest
  `frequency` (`tag_definition.name = 'frequency'`) reading; `running` if
  `value_num > 0 AND timestamp > now() - interval '30 seconds'`, else `stopped`. This reuses
  the same `DISTINCT ON` pattern as endpoint 1, filtered to the frequency tag only.
- `total_power_kw`: `SUM` of each machine's latest `power` reading (same recency logic, but per
  the handoff spec the "running" test is time-bound to 30s while `total_power_kw` sums whatever
  the latest power reading is regardless of staleness — will confirm this is the intended
  semantics before building, since a stale power value from a stopped machine could inflate the
  total).
- `last_updated`: `MAX(timestamp)` across all of the company's telemetry rows.
- Company with zero machines or zero telemetry rows → all-zero response, not a 500.

### `main.py` change

```python
from routers.telemetry_read import router as telemetry_read_router
...
app.include_router(telemetry_read_router)   # GET /machines/live, /machines/{id}/live, /machines/{id}/history, /fleet/summary
```

Placed after `data_router` in the include order, consistent with existing ordering (CRUD
routers first, then data, then read-aggregation last).

### `test_api.py`

Append smoke-test steps after the existing `POST /data` step, following the file's existing
`request()` helper + PASS/FAIL pattern:
1. `GET /machines/live` with the JWT from step 4 → expect 200, list, each item has a `tags`
   dict.
2. `GET /machines/{machine_id}/live` for the machine created earlier in the script → expect
   200 and matching `machine_id`.
3. `GET /machines/{machine_id}/history?interval=1m` → expect 200 and a `data` list (may be
   empty if the test posted only one point — acceptable, since the check is "no 500", not
   "non-empty").
4. `GET /fleet/summary` → expect 200 and `total_machines >= 1`.
5. Negative case: `GET /machines/999999/live` → expect 404.

### `docs/architecture-decisions.md` — ADR-009 (draft)

**ADR-009: Read-side telemetry API uses raw SQL for time-series aggregation**

Context: Phase 5c needs "latest value per machine per tag" and "time-bucketed average" queries
against the `telemetry_data` hypertable. The ORM cannot express `DISTINCT ON` ordering or
TimescaleDB's `time_bucket()`.

Decision: Use SQLAlchemy `text()` with bound parameters for these two query shapes, kept inside
`routers/telemetry_read.py` only. All bound values (company_id, component_instance_id, start,
end) are passed as params; the only interpolated-but-not-parameterized value is the
`time_bucket()` interval literal, which is restricted to a fixed whitelist dict rather than
accepting raw user input, to avoid SQL injection through the `interval` query parameter.

Consequence: Read-side aggregation queries are harder to unit test in isolation than ORM
queries, but are the only way to get hypertable-native performance. Company-scoping is enforced
identically to write-side routers (`WHERE company_id = :company_id` in every query) — no new
tenant-isolation pattern introduced.

## Open question before building

`total_power_kw` in `/fleet/summary` — should it sum *only* running machines' power, or the
latest power reading regardless of staleness (including from stopped machines, which may still
report a small non-zero idle draw)? The handoff spec doesn't disambiguate. Recommend: sum power
only for machines currently `running` (frequency > 0 within 30s), since a stopped machine's
"power" reading is likely stale/irrelevant. Flagging for review-chat sign-off before Claude Code
builds.

## Explicitly not touched

`data_router.py`, `machine_router.py`, any other write-side router or model — nothing existing
is modified. `alembic/versions/003_rls_tenant_isolation.py` untouched (Phase 5b still deferred).
