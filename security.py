from datetime import datetime, timedelta
from jose import jwt, JWTError
from jose.exceptions import ExpiredSignatureError
from passlib.context import CryptContext
from fastapi import HTTPException
import os

# ---------------------------------------------------------------------------
# Startup guard — fail loudly if SECRET_KEY is absent or empty.
#
# Using os.environ.get() (returns None) instead of os.environ[] (raises
# KeyError) so we can emit a descriptive RuntimeError rather than a bare
# traceback.  An empty SECRET_KEY would sign tokens with "" which means
# any attacker can forge valid tokens for any user.
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY environment variable is not set or is empty.  "
        "Add it to your .env file and restart the container.  "
        "A missing key would allow forged JWTs — the server refuses to start."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60   # tokens expire after 1 hour

# Passlib CryptContext wraps bcrypt.  bcrypt==4.0.1 is pinned in
# requirements.txt — newer versions break passlib's internal API.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if plain_password matches the stored bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """Encode a JWT containing the caller-supplied claims plus exp and iat.

    'data' must include at minimum:
        {"sub": <username>, "company_id": <int>, "role": <str>}

    iat (issued-at) is added here so that downstream systems can detect
    tokens that were issued before a password change if needed in the future.
    exp (expiry) is added here and enforced by decode_access_token().
    """
    to_encode = data.copy()
    now = datetime.utcnow()
    to_encode.update({
        "iat": now,                                                    # when issued
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),   # when it dies
    })
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT.  Raises HTTP 401 for any failure.

    Failure cases handled:
    - Expired token           → ExpiredSignatureError → 401
    - Invalid signature       → JWTError             → 401
    - Missing 'sub' claim     → explicit check        → 401

    On success, returns the full payload dict so callers can read
    sub, company_id, role, iat, and exp.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        # Raised when 'exp' is in the past — tell the client to log in again.
        raise HTTPException(status_code=401, detail="Token has expired.  Please log in again.")
    except JWTError:
        # Raised for tampered signature, wrong algorithm, malformed token, etc.
        raise HTTPException(status_code=401, detail="Token signature is invalid.")

    # 'sub' is the username claim — every token we issue must have it.
    if payload.get("sub") is None:
        raise HTTPException(status_code=401, detail="Token is missing the 'sub' claim.")

    return payload
