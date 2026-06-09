from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from app.core.sanitize import SafeStr, SafeOptStr


class CustomerCreate(BaseModel):
    name: SafeStr
    phone: SafeStr
    email: SafeOptStr = None
    notes: SafeOptStr = None


class CustomerUpdate(BaseModel):
    name: SafeOptStr = None
    phone: SafeOptStr = None
    email: SafeOptStr = None
    notes: SafeOptStr = None


class RedeemPointsRequest(BaseModel):
    points: int = Field(ge=0)


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
