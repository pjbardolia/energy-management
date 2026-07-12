# mevion — Build Architect Handbook

**Version 1.0 — 2026-07-12**
**Author of record: Pruthvi Bardolia (pjbardolia) with Claude (architecture review chat)**
**Audience: AI coding agents (Claude Code and successors) and human engineers joining this project.**

---

## 0. How to use this document

You are an AI builder working on mevion. This document is your onboarding, your
constraints, and your roadmap. Read it fully before proposing any change.

Rules of engagement (non-negotiable, learned the hard way — see §7):

1. **Plan before touching files.** Propose a plan, wait for explicit approval,
   then build. Never combine "here is the plan" and "I built it" in one turn.
2. **Never trust your own completion summary.** After building, verify with
   `grep`/`ls` that files exist on disk, contain what the plan said, and are
   actually tracked by git (`git status`, `git check-ignore -v <file>`). This
   project has three documented incidents of "built" artifacts that did not
   exist or were silently gitignored.
3. **Pruthvi commits and pushes manually** in his own terminal. You never run
   git write operations.
4. **Explain architectural reasoning.** Pruthvi is learning-focused; he reviews
   every change and expects to understand *why*, not just *what*.
5. **Honor the hard contracts in §5.** Breaking them causes silent data loss,
   not errors.
6. **Never run destructive tests against production.** `test_api.py` has
   fail-closed guards (env gate + preflight company check). Never weaken them.
7. **Document significant decisions** as ADRs in
   `docs/architecture-decisions.md` (ADR-001 … ADR-010 exist).
8. When given a mapping table (IDs, slugs, names), reproduce it **verbatim**.
   Do not derive, regex, or "improve" it. Two review rounds were lost to a
   builder substituting its own derivation for a supplied contract table.

---

## 1. Project identity and vision

**mevion** ("Data to Decisions", red #E31837 / white) is a multi-tenant
Industrial IoT SaaS platform for energy management, starting with textile
processing and designed to generalize across industries.

- **First live deployment / reference customer:** Shiv Shakti Prints Pvt Ltd
  (SSPPL), a jet-dyeing factory in Surat, India — owned by Pruthvi himself.
  14 jet dyeing machines instrumented via their VFDs.
- **Business model (target):** subscription SaaS. A platform superadmin
  provisions customer companies; each company's plan caps how many machines
  they may register (pay more → register more). Factory admins then self-serve:
  create departments, machines, and machine instances **with one click** in the
  web UI, with everything reflected in the database immediately.
- **Long-term vision:** tens of thousands of factories; textile-specific
  features (dyehouse recipe OCR, costing) as optional modules on a generic
  industrial core; alarms, reports, predictive maintenance, mobile access.

The strategy is deliberate: prove everything on SSPPL (real machines, real
failure modes, real operators), then productize the provisioning path so the
second factory takes hours, not weeks.

---

## 2. Current state — verified working as of 2026-07-12

### Backend (Phases 1–5c complete)
- FastAPI (Python 3.13) + SQLAlchemy + Alembic. Routers: health, auth,
  companies, users, departments, machine-types, machines, component-types,
  machine-components, tag-definitions, data (write), **telemetry_read** (read).
- **Read API (Phase 5c, deployed and verified against live production data):**
  - `GET /machines/live` — latest reading per machine, tags pivoted into a
    `tags: {slug: value}` dict.
  - `GET /machines/{machine_id}/live` — single machine, 404 cross-tenant safe.
  - `GET /machines/{machine_id}/history?hours=1..24` — TimescaleDB
    `time_bucket()` long-form query + Python pivot; response buckets are
    `{bucket, tags: {...}}`. Bucket size auto-scales (1m/5m/15m).
  - `GET /fleet/summary` — totals computed server-side; "running" =
    frequency > 0 in latest readings.
  - All tenant-generic: **no tag IDs or display names are hardcoded anywhere**;
    identification is by `tag_definition.key` slug (ADR-010).
- JWT auth: company_id + role embedded, 60-min expiry, bcrypt hashes.
  `get_tenant_db()` sets `SET LOCAL app.current_company_id` per request.
- RLS policies exist on 7 tables (migration 003) but are **currently bypassed**
  because the app connects as a Postgres superuser. Full enforcement is
  Phase 5b (dedicated `app_role`). telemetry_data is excluded from direct RLS
  (TimescaleDB columnstore incompatibility) — its isolation is via the app
  layer + explicit `company_id` predicates in the read queries.
- Migrations 001–004 applied. 004 added `tag_definition.key` (NOT NULL,
  unique per `(company_id, key)`), backfilled with an explicit CASE mapping.

### Database (TimescaleDB / PG16, Docker on DigitalOcean 165.22.247.235:8001)
- `telemetry_data` hypertable, composite PK (id, timestamp), UTC timestamps.
- Seeded via `scripts/seed_ssppl.sql` (idempotent, IDs pinned): company 1,
  Dyeing department, Jet Dyeing Machine type, **Reel Motor** component type,
  tag definitions 1–7, machines 3–16 (Jet 33, 32, 16, 01–04, 20, 19, 21,
  26–29 in RS485 slave order), component instances 4–17.
- **The pgdata volume now actually persists** (see §7.1 — it did not before).

### Edge gateway (Raspberry Pi 3B+, office LAN 192.168.0.200)
- `gateway/gateway_service.py`, systemd service, pymodbus 3.13.1, Waveshare
  FT4232H RS485 on /dev/ttyUSB2, 9600 8N1, polls 14 drives every 10 s.
- **Store-and-forward:** every reading lands in a local SQLite outbox *before*
  any network attempt; a forwarder drains the outbox to `POST /data` with JWT
  auth. This design has now survived two multi-hour outages (one network, one
  server-side auth loss) with zero data loss — only delay. Treat it as
  load-bearing architecture, not an optimization.
- Config: `gateway/config.json` on the Pi hardcodes API URL, gateway
  credentials, and the device map (slave_id ↔ component_instance_id ↔
  vfd_model). This hardcoding is the platform's biggest scaling liability —
  see Phase 9 (§9.4).

### Frontend (React + Vite, `frontend/`, mock data — swap pending)
- Three views: login, fleet overview (14 tiles, KPI bar, status LEDs, IST
  timestamps), jet detail (7 metric cards + area chart).
- Runs locally via `npm run dev`; Vite proxies `/api` → production API.
- `buildFleet()` / `buildHistory()` mocks marked with `TODO Phase 5c`.
- An approved swap plan exists (real login, `apiFetch` with 401→logout,
  `flattenHistory` adapter, missing-vs-zero rendering, 10 s polling,
  "reporting X / 14" KPI). **Not yet built** — this is the immediate next task.

### Hardware truths (do not re-litigate; empirically established)
- RS485 signal ground: INVT drives need COM connected; Yaskawa drives leave
  the ground wire unterminated. Missing signal ground was the root cause of
  most early Modbus timeouts. 120 Ω termination at the far end (Jet 29).
- Physics cross-validation is the standard acceptance test for register
  scaling: e.g. 2-pole motor at 33.34 Hz ⇒ ~1000 rpm synchronous; DC bus
  ≈ 560–600 V on a 415 V supply.
- Open items: Yaskawa `dc_voltage` register scaling wrong on F7/A1000 units
  (27 V / 105 V readings — physically impossible); Jet 29 loose ferrule
  (intermittent, electrician queued); power register suspected 0.1 kW units,
  verify on VFD front panel; inverter-room row 2 not yet wired.

---

## 3. Architecture as-built

```
14 VFDs (INVT CHF100A, Yaskawa F7/V1000/A1000)
   │  RS485 / Modbus RTU, daisy chain, slave IDs 1–14
   ▼
Raspberry Pi gateway (poll → validate → SQLite outbox → forward)
   │  HTTPS* POST /data, JWT (gateway user)          *currently plain HTTP — fix in Phase 11
   ▼
FastAPI (routers → services logic → SQLAlchemy)
   │  get_tenant_db(): SET LOCAL app.current_company_id
   ▼
TimescaleDB  (relational catalog + telemetry_data hypertable)
   ▲
   │  GET /machines/live | /{id}/live | /{id}/history | /fleet/summary  (JWT)
React + Vite dashboard (mevion)
```

### Data model (catalog)
```
company ─┬─ users (role, bcrypt hash)
         ├─ department ─┬─ machine_type
         │              └─ machine (machine_type_id, department_id)
         ├─ component_type ── component_type_tag ── tag_definition (name, key, unit, data_type)
         └─ machine ── machine_component_instance (component_type_id)
                              ▲
telemetry_data (hypertable) ──┘  (component_instance_id, tag_definition_id,
                                  value_num/value_text, timestamp, company_id)
```
Domain conventions (ADR-settled): the machine sub-unit entity is named
**component** (reserving "part" for a future spare-parts/inventory module);
process variables (temperature, pressure) attach to a single **Vessel**
component per machine; **VFDs are not components** — the motor/pump is the
component and the VFD's registers are its tags.

### API contract style (ADR-009/010)
- Machine-facing keys are **slugs** (`frequency`, `dc_voltage`, …) from
  `tag_definition.key`; display names (`"Output Frequency"`) are UI labels
  only and may be edited freely by operators.
- Live and history endpoints share the symmetric `tags: dict` shape so the
  frontend renders any tenant's tag set generically.
- Raw SQL via `text()` is the accepted pattern for `DISTINCT ON` and
  `time_bucket()`; always fully parameterised; per-tenant tag resolution via
  JOIN on `tag_definition`, never via literals.

---

## 4. Environments, credentials, workflow

- **Prod:** DigitalOcean droplet `165.22.247.235`, Ubuntu 24.04, Docker
  Compose (`energy-management-api-1` on host port 8001,
  `energy-management-postgres-1`). Deploy = `git pull` + `docker compose up
  --build -d` + `docker compose exec api alembic upgrade head`.
- **Dev:** MacBook Air (`~/Desktop/energy-management`), three terminal tabs
  (Claude Code / compose logs / zsh). Frontend via `npm run dev`.
- **Git:** GitHub `pjbardolia/energy-management`. Server authenticates via SSH
  deploy key. MacBook is the primary editing environment; server-side hotfixes
  are committed *from the server* then pulled locally.
- **Secrets:** `.env` on the server (POSTGRES_*, SECRET_KEY); gateway
  credentials in `config.json` on the Pi. **Never print or commit secrets.**
- ⚠️ **The repo is currently public.** For a commercial SaaS carrying
  infrastructure details this is a liability. Recommendation: make it private
  before Phase 6 ships (one-time GitHub setting; deploy key keeps working).

---

## 5. Hard contracts and invariants — DO NOT BREAK

These couple systems that cannot see each other. Violations produce **silent
data loss**, not errors, because the hypertable's FK constraints were dropped
(TimescaleDB requirement, migration 002).

| # | Contract | Bound parties |
|---|----------|---------------|
| C1 | `component_instance_id` 4–17 ↔ Jets 33,32,16,01,02,03,04,20,19,21,26,27,28,29 (slave 1–14) | Pi `config.json` ↔ DB ↔ `scripts/seed_ssppl.sql` |
| C2 | `tag_definition` IDs 1–7 = rpm, torque, current, dc_voltage, output_voltage, frequency, power | Pi `config.json` ↔ DB ↔ seed |
| C3 | Tag **key slugs** exactly: `rpm, torque, current, dc_voltage, output_voltage, frequency, power` | API ↔ frontend ↔ seed ↔ migration 004 backfill |
| C4 | pgdata volume mounts at **`/home/postgres/pgdata`** (timescaledb-ha's PGDATA parent), *not* `/var/lib/postgresql/data` | docker-compose.yml ↔ data survival |
| C5 | `.gitignore` has `*.sql` with exception `!scripts/*.sql` — seed scripts are source code | git ↔ disaster recovery |
| C6 | All DB timestamps UTC; IST (+5:30) applied only at display | gateway ↔ DB ↔ frontend |
| C7 | TimescaleDB session rules: `expire_on_commit=False`, no `db.refresh()` on hypertable rows, string-form `foreign_keys` in relationships | ORM ↔ hypertable |
| C8 | Machine limit enforcement (once Phase 7 lands) is **server-side** on `POST /machines`; UI disabling is cosmetic only | API ↔ billing integrity |

Phase 9 exists specifically to retire C1/C2 by making the gateway pull its
device map from the API. Until then, changing those IDs requires a matched
edit to the Pi config **and** a gateway restart — coordinate explicitly.

---

## 6. ADR index (docs/architecture-decisions.md)

- **ADR-001–003** — early foundations (FastAPI/Postgres/Docker choices, phase
  structure, migration approach incl. raw-SQL migrations to bypass SQLAlchemy
  enum auto-creation).
- **ADR-004** — "component" naming retained (reserve "part" for future
  spare-parts module); Vessel component per machine for process variables;
  VFDs are tags-sources, not components.
- **ADR-005–007** — Phase 5a repo reorganisation; gateway store-and-forward
  design; systemd service model.
- **ADR-008** — TimescaleDB hypertable + RLS strategy (RLS on catalog tables;
  telemetry isolation at app layer; enforcement deferred to app_role).
- **ADR-009** — Telemetry read API shape: symmetric `tags` dict on live and
  history; long-form SQL + Python pivot; single fleet query reused for
  `/fleet/summary` and filtered in Python for single-machine live.
- **ADR-010** — Tag key slugs: `key` is the machine contract, `name` is the
  human label; unique per (company_id, key); regex `^[a-z][a-z0-9_]*$`;
  never renamed after creation.

New phases below should each land with their own ADR (ADR-011+).

---

## 7. Postmortems — three silent failures (institutional memory)

**7.1 The vanishing database (root cause of all data loss).**
`docker-compose.yml` mounted the pgdata volume at `/var/lib/postgresql/data` —
the official `postgres` image's path. But `timescale/timescaledb-ha` uses
`PGDATA=/home/postgres/pgdata/data`. The volume sat empty (0 B) while the real
database lived in the container's ephemeral layer, so **every
`docker compose down` since project start destroyed the entire database**,
including ~140k telemetry rows. Nothing errored; the loss surfaced only when a
test run made the fresh state obvious. *Lessons:* verify persistence
empirically (`docker system df -v` must show the volume growing; write → down
→ up → read); image documentation over habit; the gateway's SQLite outbox was
the only reason live data survived.

**7.2 The gitignored seed script.**
`.gitignore` line 5 was `*.sql`. Claude Code genuinely wrote
`scripts/seed_ssppl.sql`; `git add` silently ignored it; `git status` showed
clean; the builder's "already exists ✓" claim was true on disk and false in
git. The asset hierarchy therefore had no versioned source of truth and was
reconstructed by hand from the Pi's config file. *Lessons:* "file written" ≠
"file versioned"; check `git check-ignore -v` when a file refuses to appear;
blanket ignore patterns are landmines.

**7.3 The production-destroying smoke test.**
`test_api.py` creates companies/users/machines in whatever DB it targets. Run
against production, it renamed company 1 to "Test Factory …" and orphaned the
hierarchy. Guards now exist: `ALLOW_DESTRUCTIVE_TESTS=1` env gate, and a
**fail-closed** preflight (`TEST_PREFLIGHT_USER/PASSWORD` required; abort if
any non-"test" company exists) that runs **before any write**. An earlier
draft of the guard was inverted (aborted only when *no* test company existed)
and ran *after* the first write — both defects caught in review. *Lessons:*
guards must fail closed; ordering matters; review the guard as adversarially
as the feature.

**Builder-discipline corollary (three incidents):** completion summaries have
claimed corrections that weren't applied (identical plan resubmitted twice),
files that didn't exist, and a self-invented slug mapping replacing a supplied
contract table. Hence §0 rules 2 and 8, and the grep-verification checklists
that reviews now demand. When a plan is rejected with corrections, the next
plan must *demonstrate* each correction, not restate the old text.

---

## 8. Immediate next tasks (Phase 5 close-out)

1. **Frontend live-data swap** — approved plan on file (real `/login`, token in
   React state only, `apiFetch` with 401→logout, `flattenHistory`, `fmt()`
   rendering "—" for missing vs `0.0` for zero, distinct "No data" tile state,
   `reporting X / 14` KPI, 10 s polling with cleanup, error banners, no mock
   fallback). Definition of done: dashboard shows the 13 live jets + Jet 29 as
   no-data, from a cold browser, against production.
2. **Phase 5b — RLS enforcement:** create `app_role` (non-superuser), grant
   least privilege, point the app connection at it, prove cross-tenant reads
   fail at the DB layer even if an app filter is dropped. Design together with
   superadmin bypass (§9.1) — they interact.
3. **Nginx deploy of `frontend/dist`** on port 80 with `/api` reverse proxy;
   then HTTPS via Let's Encrypt (also fixes the gateway's plain-HTTP posting).
4. Housekeeping: gateway user creation documented/scripted (not in seed SQL —
   hashing belongs in app code); delete leftover test rows guard-rails.

---

## 9. Roadmap to SaaS — phases, in dependency order

The ordering below is deliberate: **provisioning (6) → limits (7) → one-click
assets (8) → gateway provisioning (9)** builds the commercial spine before
comfort features. Alarms (10) and ops hardening (11) run as soon as
capacity allows — 11 protects revenue-bearing customer data and should not
slip past the first paying customer.

### 9.1 Phase 6 — Superadmin & company provisioning  *(Pruthvi's ask)*

**Goal:** the platform owner creates customer companies, each with an initial
admin login, from a console — no SQL, no SSH.

*Data/auth:*
- `users.role` gains `superadmin`; `users.company_id` becomes **nullable**
  (migration; superadmin belongs to no tenant).
- JWT for superadmin carries `role=superadmin` and **no** company_id claim.
- New dependency `require_superadmin`; new DB dependency for superadmin
  requests that does **not** set `app.current_company_id` (or sets a bypass
  GUC) — must be co-designed with Phase 5b RLS policies (add
  `OR current_setting('app.is_superadmin', true) = '1'` style clause, or use a
  separate role with BYPASSRLS; document choice as ADR-011).

*Endpoints (`routers/superadmin.py`):*
- `POST /superadmin/companies` — {company_name, address, admin_email,
  admin_password, plan_id} → creates company + its first admin **in one
  transaction**; returns both.
- `GET /superadmin/companies` — all tenants with plan, machine count, user
  count, last-telemetry timestamp (health at a glance).
- `PATCH /superadmin/companies/{id}` — plan change, activate/suspend
  (`company.is_active`; suspended tenants get 403 on login and 401 on data
  ingest — gateway outbox buffers, nothing is lost).
- `POST /superadmin/companies/{id}/users` — additional users for a tenant.
- Audit every superadmin action (see 9.6).

*Frontend:* superadmin console route (visible only to the role): company
table + "New Company" modal + plan selector + suspend toggle + per-company
health chips.

*Definition of done:* from a clean browser, superadmin logs in, creates
"Factory 2" with a plan and an admin login; that admin logs in and sees an
empty (zero-machine) dashboard; SSPPL data is invisible to them; all 38+
smoke tests still pass; ADR-011 written.

### 9.2 Phase 7 — Plans, machine limits, payments  *(Pruthvi's ask)*

**Goal:** what a company pays determines how many machines it may register.

*Data:*
- `plan` table: id, name, `max_machines`, `max_users`, `max_gateways`,
  `data_retention_days`, `history_window_days`, `price_monthly_inr`,
  `is_active`. Seed: e.g. Starter (5 machines), Growth (15), Plant (40),
  Enterprise (custom) — Pruthvi sets real numbers/prices.
- `company.plan_id` (FK), `company.plan_expires_at`.

*Enforcement (server-side, C8):*
- Dependency `enforce_plan_limit("machines")` on `POST /machines` (and the
  Phase 8 quick-create): count active machines for the tenant; if
  `>= plan.max_machines`, return **403 with a machine-readable code**
  (`PLAN_LIMIT_REACHED`, current, limit, plan name) so the UI can render an
  upgrade prompt instead of a generic error. Same pattern for users/gateways.
- `GET /account/usage` — {machines: used/limit, users: used/limit, plan,
  expires_at} for the frontend to show meters and disable "+" buttons
  *cosmetically* (the API remains the enforcer).

*Payments (staged deliberately):*
- **Stage 1 (ship first):** superadmin assigns/renews plans manually after
  offline payment (bank transfer/UPI — realistic for Indian B2B). Zero
  payment-gateway code; full limit enforcement.
- **Stage 2:** Razorpay integration (subscriptions + webhooks) updating
  `plan_id`/`plan_expires_at` automatically; invoice PDFs. Razorpay over
  Stripe — INR-native, UPI/netbanking, Indian-entity friendly.
- Expiry behavior: grace period (e.g. 7 days, banner), then read-only (ingest
  continues, dashboard views work, creation blocked), then suspension. Never
  silently drop telemetry — the outbox will hold it, and data is the product.

*Definition of done:* Factory 2 on Starter can create machine #5 but gets a
clean upgrade-prompting 403 on #6; superadmin bumps the plan; #6 succeeds;
usage meter reflects it live.

### 9.3 Phase 8 — One-click asset creation & machine templates  *(Pruthvi's ask)*

**Goal:** a factory admin builds their whole asset tree from the UI — click,
name it, done — with the DB updated instantly and the telemetry chain ready.

The key design move is a **cascade with templates**, otherwise "add a machine"
is really four coupled creations (machine → component instances → tag
availability → gateway mapping) and one click is impossible.

*Data:*
- `machine_type_component` template table: (machine_type_id,
  component_type_id, default_name, quantity). Seed for Jet Dyeing Machine:
  1× Reel Motor (and later 1× Vessel when temperature/pressure sensors land).
- `machine` gains nullable `slave_id` (int) and `vfd_model` (varchar) — the
  metadata the read API already reserves nulls for, and Phase 9's fuel.
  Unique `(company_id, slave_id)` per gateway once gateways are entities.

*Endpoints:*
- `POST /departments` (exists) — UI modal, one field.
- `POST /machines/quick` — {name, department_id, machine_type_id, slave_id?,
  vfd_model?} → **one transaction**: create machine, instantiate every
  template component as `machine_component_instance`, return the full tree
  (machine + instances with IDs). Plan-limit dependency applied.
- `POST /machine-types` / `POST /component-types` remain for the "manage
  catalog" screen; sensible defaults seeded per company at provisioning time
  (copy a global template catalog into the tenant on company creation — add
  to Phase 6's create-company transaction).
- `DELETE`/`PATCH` with care: block deleting a machine that has telemetry
  (soft-delete `is_active` flag instead) — history must never orphan.

*Frontend:* "Assets" page — tree (Department → Machine → Components) with
"+ Add Department", "+ Add Machine" (wizard: name, type, department,
slave_id, VFD model), inline component list auto-populated from the template.
Show plan usage meter beside "+ Add Machine".

*Definition of done:* Factory 2's admin creates "Dyeing" and "Jet 07" in two
clicks; `machine`, `machine_component_instance` rows exist with correct
company_id; the machine appears on the fleet dashboard as a no-data tile
immediately; SSPPL flow re-tested unchanged.

### 9.4 Phase 9 — Gateway provisioning API  *(architect's addition — critical)*

**Why:** today, contracts C1/C2 live in a JSON file on a Raspberry Pi. Every
one-click machine creation is a lie at scale if someone must still SSH into an
edge device and hand-edit IDs. This phase is what makes factory #2 (and #200)
installable by an electrician with a claim code.

*Design:*
- `gateway` entity: id, company_id, name, `device_token_hash` (long-lived
  credential, revocable — replaces the human-style gateway user), last_seen,
  sw_version. Claim flow: superadmin/admin generates a one-time claim code →
  fresh Pi image posts it to `POST /gateways/claim` → receives its device
  token → done.
- `GET /gateway/config` (device-token auth): returns the device map derived
  from the DB — for each machine with a `slave_id`: slave_id, vfd_model
  (register-map selector), component_instance_id, tag key→id map, polling
  interval. The gateway polls this every N minutes and hot-reloads; a machine
  created in the UI starts being polled **without touching the Pi**.
- `POST /gateway/heartbeat` — uptime, outbox depth, per-slave comm status;
  feeds monitoring (9.5) and a "gateway offline" alarm (9.6... see 10).
- Batch ingest: `POST /data/batch` accepting an array — the outbox currently
  drains one POST per row; batching turns multi-hour backlogs from minutes
  into seconds.
- Retires C1/C2: the DB becomes the single source of truth; the Pi config
  shrinks to {api_url, device_token, serial settings}.

*Definition of done:* change a machine's slave_id in the UI → gateway polls
the new address within one refresh cycle, no SSH; revoked token stops ingest;
outbox backlog of 10k rows drains via batch in < 30 s.

### 9.5 Phase 10 — Alarms & notifications  *(architect's addition)*

The platform's pitch is "know before it costs you." Today Jet 29 has been
silently dark behind a loose ferrule — a communication-loss alarm would have
paged someone the first hour. Minimum viable engine:

- `alarm_rule`: company_id, machine_id/nullable-for-fleet, tag key, condition
  (>, <, ==, stale_for), threshold, duration, severity, is_active.
- Evaluator (start simple: a loop in the API container every poll interval
  over `/machines/live`-equivalent data; graduate to a worker later).
- `alarm_event` log: fired_at, cleared_at, acknowledged_by.
- Built-in rules per machine: **communication loss** (no reading for X min)
  and gateway offline (from heartbeat).
- Notifications: email first; **WhatsApp Business API second** — in Indian
  factories WhatsApp is the channel that actually gets read. In-app banner +
  tile badge on the dashboard.

### 9.6 Phase 11 — Operational hardening  *(architect's addition — do not defer past first paying customer)*

Directly informed by §7:
- **Backups:** nightly `pg_dump` to DigitalOcean Spaces (off-droplet),
  14–30 day retention, plus weekly droplet snapshot. **Quarterly restore
  drill** — an untested backup is a hope, not a backup.
- **Monitoring:** uptime check on `/health`; disk/CPU alerts; gateway
  heartbeat freshness; "telemetry stale > 15 min" fleet alarm. (UptimeRobot
  or a cron + WhatsApp is fine at this scale.)
- **HTTPS everywhere** (with 8.3's Nginx): API + dashboard + gateway posting.
- **Staging:** a second compose project (different ports/volumes) on the same
  droplet; destructive tests run only there; prod deploys only after staging
  smoke passes.
- **Audit log** table: actor, role, action, entity, before/after JSON,
  timestamp — mandatory for superadmin actions, plan changes, deletions.
  B2B customers will ask for this.
- **API versioning** (`/api/v1/...`) before any external integration exists;
  request rate limiting; structured JSON logs with request IDs.
- **Data lifecycle per plan:** TimescaleDB compression policy (compress
  chunks > 7 days), continuous aggregates (1-min/1-hour rollups powering
  history at long ranges), retention job dropping raw chunks past
  `plan.data_retention_days` while keeping rollups. This is also the honest
  cost model behind plan pricing.

### 9.7 Later (post-SaaS-spine backlog, roughly ordered)
Reports (weekly energy/uptime PDF per machine, per department, ₹-cost via
tariff input) → PWA manifest + push (installable dashboard, the mobile app
before the mobile app) → RBAC depth (manager/operator/viewer roles; operators
see, admins create) → onboarding wizard (first-login guided setup for new
companies) → demo tenant with simulated telemetry (sales tool) → multi-gateway
per company & multi-plant hierarchy (Company → Plant → Department) → energy
KPIs (kWh/kg needs production input — the wedge into production tracking) →
predictive maintenance (current/vibration trends) → recipe OCR & costing
(textile module, per original vision) → white-labeling.

---

## 10. Definition-of-done, globally

A phase is done when: migrations apply **and downgrade** cleanly; smoke tests
extended and green (38+ and counting); grep-verifiable checklist from the
approved plan passes; deployed to prod (via staging once it exists); seed
script updated if catalog shape changed; ADR written; handoff context
document updated. "The builder said it's done" is not a definition of done —
§7 is why.

---

## 11. One-paragraph orientation for a fresh AI session

You're building mevion, a multi-tenant industrial-IoT SaaS whose first tenant
is the owner's own dyeing factory (14 VFD-instrumented jet machines →
Raspberry Pi store-and-forward gateway → FastAPI/TimescaleDB → React
dashboard). The read API is live against real production telemetry; the
frontend mock-swap is the immediate task; then superadmin provisioning,
plan-based machine limits, one-click asset creation with machine templates,
and an API-driven gateway config that removes the last hand-edited file from
the loop. Work plan-first, verify everything with grep and git, respect the
contracts in §5, and read the postmortems in §7 before assuming anything is
persisted, committed, or true because a summary said so.
