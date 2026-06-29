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

# Routers — Phase 1 (were working before Phase 2)
from department_router import router as department_router

# Routers — Phase 2 (fixed or newly created)
from machine_type_router import router as machine_type_router
from machine_router import router as machine_router
from component_type_router import router as component_type_router
from machine_component_router import router as machine_component_router
from tag_definition_router import router as tag_definition_router
from data_router import router as data_router

from passlib.context import CryptContext

# Create all tables on startup if they don't already exist.
# Note: create_all cannot ALTER existing columns — use Alembic for schema changes.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Energy Management API",
    description=(
        "Generic multi-tenant Industrial IoT SaaS platform. "
        "Phase 2: catalogue + telemetry endpoints fully wired up."
    ),
    version="0.2.0"
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
def get_companies(db: Session = Depends(get_db)):
    return db.query(Company).all()


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
    user = db.query(User).filter(
        User.username == data.username
    ).first()

    if user is None:
        return {
            "access_token": "",
            "token_type": "invalid user"
        }

    if not verify_password(data.password, user.password_hash):
        return {
            "access_token": "",
            "token_type": "wrong password"
        }

    token = create_access_token(
        {
            "sub": user.username,
            "role": user.role
        }
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }
