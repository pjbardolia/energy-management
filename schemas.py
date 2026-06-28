from pydantic import BaseModel

class CompanyCreate(BaseModel):
    company_name: str
    address: str

class CompanyResponse(BaseModel):
    id: int
    company_name: str
    address: str

    class Config:
        from_attributes = True
