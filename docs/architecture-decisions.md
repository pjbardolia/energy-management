# Architecture Decisions — Energy Management / Industrial IoT SaaS Platform

Status: ACCEPTED (locked 2026-06-28)
Audience: this is the build spec. Implement to it; do not deviate without explicit approval.
Scale target: 50,000+ factories, multi-industry (textiles, food, plastics, chemicals, …).

---

## Decision 1 — Telemetry storage: TimescaleDB

**Decision:** Store telemetry in PostgreSQL with the **TimescaleDB** extension. The
`telemetry_data` table becomes a **hypertable** partitioned by time.

**Why:** Telemetry grows to billions of rows (≈50 machines × components × tags × frequent
samples = millions/day per factory). A plain Postgres table degrades (index bloat, slow
inserts, slow range queries, painful retention/backup). TimescaleDB is a Postgres extension
— SQLAlchemy/SQL/code stay identical — and adds automatic time-partitioning (chunks),
columnar compression (~90% on sensor data), and continuous aggregates (pre-rolled
hourly/daily summaries for fast dashboards). MongoDB was considered and rejected: the model
is deeply relational (foreign keys, joins, RLS) and not document-shaped.

**Built for tenant-scale:** every telemetry row carries `company_id`; indexes lead with
`company_id` then time, so each tenant reads only its own recent chunks. Compression +
time-partitioning keep storage and queries manageable. Extreme scale is handled by sharding
**per tenant** at the app layer (tenant data is independent). This removes InfluxDB from the
roadmap.

**Implications:** swap the Postgres image in `docker-compose.yml` for the official
TimescaleDB image; `CREATE EXTENSION timescaledb`; `create_hypertable('telemetry_data','timestamp')`;
add a compression policy and (later) a retention policy. Hypertables require the partition
column in any primary key, so `telemetry_data` PK is composite, e.g. `(timestamp, id)`.

---

## Decision 2 — Telemetry value typing: two columns + a tag data_type

**Decision:** Store each reading's value in **two columns**:
- `value_num` — **DOUBLE PRECISION**, nullable. Holds the numeric family: float, integer,
  and booleans (as 0/1). This is the hot path used by ~99% of readings (VFD output
  frequency, bus voltage, current; later temperature, pressure; counters; on/off states).
- `value_text` — **TEXT**, nullable. Holds the exceptions: fault/status codes and other
  free-text (e.g. "OVERTEMP", "E-021", batch/lot IDs). Mostly NULL; compresses to ~nothing.

Add a **`data_type`** field to `TagDefinition` (enum: `float | int | bool | text`) alongside
the existing `unit`. It declares each tag's type, drives which value column is used, and
tells dashboards how to render (e.g. 0/1 → "Off/On", integers without decimals).

**Why:** Current data is all numeric, so a single numeric column would suffice *today*. But
the platform must be generic across industries, which will eventually bring non-numeric
signals (chemical/food fault codes, statuses, batch IDs). `DOUBLE PRECISION` cleanly absorbs
float/int/bool and is the standard, compact, compressible choice for high-volume sensor data
(NUMERIC/DECIMAL is slower/larger and only needed for exact decimals, which telemetry is
not). The tiny cost of one mostly-empty text column avoids a painful billion-row migration
the first time a text reading appears.

---

## Decision 3 — Machine ↔ Department: department_id on Machine

**Decision:** Put `department_id` directly on **Machine**. `MachineType` becomes a pure,
reusable catalog (no department). A machine independently declares its `machine_type_id`
AND its `department_id`.

**Why:** "What kind of machine it is" (a Stenter, a Pump) and "where it lives" (Finishing,
Cooling) are independent facts. Tying department to the *type* forces every machine of a type
into one department — fine for textiles (Stenter only in Finishing) but broken for
multi-industry use, where the same type (Pump, Motor, Conveyor) appears in many departments.
Putting department on the machine keeps `MachineType` reusable across departments and matches
the original handoff spec (Machine "Belongs to Department" AND "Belongs to Machine Type").

**Hierarchy is preserved:** the displayed tree (e.g. Finishing → Stenter → stenter1,2,3) is a
*grouping* — show a department, group its machines by type. Underneath, each machine knows its
own department and type, so "Pump" can appear under Cooling, Utilities, and Reaction at once.

**Implications:** move `department_id` from `MachineType` to `Machine`; `MachineType` keeps
just `name` (+ `company_id`).

---

## Decision 4 — Multi-tenancy: shared schema + company_id + Row-Level Security

**Decision:** Single shared database/schema. Every tenant-scoped table carries `company_id`.
Isolation is enforced by **PostgreSQL Row-Level Security (RLS)** policies, not by trusting
each query to filter.

**Why:** Scales to tens of thousands of tenants (DB-per-tenant and schema-per-tenant do not).
RLS pushes the isolation guarantee into the database, so one forgotten `WHERE company_id=…`
cannot leak one factory's data to another — it fails safe.

**Implications:** `company_id` (FK → company, NOT NULL) on every tenant-scoped table; enable
RLS + policies keyed on a per-request session variable (`app.current_company_id`); a
`get_db()` dependency sets that variable inside each request's transaction (handle connection
pooling with `SET LOCAL`); JWT carries `company_id`; a `BYPASSRLS` role for platform-admin
and migrations. Also enforce the JWT auth dependency on endpoints (currently issued but not
checked).

---

## Target schema (tables → columns)

Tenant-scoped tables carry `company_id` (FK → company, NOT NULL).

- **company**: id, company_name, address  *(tenant root)*
- **users**: id, username (unique), password_hash, role, company_id
- **department**: id, name, description, company_id
- **machine_type**: id, name, company_id  *(reusable catalog; NO department_id)*
- **machine**: id, name, description, machine_type_id (FK), **department_id (FK)**, company_id
- **component_type**: id, name, company_id  *(reusable catalog)*
- **machine_component_instance**: id, name, component_type_id (FK), machine_id (FK), company_id
- **tag_definition**: id, name, unit, **data_type (enum)**, company_id
- **component_type_tag**: id, component_type_id (FK), tag_definition_id (FK), company_id  *(junction)*
- **telemetry_data**: timestamp, component_instance_id (FK), tag_definition_id (FK),
  **value_num (DOUBLE PRECISION, null)**, **value_text (TEXT, null)**, company_id
  *(hypertable; PK includes timestamp)*

---

## Config CRUD needed (prerequisite for the write endpoints)

To create a machine you need a `machine_type` and a `department`; to create a component you
need a `component_type`; to create telemetry you need a `tag_definition`. The type/tag tables
have no endpoints yet, so add CRUD (POST + GET) for: **machine_types, component_types,
tag_definitions** (and component_type_tags). Without these, a factory cannot be configured
via the API.

---

## Build sequencing (get to green first, then add scale machinery)

**Phase 1 — Foundation (plain Postgres):** model + schema restructure to the target schema
above (value_num/value_text, data_type, department_id→Machine, company_id columns,
MachineType as catalog).

**Phase 2 — Config endpoints:** CRUD for machine_types, component_types, tag_definitions
(+ component_type_tags).

**Phase 3 — Fix the three write endpoints** to the normalized shape:
- POST /machines → {name, description, machine_type_id, department_id}
- POST /machine-components → {name, component_type_id, machine_id}
- POST /data → {timestamp, component_instance_id, tag_definition_id, value_num? | value_text?}
  (choose the column per the tag's data_type; validate)
Update `test_api.py` to the new create→read chain
(company → department → machine_type → machine → component_type → component → tag_definition
→ data) so the three known failures flip to green.

**Phase 4 — Scale machinery:** swap to TimescaleDB image + hypertable + compression; add RLS
policies + `get_db()` tenant context + `company_id` in JWT + enforce auth on endpoints.

Also (cleanup, alongside): replace the per-handler `SessionLocal()` (connection leak) with a
`get_db()` dependency; remove orphaned flat files (models.py, schemas.py, root user.py,
data_old.py, the flat *_router.py once moved); fully pin requirements.txt; introduce Alembic
for migrations (create_all cannot ALTER existing tables, and cannot create hypertables/RLS).

Follow CLAUDE.md for every change: comment important lines, explain reasoning, provide testing
AND rollback steps, preserve working behavior, no black-box code.
