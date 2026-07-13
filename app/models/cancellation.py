from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base


class CancellationEvent(Base):
    __tablename__ = "cancellation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), nullable=False, index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"), nullable=False)
    approved_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_note: Mapped[str] = mapped_column(Text, nullable=True)
    kot_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    station_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="How many units were cancelled (item.quantity for full item)")
    cancelled_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        {"comment": "Audit log for item cancellations — insert-only, never updated or deleted"},
    )
