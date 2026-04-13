from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
from datetime import date, timedelta

from app.db.session import get_db
from app.models.staff import Staff, AttendanceLog
from app.models.user import User
from app.schemas.staff import StaffCreate, StaffUpdate, StaffOut
from app.core.security import get_current_user

router = APIRouter(prefix="/staff", tags=["Staff"])


@router.get("/", response_model=List[StaffOut])
async def list_staff(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(Staff).where(Staff.restaurant_id == u.restaurant_id, Staff.is_active == True).order_by(Staff.name)
    )
    return r.scalars().all()


@router.post("/", response_model=StaffOut, status_code=201)
async def create_staff(body: StaffCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    member = Staff(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(member)
    await db.flush()
    # Log today's attendance as present by default
    db.add(AttendanceLog(
        staff_id=member.id, restaurant_id=u.restaurant_id,
        date=date.today(), status="present",
    ))
    return member


@router.patch("/{staff_id}", response_model=StaffOut)
async def update_staff(staff_id: int, body: StaffUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Staff).where(Staff.id == staff_id, Staff.restaurant_id == u.restaurant_id))
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Staff member not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(member, k, v)
    return member


@router.patch("/{staff_id}/attendance", response_model=StaffOut)
async def mark_attendance(
    staff_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Mark attendance for today — upserts today's log."""
    r = await db.execute(select(Staff).where(Staff.id == staff_id, Staff.restaurant_id == u.restaurant_id))
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Staff member not found")

    status = body.get("status", "present")
    notes  = body.get("notes", None)
    today  = date.today()

    # Update the staff's current status
    member.status = status

    # Upsert today's attendance log
    log_r = await db.execute(
        select(AttendanceLog).where(
            AttendanceLog.staff_id == staff_id,
            AttendanceLog.date == today,
        )
    )
    log = log_r.scalar_one_or_none()
    if log:
        log.status = status
        log.notes  = notes
    else:
        db.add(AttendanceLog(
            staff_id=staff_id, restaurant_id=u.restaurant_id,
            date=today, status=status, notes=notes,
        ))

    return member


@router.get("/{staff_id}/attendance")
async def get_attendance(
    staff_id: int,
    month: int | None = None,
    year: int | None = None,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Get attendance logs for a staff member — defaults to current month."""
    today = date.today()
    month = month or today.month
    year  = year  or today.year

    start = date(year, month, 1)
    # last day of month
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)

    r = await db.execute(
        select(AttendanceLog).where(
            AttendanceLog.staff_id == staff_id,
            AttendanceLog.restaurant_id == u.restaurant_id,
            AttendanceLog.date >= start,
            AttendanceLog.date <= end,
        ).order_by(AttendanceLog.date)
    )
    logs = r.scalars().all()

    # Count summary
    present = sum(1 for l in logs if l.status == "present")
    leave   = sum(1 for l in logs if l.status == "leave")
    off     = sum(1 for l in logs if l.status == "off")

    return {
        "staff_id": staff_id,
        "month": month,
        "year": year,
        "summary": { "present": present, "leave": leave, "off": off },
        "logs": [
            {
                "id": l.id,
                "date": str(l.date),
                "status": l.status,
                "notes": l.notes,
            }
            for l in logs
        ],
    }


@router.delete("/{staff_id}", status_code=204)
async def delete_staff(staff_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Staff).where(Staff.id == staff_id, Staff.restaurant_id == u.restaurant_id))
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Staff member not found")
    member.is_active = False