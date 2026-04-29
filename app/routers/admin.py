from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime

from app.db.session import get_db
from app.models.user import User, Restaurant
from app.models.order import Order
from app.core.security import require_super_admin, hash_password

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Restaurants ───────────────────────────────────────────────────────────────

@router.get("/restaurants")
async def list_restaurants(
    search: Optional[str] = None,
    plan: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(Restaurant)
    if search:
        q = q.where(Restaurant.name.ilike(f"%{search}%"))
    if plan:
        q = q.where(Restaurant.plan == plan)
    if is_active is not None:
        q = q.where(Restaurant.is_active == is_active)
    result = await db.execute(q.order_by(Restaurant.created_at.desc()))
    restaurants = result.scalars().all()
    return [_restaurant_dict(r) for r in restaurants]


@router.get("/restaurants/{restaurant_id}")
async def get_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    users_result = await db.execute(
        select(User).where(User.restaurant_id == restaurant_id)
    )
    users = users_result.scalars().all()
    orders_count = await db.execute(
        select(func.count()).where(Order.restaurant_id == restaurant_id)
    )
    return {
        **_restaurant_dict(r),
        "users": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "is_active": u.is_active} for u in users],
        "total_orders": orders_count.scalar(),
    }


@router.patch("/restaurants/{restaurant_id}")
async def update_restaurant(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    allowed = {"name", "plan", "is_active", "trial_ends_at", "gst_rate", "city", "phone"}
    for key, value in payload.items():
        if key in allowed:
            setattr(r, key, value)
    await db.commit()
    await db.refresh(r)
    return _restaurant_dict(r)


@router.patch("/restaurants/{restaurant_id}/suspend")
async def suspend_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = False
    await db.commit()
    return {"message": f"Restaurant '{r.name}' suspended."}


@router.patch("/restaurants/{restaurant_id}/activate")
async def activate_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = True
    await db.commit()
    return {"message": f"Restaurant '{r.name}' activated."}


@router.patch("/restaurants/{restaurant_id}/plan")
async def change_plan(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    plan = payload.get("plan")
    if plan not in ("trial", "starter", "pro", "enterprise"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose: trial, starter, pro, enterprise")
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.plan = plan
    await db.commit()
    return {"message": f"Plan updated to '{plan}' for '{r.name}'."}


@router.delete("/restaurants/{restaurant_id}")
async def delete_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    await db.delete(r)
    await db.commit()
    return {"message": f"Restaurant '{r.name}' permanently deleted."}


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_all_users(
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(User)
    if role:
        q = q.where(User.role == role)
    result = await db.execute(q.order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "restaurant_id": u.restaurant_id, "is_active": u.is_active} for u in users]


@router.post("/users/super-admin")
async def create_super_admin(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    existing = await db.execute(select(User).where(User.email == payload["email"]))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = User(
        name=payload["name"],
        email=payload["email"],
        hashed_password=hash_password(payload["password"]),
        role="super_admin",
        restaurant_id=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


@router.patch("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    user = await _get_user_or_404(user_id, db)
    user.is_active = False
    await db.commit()
    return {"message": f"User '{user.email}' deactivated."}


# ── Dashboard Stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    total_restaurants = await db.execute(select(func.count()).select_from(Restaurant))
    active_restaurants = await db.execute(select(func.count()).select_from(Restaurant).where(Restaurant.is_active == True))
    total_users = await db.execute(select(func.count()).select_from(User).where(User.role != "super_admin"))
    total_orders = await db.execute(select(func.count()).select_from(Order))

    plan_counts_result = await db.execute(
        select(Restaurant.plan, func.count()).group_by(Restaurant.plan)
    )
    plan_counts = {row[0]: row[1] for row in plan_counts_result.all()}

    return {
        "total_restaurants": total_restaurants.scalar(),
        "active_restaurants": active_restaurants.scalar(),
        "total_users": total_users.scalar(),
        "total_orders": total_orders.scalar(),
        "restaurants_by_plan": plan_counts,
    }


# ── Admin Auth (login via existing /api/auth/login — role checked there) ──────

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_restaurant_or_404(restaurant_id: int, db: AsyncSession) -> Restaurant:
    result = await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    return r


async def _get_user_or_404(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    u = result.scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="User not found.")
    return u


def _restaurant_dict(r: Restaurant) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "city": r.city,
        "phone": r.phone,
        "plan": r.plan,
        "is_active": r.is_active,
        "gst_rate": r.gst_rate,
        "trial_ends_at": r.trial_ends_at,
        "created_at": r.created_at,
        "zomato_enabled": r.zomato_enabled,
        "swiggy_enabled": r.swiggy_enabled,
    }
