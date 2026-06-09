from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum

from app.core.sanitize import SafeStr, SafeOptStr


class StaffRole(str, Enum):
    """Allowed employee-record roles. These are HR labels on Staff records, not
    login privilege levels (login accounts are managed via /api/auth/team)."""
    owner = "owner"
    manager = "manager"
    cashier = "cashier"
    waiter = "waiter"
    kitchen = "kitchen"


class StaffCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: SafeStr
    role: StaffRole
    phone: SafeOptStr = None
    email: SafeOptStr = None
    salary: float = Field(default=0.0, ge=0, le=100_000_000)
    join_date: Optional[datetime] = None


class StaffUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: SafeOptStr = None
    role: Optional[StaffRole] = None
    phone: SafeOptStr = None
    salary: Optional[float] = Field(default=None, ge=0, le=100_000_000)
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
