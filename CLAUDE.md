# CLAUDE.md — Energy Management / Industrial IoT SaaS Platform

This file gives Claude Code the context and rules for this project. Read it before making changes.

## What this project is
A generic, database-driven, multi-tenant Industrial IoT SaaS platform for factories —
monitoring machines, energy, water, production, and telemetry. The first deployment target
is Shiv Shakti Prints Pvt. Ltd. (textile dyeing), but the architecture MUST stay generic:
any factory should be configurable through data alone, with NO per-factory code changes.

## Tech stack
- Python 3.13, FastAPI, SQLAlchemy ORM, PostgreSQL
- JWT auth (python-jose); password hashing (passlib + bcrypt — KEEP the `bcrypt==4.0.1` pin,
  newer bcrypt breaks passlib)
- Docker + docker-compose for local development and deployment
- Local dev: `docker compose up --build`, then Swagger at http://localhost:8001/docs
- Smoke test: `docker compose exec api python test_api.py` (goal: "0 unexpected failures")

## Architecture principles (do not violate)
- Generic schema only: Machine -> MachineComponentInstance -> TagDefinition -> TelemetryData.
  NEVER create per-machine tables (no JetData, PumpData, MotorData, etc.).
- Multi-tenant: every tenant-scoped row carries `company_id`.
- API-first: every feature is exposed via REST.

## Current state (checkpoint)
WORKING: auth (POST /users, POST /login), companies, departments, and all GET endpoints.

BROKEN — the unfinished half of a refactor, and the immediate next task. The MODELS were
normalized but the SCHEMAS and ROUTERS were left on the old denormalized shape:
- POST /machines (500): `Machine` model uses `machine_type_id` (FK) + a `machine_type`
  relationship and has NO `department_id` column, but `MachineCreate` / `machine_router`
  still send `machine_type` (str) and `department_id` (int).
- POST /machine-components (500): `MachineComponentInstance` uses `component_type_id` (FK),
  but `MachineComponentCreate` / `machine_component_router` send `component_type` (str).
- POST /data (500): `TelemetryData` model is generic (`component_instance_id`,
  `tag_definition_id`, `value`), but `DataCreate` / `data_router` still use old wide columns
  (`machine_id`, `output_frequency`, `dc_bus_voltage`, etc.).

## Known cleanup items (not yet done)
- Refactor is half-finished: old flat files (`models.py`, `schemas.py`, `data_old.py`,
  root `user.py`, the `*_router.py` files) coexist with the new `models/` and `schemas/`
  packages. The `routers/` package is empty; routers are still imported from the flat
  `*_router.py` files in `main.py`.
- Every endpoint opens `db = SessionLocal()` and never closes it (connection leak). Should
  use a `get_db()` dependency with `yield` / `finally`.
- `requirements.txt` should be fully version-pinned (`==`) for reproducible builds.
- No Alembic migrations yet; uses `Base.metadata.create_all()`, which cannot ALTER existing
  tables. Schema changes will need Alembic.
- JWT is issued but NOT enforced on endpoints, and there is no `company_id` scoping yet.

## Development rules (follow for every change)
- Comment every important line; write beginner-friendly explanations. No black-box code.
- Explain the reasoning behind each change.
- Preserve backward compatibility. Never delete working code without explicit approval.
- Provide testing steps AND rollback steps before any major change.
- Validate inputs on every endpoint.
- Use SQLAlchemy ORM, PostgreSQL, FastAPI. Keep the multi-company architecture.
- Keep the current folder structure. Write production-ready code.
