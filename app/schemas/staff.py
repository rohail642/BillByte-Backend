from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime, date as Day
from enum import Enum

from app.core.sanitize import SafeStr, SafeOptStr


class StaffRole(str, Enum):
    """Allowed employee-record roles. These are HR job titles on Staff records,
    not login privilege levels (login accounts are managed via /api/auth/team).
    The lowercase values are kept so records saved before the job-title list
    still validate when edited."""
    head_chef = "Head Chef"
    sous_chef = "Sous Chef"
    chef = "Chef"
    waiter_t = "Waiter"
    cashier_t = "Cashier"
    manager_t = "Manager"
    delivery = "Delivery"
    cleaner = "Cleaner"
    kitchen_t = "Kitchen"
    barista = "Barista"
    helper = "Helper"
    other = "Other"
    # legacy values
    owner = "owner"
    manager = "manager"
    cashier = "cashier"
    waiter = "waiter"
    kitchen = "kitchen"


AttendanceStatus = Literal["present", "half_day", "leave", "off"]
PayMethod = Literal["cash", "upi", "bank"]


class StaffCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: SafeStr = Field(min_length=1, max_length=200)
    role: StaffRole
    phone: SafeOptStr = Field(default=None, max_length=20)
    email: SafeOptStr = Field(default=None, max_length=200)
    salary: float = Field(default=0.0, ge=0, le=100_000_000)
    join_date: Optional[datetime] = None
    weekly_off: Optional[int] = Field(default=None, ge=0, le=6)
    notes: SafeOptStr = Field(default=None, max_length=500)


class StaffUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: SafeOptStr = Field(default=None, min_length=1, max_length=200)
    role: Optional[StaffRole] = None
    phone: SafeOptStr = Field(default=None, max_length=20)
    salary: Optional[float] = Field(default=None, ge=0, le=100_000_000)
    join_date: Optional[datetime] = None
    weekly_off: Optional[int] = Field(default=None, ge=0, le=6)
    notes: SafeOptStr = Field(default=None, max_length=500)
    is_active: Optional[bool] = None


class StaffOut(BaseModel):
    id: int
    name: str
    role: str
    phone: Optional[str]
    email: Optional[str]
    salary: float
    # Today's attendance (India date): present | half_day | leave | off |
    # weekly_off (unmarked on their weekly off day) | unmarked
    status: str
    is_active: bool
    join_date: Optional[datetime]
    weekly_off: Optional[int] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class AttendanceMark(BaseModel):
    status: AttendanceStatus
    notes: SafeOptStr = Field(default=None, max_length=300)
    date: Optional[Day] = None  # defaults to today (India time)


class BulkAttendanceEntry(BaseModel):
    staff_id: int
    status: Optional[AttendanceStatus] = None  # None clears the day
    notes: SafeOptStr = Field(default=None, max_length=300)


class BulkAttendance(BaseModel):
    date: Optional[Day] = None
    entries: List[BulkAttendanceEntry] = Field(min_length=1, max_length=500)


class AdvanceCreate(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    date: Optional[Day] = None
    note: SafeOptStr = Field(default=None, max_length=200)


class PayoutCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    paid_amount: Optional[float] = Field(default=None, ge=0, le=100_000_000)  # defaults to net payable
    method: PayMethod = "cash"
    paid_on: Optional[Day] = None
    note: SafeOptStr = Field(default=None, max_length=200)


class StaffSettings(BaseModel):
    paid_leaves_per_month: int = Field(ge=0, le=31)
