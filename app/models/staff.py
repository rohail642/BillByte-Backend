from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Boolean, Date, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.session import Base


class Staff(Base):
    """An employee record (HR), separate from login accounts (users)."""
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))          # job title, e.g. "Head Chef"
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    email: Mapped[str] = mapped_column(String(200), nullable=True)
    salary: Mapped[float] = mapped_column(Float, default=0.0)  # monthly
    join_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    # Legacy "last marked" status. Today's status is now derived from
    # attendance_logs; kept only so old clients don't break.
    status: Mapped[str] = mapped_column(String(20), default="present")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_off: Mapped[int] = mapped_column(Integer, nullable=True)  # 0=Mon … 6=Sun, None = no fixed day
    notes: Mapped[str] = mapped_column(String(500), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attendance: Mapped[list["AttendanceLog"]] = relationship("AttendanceLog", back_populates="staff_member")


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    date: Mapped[Date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20))  # present | half_day | leave | off
    notes: Mapped[str] = mapped_column(String(300), nullable=True)
    marked_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    staff_member: Mapped["Staff"] = relationship("Staff", back_populates="attendance")


class StaffAdvance(Base):
    """Money paid to an employee during the month, deducted from that month's salary."""
    __tablename__ = "staff_advances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    date: Mapped[Date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(200), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalaryPayout(Base):
    """A month's salary marked as paid, with the calculation frozen at that moment."""
    __tablename__ = "salary_payouts"
    __table_args__ = (UniqueConstraint("staff_id", "year", "month", name="uq_salary_payout_staff_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    base_salary: Mapped[float] = mapped_column(Float)
    worked_days: Mapped[float] = mapped_column(Float)
    unpaid_days: Mapped[float] = mapped_column(Float)
    deduction: Mapped[float] = mapped_column(Float)
    advances: Mapped[float] = mapped_column(Float)
    net_payable: Mapped[float] = mapped_column(Float)
    paid_amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20))   # cash | upi | bank
    paid_on: Mapped[Date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
