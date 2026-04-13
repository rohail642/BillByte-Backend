from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class StaffCreate(BaseModel):
    name: str
    role: str
    phone: Optional[str] = None
    email: Optional[str] = None
    salary: float = 0.0
    join_date: Optional[datetime] = None


class StaffUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    salary: Optional[float] = None
    status: Optional[str] = None
    is_active: Optional[bool] = None


class StaffOut(BaseModel):
    id: int
    name: str
    role: str
    phone: Optional[str]
    email: Optional[str]
    salary: float
    status: str
    is_active: bool
    join_date: Optional[datetime]

    class Config:
        from_attributes = True
