# alembic/env.py — Alembic migration environment.
#
# This file is run by every Alembic command.  Its job is to:
#   1. Point Alembic at our database (reads DATABASE_URL from the environment,
#      same as the API server does — no credentials in source control).
#   2. Import our SQLAlchemy Base and all models so that autogenerate
#      ("alembic revision --autogenerate") can diff the live schema against
#      the ORM definitions and produce accurate migration scripts.
#   3. Support both "online" mode (connects to a live DB and runs migrations
#      immediately) and "offline" mode (emits SQL statements to stdout for
#      review or manual execution — useful for production audits).

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# --- Import our ORM Base and all models ---
#
# We import Base from database.py (which reads DATABASE_URL from env) and
# import all models so their table definitions are registered with Base.metadata
# before autogenerate inspects it.
#
# IMPORTANT: every new model file must be imported here (or imported via the
# models/__init__.py wildcard) or autogenerate will not detect it.
from database import Base

# Import all model classes — this registers their __tablename__ with Base.metadata
from models import (
    Company,
    Department,
    MachineType,
    Machine,
    ComponentType,
    MachineComponentInstance,
    User,
    TagDefinition,
    ComponentTypeTag,
    TelemetryData,
)

# --- Alembic Config object ---
# Provides access to values in alembic.ini.
config = context.config

# Read logging configuration from alembic.ini and apply it.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tell autogenerate what the "target" schema looks like — our ORM models.
target_metadata = Base.metadata

# --- Database URL ---
# Override whatever sqlalchemy.url is set in alembic.ini with the real URL
# from the environment.  This keeps credentials out of the repo.
database_url = os.environ["DATABASE_URL"]
config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """
    Offline mode: generate SQL statements without connecting to the database.
    Useful for reviewing what a migration will do, or for environments where
    the migration must be applied manually by a DBA.

    Usage: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Online mode: connect to the database and apply migrations immediately.
    This is the normal mode used by `alembic upgrade head`.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool avoids connection-pool reuse across migrations — each
        # migration step gets a fresh connection, which is safer for DDL.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
