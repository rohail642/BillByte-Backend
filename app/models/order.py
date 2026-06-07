from sqlalchemy import String, Integer, Float, ForeignKey, Text, DateTime, JSON, func as sa_func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from typing import Optional
from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    order_number: Mapped[str] = mapped_column(String(50), index=True)
    order_type: Mapped[str] = mapped_column(String(20), default="dine_in")  # dine_in|takeaway|delivery
    table_number: Mapped[str] = mapped_column(String(20), nullable=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=True)
    customer_name: Mapped[str] = mapped_column(String(200), nullable=True)
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=True)

    gst_rate: Mapped[float] = mapped_column(Float, default=5.0)  # locked at order creation
    subtotal: Mapped[float] = mapped_column(Float, default=0.0)
    gst_amount: Mapped[float] = mapped_column(Float, default=0.0)
    discount_amount: Mapped[float] = mapped_column(Float, default=0.0)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending → kot_sent → preparing → ready → served → paid → cancelled

    # Per-KOT statuses e.g. {"1": "preparing", "2": "kot_sent"}
    kot_statuses: Mapped[Optional[dict]] = mapped_column(MutableDict.as_mutable(JSON), nullable=True)

    payment_method: Mapped[str] = mapped_column(String(30), nullable=True)  # cash|upi|card
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid")

    platform: Mapped[str] = mapped_column(String(30), nullable=True)  # zomato|swiggy|direct
    platform_order_id: Mapped[str] = mapped_column(String(100), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[float] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(String(500), nullable=True)
    kot_number: Mapped[int] = mapped_column(Integer, default=1)
    cancelled_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped["Order"] = relationship("Order", back_populates="items")
