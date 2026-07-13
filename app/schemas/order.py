from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from app.core.sanitize import SafeStr, SafeOptStr


class OrderItemCreate(BaseModel):
    menu_item_id: Optional[int] = None
    name: SafeStr
    price: float = Field(ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=100_000)
    notes: SafeOptStr = None


class OrderCreate(BaseModel):
    order_type: str = "dine_in"
    table_number: SafeOptStr = None
    customer_id: Optional[int] = None
    customer_name: SafeOptStr = None
    customer_phone: SafeOptStr = None
    items: List[OrderItemCreate] = Field(min_length=1)
    discount_percent: float = Field(default=0.0, ge=0, le=100)
    notes: SafeOptStr = None
    platform: Optional[str] = None
    platform_order_id: Optional[str] = None


class OrderStatusUpdate(BaseModel):
    status: str


class PaymentUpdate(BaseModel):
    payment_method: str
    discount_percent: float = Field(default=0.0, ge=0, le=100)
    points_to_redeem: int = Field(default=0, ge=0)


class CancelItemRequest(BaseModel):
    reason_code: str = Field(..., description="wrong_order|changed_mind|stock_out|quality_issue|other")
    reason_note: Optional[str] = None
    manager_pin: Optional[str] = None
    quantity: Optional[int] = Field(default=None, ge=1, description="Number of units to cancel. Defaults to full item quantity.")


class CancelItemResponse(BaseModel):
    success: bool
    requires_approval: bool = False
    cancellation_event_id: Optional[int] = None
    message: str
    station_notified: bool = False


class ManagerPinSetup(BaseModel):
    current_pin: Optional[str] = None
    new_pin: str = Field(..., min_length=4, max_length=20)


class ManagerPinVerify(BaseModel):
    pin: str


class OrderItemOut(BaseModel):
    id: int
    menu_item_id: Optional[int]
    name: str
    price: float
    quantity: int
    total: float
    notes: Optional[str]
    kot_number: int = 1
    cancelled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    order_number: str
    order_type: str
    table_number: Optional[str]
    customer_name: Optional[str]
    customer_phone: Optional[str]
    gst_rate: float = 5.0
    subtotal: float
    gst_amount: float
    discount_amount: float
    total_amount: float
    status: str
    payment_method: Optional[str]
    payment_status: str
    platform: Optional[str]
    notes: Optional[str]
    items: List[OrderItemOut]
    created_at: datetime
    kot_statuses: Optional[dict] = None

    class Config:
        from_attributes = True
