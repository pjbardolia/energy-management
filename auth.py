# FastAPI security dependencies — JWT validation and tenant DB isolation.
#
# Two dependency functions live here:
#
#   get_current_user(token, db) → dict
#       Validates a Bearer JWT from the Authorization header.
#       Verifies the token signature, checks the user still exists in DB,
#       and confirms the company_id in the token matches the DB record.
#       Returns {"id", "username", "company_id", "role"} as a plain dict.
#
#   get_tenant_db(current_user, db) → Session
#       Wraps get_db() by first executing:
#           SET LOCAL app.current_company_id = <company_id>
#       This writes the tenant ID into the PostgreSQL session variable that
#       the RLS policies (migration 003) read on every query.
#
# Dependency DAG per request:
#
#   get_db()                    (1 session, created once per request)
#     └── get_current_user()    (1 user lookup, result cached by FastAPI)
#           └── get_tenant_db() (SET LOCAL, then yields same session)
#
# FastAPI deduplicates dependencies by function identity — get_db() and
# get_current_user() are each called exactly once even when declared in
# multiple places on the same handler.

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db
from security import decode_access_token
from models import User

# OAuth2PasswordBearer extracts the token from the Authorization: Bearer header.
# tokenUrl is the path the Swagger UI "Authorize" button will POST to — it is
# metadata only and does not change where login actually lives.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Validate a Bearer JWT and return the authenticated user as a dict.

    Steps:
    1. decode_access_token() verifies signature and expiry → HTTP 401 on failure.
    2. Re-fetch the user from the DB so stale/deleted-user tokens are rejected.
    3. Confirm the company_id encoded in the token matches the DB record
       (guards against tokens issued before a company re-assignment).

    Returns:
        {"id": int, "username": str, "company_id": int, "role": str}
    """
    # Step 1 — cryptographic verification (signature + expiry + sub claim).
    payload = decode_access_token(token)

    username: str = payload.get("sub")
    token_company_id: int = payload.get("company_id")

    # Step 2 — live DB check so revoked / deleted users are caught immediately.
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User '{}' not found — token is no longer valid.".format(username),
        )

    # Step 3 — company_id tampering / stale-token guard.
    # If the user's company changed after the token was issued, the company_id
    # embedded in the token no longer matches — require a fresh login.
    if user.company_id != token_company_id:
        raise HTTPException(
            status_code=401,
            detail="Token company_id does not match the database.  Please log in again.",
        )

    return {
        "id": user.id,
        "username": user.username,
        "company_id": user.company_id,
        "role": user.role,
    }


def get_tenant_db(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Yield a DB session pre-loaded with the current tenant's company_id.

    Before the route handler runs, this dependency executes:
        SET LOCAL app.current_company_id = <company_id>

    SET LOCAL is transaction-scoped: the variable is visible for the
    lifetime of the current transaction and is cleared automatically on
    COMMIT or ROLLBACK.  This means after db.commit() inside a handler,
    subsequent operations in that same request run in a new implicit
    transaction that does NOT have the variable set — but application-layer
    WHERE filters already provide isolation for those reads.

    NOTE — dev limitation (Phase 4d):
        The Docker Compose API container connects as POSTGRES_USER, which is
        a PostgreSQL superuser.  PostgreSQL superusers bypass RLS even with
        FORCE ROW LEVEL SECURITY set; the SET LOCAL has no enforcement effect
        on superuser connections.  Full RLS enforcement is activated in Phase 5
        when a dedicated non-superuser application role is introduced.
        Application-layer WHERE company_id filters in the routers provide
        the actual multi-tenant isolation in Phase 4d.
    """
    cid = current_user["company_id"]

    # Execute SET LOCAL before yielding so every subsequent query in this
    # handler runs with app.current_company_id visible to RLS policies.
    db.execute(text("SET LOCAL app.current_company_id = :cid"), {"cid": cid})

    try:
        yield db
    finally:
        # SET LOCAL is already cleared by any COMMIT or ROLLBACK the handler
        # issued.  We reset it explicitly here to cover the case where the
        # handler ended without committing (e.g. a read-only handler) and the
        # connection is about to be returned to the pool.
        try:
            db.execute(text("SET LOCAL app.current_company_id = ''"))
        except Exception:
            # If the session is already closed or the connection is gone,
            # there is nothing to clear — the pool handles cleanup.
            pass
