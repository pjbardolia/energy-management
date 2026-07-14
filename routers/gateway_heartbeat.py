"""
Gateway heartbeat router.

POST /gateway/heartbeat  — called by Pi after every poll cycle (gateway JWT)
GET  /gateway/status     — called by frontend dashboard (user JWT)

Tenant isolation: company_id comes from the JWT, never from the request body.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import get_db
from auth import get_current_user
from models.gateway_heartbeat import GatewayHeartbeat
from schemas.gateway_heartbeat import HeartbeatCreate, GatewayStatusResponse

router = APIRouter(prefix="/gateway", tags=["gateway"])

# Gateway is considered offline if no heartbeat received for this long.
# Matches STALE_THRESHOLD_MS = 2 * 60 * 1000 on the frontend.
OFFLINE_THRESHOLD_SECONDS = 120  # 2 minutes


@router.post("/heartbeat", status_code=204)
def post_heartbeat(
    payload:      HeartbeatCreate,
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    Pi gateway calls this at the end of every poll cycle.
    Upserts a single row per company — always the latest heartbeat.
    Returns 204 No Content on success.
    """
    # company_id always comes from the validated JWT, never from the request body.
    company_id = current_user["company_id"]

    # PostgreSQL upsert: insert the first heartbeat or update the existing row.
    # The constraint name must match __table_args__ in models/gateway_heartbeat.py.
    stmt = pg_insert(GatewayHeartbeat).values(
        company_id        = company_id,
        last_seen         = datetime.now(timezone.utc),
        poll_duration_sec = payload.poll_duration_sec,
        machines_polled   = payload.machines_polled,
        machines_failed   = payload.machines_failed,
    ).on_conflict_do_update(
        constraint = "uq_gateway_heartbeat_company",
        set_ = {
            "last_seen":         datetime.now(timezone.utc),
            "poll_duration_sec": payload.poll_duration_sec,
            "machines_polled":   payload.machines_polled,
            "machines_failed":   payload.machines_failed,
        }
    )

    db.execute(stmt)
    db.commit()
    # FastAPI returns 204 No Content automatically when the function returns None.


@router.get("/status", response_model=GatewayStatusResponse)
def get_gateway_status(
    db:           Session = Depends(get_db),
    current_user: dict    = Depends(get_current_user),
):
    """
    Frontend polls this to show the gateway online/offline badge.
    Returns is_online=False with null last_seen if no heartbeat has been received yet.
    """
    company_id = current_user["company_id"]

    row = db.query(GatewayHeartbeat).filter(
        GatewayHeartbeat.company_id == company_id
    ).first()

    if not row:
        # No heartbeat has ever been received for this company.
        return GatewayStatusResponse(
            last_seen         = None,
            seconds_ago       = None,
            is_online         = False,
            poll_duration_sec = None,
            machines_polled   = None,
            machines_failed   = None,
        )

    now       = datetime.now(timezone.utc)
    # Normalise the stored timestamp to UTC in case the column is returned
    # as a naive datetime by the driver (PostgreSQL TIMESTAMPTZ is always UTC,
    # but SQLAlchemy may strip the tzinfo depending on dialect settings).
    last_seen = (
        row.last_seen.replace(tzinfo=timezone.utc)
        if row.last_seen.tzinfo is None
        else row.last_seen
    )
    seconds_ago = int((now - last_seen).total_seconds())
    is_online   = seconds_ago < OFFLINE_THRESHOLD_SECONDS

    return GatewayStatusResponse(
        last_seen         = row.last_seen,
        seconds_ago       = seconds_ago,
        is_online         = is_online,
        poll_duration_sec = row.poll_duration_sec,
        machines_polled   = row.machines_polled,
        machines_failed   = row.machines_failed,
    )
