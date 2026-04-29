from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class OrderItemCreate(BaseModel):
    menu_item_id: Optional[int] = None
    name: str
    price: float
    quantity: int = 1
    notes: Optional[str] = None


class OrderCreate(BaseModel):
    order_type: str = "dine_in"
    table_number: Optional[str] = None
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    items: List[OrderItemCreate]
    discount_percent: float = Field(default=0.0, ge=0, le=100)
    notes: Optional[str] = None
    platform: Optional[str] = None
    platform_order_id: Optional[str] = None


class OrderStatusUpdate(BaseModel):
    status: str


class PaymentUpdate(BaseModel):
    payment_method: str
    discount_percent: float = Field(default=0.0, ge=0, le=100)
    points_to_redeem: int = 0


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
