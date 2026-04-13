from sqlalchemy import String, Integer, Float, ForeignKey, DateTime, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.session import Base


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(String(20), nullable=True)
    email: Mapped[str] = mapped_column(String(200), nullable=True)
    salary: Mapped[float] = mapped_column(Float, default=0.0)
    join_date: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="present")  # today's status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    attendance: Mapped[list["AttendanceLog"]] = relationship("AttendanceLog", back_populates="staff_member")


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    date: Mapped[Date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20))  # present|leave|off
    notes: Mapped[str] = mapped_column(String(300), nullable=True)
    marked_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    staff_member: Mapped["Staff"] = relationship("Staff", back_populates="attendance")