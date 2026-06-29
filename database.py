from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

#DATABASE_URL = "postgresql://admin:Pruthvi%402026%21iot@postgres:5432/iot_platform"

import os
DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    # expire_on_commit=False prevents SQLAlchemy from expiring all ORM
    # attributes immediately after db.commit().  The default (True) causes
    # FastAPI's response serialization to trigger a lazy DB reload for every
    # attribute — which breaks on the telemetry_data hypertable because
    # TimescaleDB reroutes rows to internal chunk tables and the reload
    # SELECT returns no rows (ObjectDeletedError).
    # With False, committed attributes stay in memory and are readable
    # directly from the instance without another round-trip.  This is the
    # pattern recommended by the FastAPI docs for SQLAlchemy integration.
    expire_on_commit=False,
)

Base = declarative_base()


# -----------------------------------------------------------------------
# get_db() — FastAPI dependency for safe database session management.
#
# Usage in a router:
#   from fastapi import Depends
#   from database import get_db
#
#   @router.post("/something")
#   def create_something(payload: SomeCreate, db: Session = Depends(get_db)):
#       ...
#
# Why use this instead of SessionLocal() directly?
#   - The 'yield' style (called a "context-manager dependency" in FastAPI)
#     guarantees the session is ALWAYS closed after the request finishes,
#     even if an unhandled exception is raised mid-handler.
#   - Without this, every request that opens a session but crashes before
#     db.close() leaks a PostgreSQL connection — the pool exhausts quickly
#     under load.
# -----------------------------------------------------------------------
def get_db():
    # Open a new session for this request
    db = SessionLocal()
    try:
        # Hand the session to the route handler
        yield db
    finally:
        # Always close — runs even if the handler raises an exception
        db.close()
