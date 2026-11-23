from sqlalchemy import String, Integer, ForeignKey, Text, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from typing import Optional
from app.db.session import Base


class TableOrderRequest(Base):
    """A guest's self-order from a table QR code, waiting for staff to accept.

    Kept separate from `orders` on purpose: an unconfirmed guest cart must not
    print a KOT, deduct stock, count in reports or mark the table active. Only
    when staff accept it is it turned into a normal Order/KOT (order_id set).
    """
    __tablename__ = "table_order_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    table_number: Mapped[str] = mapped_column(String(20))
    # [{menu_item_id, name, price, quantity, notes}] — prices copied from the
    # menu at request time (server-side), never taken from the guest.
    items = mapped_column(JSON, nullable=False)
    subtotal: Mapped[float] = mapped_column(default=0.0)
    guest_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|accepted|rejected
    reject_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # Random secret returned to the guest's browser only; required to read status.
    access_key: Mapped[str] = mapped_column(String(64))

    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True)
    kot_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    handled_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    handled_at = mapped_column(DateTime(timezone=True), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
