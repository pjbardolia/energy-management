from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# Import database engine (for create_all) and the get_db dependency
from database import engine, get_db

# Import Base class — needed to run create_all on startup
from database import Base

# Import all models so SQLAlchemy registers their tables with Base
from models import *

# Schemas for the endpoints defined directly in this file
from schemas import CompanyCreate, CompanyResponse
from schemas.auth import LoginRequest, Token
from schemas.user import UserCreate, UserResponse

# Security helpers
from security import verify_password, create_access_token

# Phase 4d: JWT auth dependency — used to protect GET /companies
from auth import get_current_user

# Routers — all now live in the routers/ package
from routers.department_router import router as department_router
from routers.machine_type_router import router as machine_type_router
from routers.machine_router import router as machine_router
from routers.component_type_router import router as component_type_router
from routers.machine_component_router import router as machine_component_router
from routers.tag_definition_router import router as tag_definition_router
from routers.data_router import router as data_router

from passlib.context import CryptContext

# create_all() is guarded by DEVELOPMENT_MODE so it only runs locally.
#
# In production, Alembic owns the schema — running `alembic upgrade head`
# (via the `migrate` service in docker-compose.yml) is the only way schema
# changes are applied.  create_all() cannot ALTER existing columns, so it
# would silently miss any migration applied after the initial table creation.
#
# In local development (DEVELOPMENT_MODE=true in docker-compose.yml),
# create_all() acts as a fast safety net: if somehow Alembic didn't run, the
# tables still appear so the API starts.  It is never harmful to run it after
# Alembic has already created the tables (it is a no-op on existing tables).
import os
if os.getenv("DEVELOPMENT_MODE", "").lower() == "true":
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Energy Management API",
    description=(
        "Generic multi-tenant Industrial IoT SaaS platform. "
        "Phase 4d: JWT hardening, app-layer tenant filtering, RLS foundation."
    ),
    version="0.4.4"
)

# Register all routers — order determines Swagger display order
app.include_router(department_router)          # POST/GET /departments
app.include_router(machine_type_router)        # POST/GET /machine-types
app.include_router(machine_router)             # POST/GET /machines
app.include_router(component_type_router)      # POST/GET /component-types
app.include_router(machine_component_router)   # POST/GET /machine-components
app.include_router(tag_definition_router)      # POST/GET /tag-definitions
app.include_router(data_router)                # POST/GET /data

# Password hashing context — bcrypt==4.0.1 is pinned in requirements.txt
# because newer bcrypt versions break passlib's internal API.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


@app.get("/")
def root():
    return {"message": "Energy Management API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Company endpoints (defined here rather than a separate router because
# Company is the top-level multi-tenant anchor — simple enough to stay inline)
# ---------------------------------------------------------------------------

@app.post("/companies", response_model=CompanyResponse, status_code=201)
def create_company(company: CompanyCreate, db: Session = Depends(get_db)):
    db_company = Company(
        company_name=company.company_name,
        address=company.address
    )

    db.add(db_company)

    try:
        db.commit()
    except IntegrityError:
        # Roll back before returning so the poisoned transaction does not
        # block the next operation on this session.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create company — a company with this name may already exist."
        )

    db.refresh(db_company)
    return db_company


@app.get("/companies", response_model=list[CompanyResponse])
def get_companies(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Filter to only the authenticated user's company so tenants never see
    # each other.  A platform-admin view (all companies) is a future endpoint
    # that will require a separate admin role.
    return db.query(Company).filter(
        Company.id == current_user["company_id"]
    ).all()


# ---------------------------------------------------------------------------
# User / auth endpoints
# ---------------------------------------------------------------------------

@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    hashed_password = pwd_context.hash(user.password)

    db_user = User(
        username=user.username,
        password_hash=hashed_password,
        role=user.role,
        company_id=user.company_id
    )

    db.add(db_user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create user — username may already be taken, or company_id does not exist."
        )

    db.refresh(db_user)
    return db_user


@app.post("/login", response_model=Token)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    # Intentionally use the same 401 message for both "user not found" and
    # "wrong password" — revealing which one is true would help attackers
    # enumerate valid usernames.
    user = db.query(User).filter(User.username == data.username).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
        )

    # Phase 4d: company_id is now embedded in the token so every downstream
    # dependency can read it without an extra DB round-trip.
    token = create_access_token({
        "sub": user.username,
        "role": user.role,
        "company_id": user.company_id,   # tenant anchor — validated on every request
    })

    return {"access_token": token, "token_type": "bearer"}
