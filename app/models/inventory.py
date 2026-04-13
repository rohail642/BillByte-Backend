from sqlalchemy import String, Integer, Float, ForeignKey, Text, DateTime, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.session import Base


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    email: Mapped[str] = mapped_column(String(200), nullable=True)
    address: Mapped[str] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(20))
    min_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    cost_per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(100), nullable=True)
    expiry_date: Mapped[Date] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    supplier: Mapped["Supplier"] = relationship("Supplier", lazy="select")
    usage_logs: Mapped[list["InventoryUsageLog"]] = relationship("InventoryUsageLog", back_populates="item")

    @property
    def is_low_stock(self) -> bool:
        return self.quantity < self.min_quantity

    @property
    def stock_value(self) -> float:
        return round(self.quantity * self.cost_per_unit, 2)

    @property
    def is_expiring_soon(self) -> bool:
        if not self.expiry_date:
            return False
        from datetime import date
        days_left = (self.expiry_date - date.today()).days
        return 0 <= days_left <= 7

    @property
    def is_expired(self) -> bool:
        if not self.expiry_date:
            return False
        from datetime import date
        return self.expiry_date < date.today()


class InventoryUsageLog(Base):
    """Auto-deducted when an order is placed, or manually logged."""
    __tablename__ = "inventory_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    quantity_used: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(200), default="manual")  # order|manual|waste|adjustment
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=True)
    notes: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="usage_logs")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    po_number: Mapped[str] = mapped_column(String(50), index=True)
    supplier_name: Mapped[str] = mapped_column(String(200))
    items_description: Mapped[str] = mapped_column(Text)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="ordered")
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    expected_delivery: Mapped[Date] = mapped_column(Date, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())