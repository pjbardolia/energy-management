from fastapi import FastAPI
from sqlalchemy.orm import Session

# Import database engine and Session object
from database import engine, SessionLocal

# Import Base class
from database import Base

# Import all models
from models import *

from schemas import CompanyCreate, CompanyResponse
from schemas.auth import LoginRequest, Token
from security import verify_password, create_access_token
from schemas.user import UserCreate, UserResponse
from machine_router import router as machine_router
from department_router import router as department_router
from machine_component_router import router as machine_component_router
from data_router import router as data_router
from passlib.context import CryptContext

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Energy Management API"
)

app.include_router(machine_router)
app.include_router(department_router)
app.include_router(machine_component_router)
app.include_router(data_router)

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

@app.get("/")
def root():
    return {
        "message": "Energy Management API is running"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }

@app.post("/companies", response_model=CompanyResponse)
def create_company(company: CompanyCreate):

    db: Session = SessionLocal()

    db_company = Company(
        company_name=company.company_name,
        address=company.address
    )

    db.add(db_company)
    db.commit()
    db.refresh(db_company)

    return db_company

@app.get("/companies")
def get_companies():

    db: Session = SessionLocal()

    companies = db.query(Company).all()

    return companies

@app.post("/users", response_model=UserResponse)
def create_user(user: UserCreate):

    db: Session = SessionLocal()

    hashed_password = pwd_context.hash(user.password)

    db_user = User(
        username=user.username,
        password_hash=hashed_password,
        role=user.role,
        company_id=user.company_id
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return db_user

@app.post("/login", response_model=Token)
def login(data: LoginRequest):

    db: Session = SessionLocal()

    user = db.query(User).filter(
        User.username == data.username
    ).first()

    if user is None:
        return {
            "access_token": "",
            "token_type": "invalid user"
        }

    if not verify_password(
        data.password,
        user.password_hash
    ):
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
