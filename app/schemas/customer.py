from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CustomerCreate(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    notes: Optional[str] = None


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None


class RedeemPointsRequest(BaseModel):
    points: int


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str
    email: Optional[str]
    total_visits: int
    total_spent: float
    loyalty_points: int
    notes: Optional[str]
    last_visit_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True
