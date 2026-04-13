from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.db.session import get_db
from app.models.staff import Staff
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


@router.delete("/{staff_id}", status_code=204)
async def delete_staff(staff_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Staff).where(Staff.id == staff_id, Staff.restaurant_id == u.restaurant_id))
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Staff member not found")
    member.is_active = False  # soft delete
