from sqlalchemy import String, Boolean, Integer, ForeignKey, DateTime, Float, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.session import Base


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    gstin: Mapped[str] = mapped_column(String(20), nullable=True)
    fssai: Mapped[str] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(5), default="INR")
    gst_rate: Mapped[float] = mapped_column(Float, default=5.0)
    plan: Mapped[str] = mapped_column(String(20), default="trial")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    trial_ends_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Integrations — per restaurant webhook secrets
    zomato_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    zomato_secret:  Mapped[str]  = mapped_column(String(500), nullable=True)
    zomato_restaurant_id: Mapped[str] = mapped_column(String(100), nullable=True)

    swiggy_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    swiggy_secret:  Mapped[str]  = mapped_column(String(500), nullable=True)
    swiggy_restaurant_id: Mapped[str] = mapped_column(String(100), nullable=True)

    razorpay_enabled:   Mapped[bool] = mapped_column(Boolean, default=False)
    razorpay_key_id:    Mapped[str]  = mapped_column(String(200), nullable=True)
    razorpay_key_secret:Mapped[str]  = mapped_column(String(200), nullable=True)

    pinelabs_enabled:        Mapped[bool] = mapped_column(Boolean, default=False)
    pinelabs_merchant_id:    Mapped[str]  = mapped_column(String(100), nullable=True)
    pinelabs_terminal_id:    Mapped[str]  = mapped_column(String(100), nullable=True)
    pinelabs_security_token: Mapped[str]  = mapped_column(String(500), nullable=True)

    table_count: Mapped[int] = mapped_column(Integer, default=10)
    table_sections = mapped_column(JSON, nullable=True)
    enabled_modules    = mapped_column(JSON, nullable=True)  # null = all enabled
    reminders_enabled  = mapped_column(Boolean, default=True, nullable=False, server_default='true')
    notes              = mapped_column(Text, nullable=True)
    round_off          = mapped_column(Boolean, default=False, nullable=False, server_default='false')
    loyalty_enabled    = mapped_column(Boolean, default=True,  nullable=False, server_default='true')

    users: Mapped[list["User"]] = relationship("User", back_populates="restaurant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner|manager|cashier|waiter
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    restaurant: Mapped["Restaurant"] = relationship("Restaurant", back_populates="users")