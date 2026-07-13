from sqlalchemy import String, Integer, BigInteger, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base


class TelegramLinkToken(Base):
    """One-time token that binds a Telegram chat to a BillByte user.

    Only the SHA-256 hash of the token is stored — the raw token exists solely
    in the deep link shown to the user, so a DB leak can't be replayed.
    """
    __tablename__ = "telegram_link_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramDeliveryLog(Base):
    """Insert-only audit trail of every daily-report send attempt."""
    __tablename__ = "telegram_delivery_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    report_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (restaurant-local day)
    status: Mapped[str] = mapped_column(String(20))       # sent|failed|blocked|invalid_chat
    error: Mapped[str] = mapped_column(Text, nullable=True)
    telegram_response: Mapped[str] = mapped_column(Text, nullable=True)  # truncated raw API reply
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
