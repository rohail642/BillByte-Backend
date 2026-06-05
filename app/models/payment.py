from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True, nullable=True)

    txn_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    pinelabs_ref_id: Mapped[str] = mapped_column(String(200), nullable=True)

    amount: Mapped[float] = mapped_column(Float)
    payment_mode: Mapped[str] = mapped_column(String(20))  # card | upi
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|success|failed|cancelled|timeout

    approval_code: Mapped[str] = mapped_column(String(100), nullable=True)
    card_number: Mapped[str] = mapped_column(String(30), nullable=True)
    card_type: Mapped[str] = mapped_column(String(50), nullable=True)
    response_message: Mapped[str] = mapped_column(String(500), nullable=True)
    raw_response = mapped_column(JSON, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
