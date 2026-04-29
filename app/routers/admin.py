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

VALID_PLANS = ("trial", "starter", "pro", "enterprise")
VALID_ROLES  = ("owner", "manager", "cashier", "waiter", "kitchen")


# ── Restaurants List ──────────────────────────────────────────────────────────

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
    return [_restaurant_list_dict(r) for r in result.scalars().all()]


# ── Create Restaurant + Owner ─────────────────────────────────────────────────

@router.post("/restaurants", status_code=201)
async def create_restaurant(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    # Check owner email not taken
    existing = await db.execute(select(User).where(User.email == payload["owner_email"]))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Owner email already registered.")

    restaurant = Restaurant(
        name=payload["restaurant_name"],
        phone=payload.get("restaurant_phone"),
        address=payload.get("address"),
        city=payload.get("city"),
        plan=payload.get("plan", "trial"),
        trial_ends_at=_parse_date(payload.get("expiry_date")),
        is_active=True,
    )
    db.add(restaurant)
    await db.flush()

    owner = User(
        name=payload["owner_name"],
        email=payload["owner_email"],
        phone=payload.get("owner_phone"),
        hashed_password=hash_password(payload["owner_password"]),
        role="owner",
        restaurant_id=restaurant.id,
        is_active=True,
    )
    db.add(owner)
    await db.commit()
    await db.refresh(restaurant)
    return _restaurant_list_dict(restaurant)


# ── Restaurant Detail ─────────────────────────────────────────────────────────

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
    owner = next((u for u in users if u.role == "owner"), None)
    return {
        **_restaurant_detail_dict(r),
        "owner": {"name": owner.name, "email": owner.email, "phone": owner.phone} if owner else None,
        "accounts": [_user_dict(u) for u in users],
        "total_orders": orders_count.scalar(),
    }


# ── Update Restaurant Details ─────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/details")
async def update_restaurant_details(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    allowed = {"name", "phone", "address", "city", "gstin", "fssai", "gst_rate"}
    for key, value in payload.items():
        if key in allowed:
            setattr(r, key, value)
    await db.commit()
    await db.refresh(r)
    return _restaurant_detail_dict(r)


# ── Update License ────────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/license")
async def update_license(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    plan = payload.get("plan")
    if plan and plan not in VALID_PLANS:
        raise HTTPException(400, f"Invalid plan. Choose: {', '.join(VALID_PLANS)}")
    r = await _get_restaurant_or_404(restaurant_id, db)
    if plan:
        r.plan = plan
    if "expiry_date" in payload:
        r.trial_ends_at = _parse_date(payload["expiry_date"])
    await db.commit()
    await db.refresh(r)
    return {"plan": r.plan, "expiry_date": r.trial_ends_at, "created_at": r.created_at}


# ── Suspend / Activate ────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/suspend")
async def suspend_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = False
    await db.commit()
    return {"message": f"Restaurant '{r.name}' suspended.", "is_active": False}


@router.patch("/restaurants/{restaurant_id}/activate")
async def activate_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = True
    await db.commit()
    return {"message": f"Restaurant '{r.name}' activated.", "is_active": True}


# ── Delete Restaurant ─────────────────────────────────────────────────────────

@router.delete("/restaurants/{restaurant_id}")
async def delete_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    name = r.name
    await db.delete(r)
    await db.commit()
    return {"message": f"Restaurant '{name}' permanently deleted."}


# ── Accounts (per restaurant) ─────────────────────────────────────────────────

@router.post("/restaurants/{restaurant_id}/accounts", status_code=201)
async def create_account(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    await _get_restaurant_or_404(restaurant_id, db)
    role = payload.get("role", "cashier")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"Invalid role. Choose: {', '.join(VALID_ROLES)}")
    existing = await db.execute(select(User).where(User.email == payload["email"]))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered.")
    user = User(
        name=payload["name"],
        email=payload["email"],
        phone=payload.get("phone"),
        hashed_password=hash_password(payload["password"]),
        role=role,
        restaurant_id=restaurant_id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/restaurants/{restaurant_id}/accounts/{user_id}")
async def update_account(
    restaurant_id: int,
    user_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    user = await _get_user_or_404(user_id, db)
    if user.restaurant_id != restaurant_id:
        raise HTTPException(404, "Account not found in this restaurant.")
    if "name" in payload:
        user.name = payload["name"]
    if "phone" in payload:
        user.phone = payload["phone"]
    if "email" in payload:
        user.email = payload["email"]
    if "password" in payload and payload["password"]:
        user.hashed_password = hash_password(payload["password"])
    if "is_active" in payload:
        user.is_active = payload["is_active"]
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.delete("/restaurants/{restaurant_id}/accounts/{user_id}")
async def delete_account(
    restaurant_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    user = await _get_user_or_404(user_id, db)
    if user.restaurant_id != restaurant_id:
        raise HTTPException(404, "Account not found in this restaurant.")
    await db.delete(user)
    await db.commit()
    return {"message": f"Account '{user.email}' deleted."}


# ── Global Users ──────────────────────────────────────────────────────────────

@router.get("/users")
async def list_all_users(
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(User).where(User.role != "super_admin")
    if role:
        q = q.where(User.role == role)
    result = await db.execute(q.order_by(User.created_at.desc()))
    return [_user_dict(u) for u in result.scalars().all()]


@router.post("/users/super-admin", status_code=201)
async def create_super_admin(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    existing = await db.execute(select(User).where(User.email == payload["email"]))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered.")
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
    return _user_dict(user)


# ── Dashboard Stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    total_restaurants  = await db.execute(select(func.count()).select_from(Restaurant))
    active_restaurants = await db.execute(select(func.count()).select_from(Restaurant).where(Restaurant.is_active == True))
    total_users        = await db.execute(select(func.count()).select_from(User).where(User.role != "super_admin"))
    total_orders       = await db.execute(select(func.count()).select_from(Order))
    plan_counts_result = await db.execute(select(Restaurant.plan, func.count()).group_by(Restaurant.plan))
    return {
        "total_restaurants":  total_restaurants.scalar(),
        "active_restaurants": active_restaurants.scalar(),
        "total_users":        total_users.scalar(),
        "total_orders":       total_orders.scalar(),
        "restaurants_by_plan": {row[0]: row[1] for row in plan_counts_result.all()},
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_restaurant_or_404(restaurant_id: int, db: AsyncSession) -> Restaurant:
    result = await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    r = result.scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Restaurant not found.")
    return r


async def _get_user_or_404(user_id: int, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    u = result.scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found.")
    return u


def _parse_date(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _restaurant_list_dict(r: Restaurant) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "city": r.city,
        "phone": r.phone,
        "plan": r.plan,
        "is_active": r.is_active,
        "created_at": r.created_at,
    }


def _restaurant_detail_dict(r: Restaurant) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "phone": r.phone,
        "address": r.address,
        "city": r.city,
        "gstin": r.gstin,
        "fssai": r.fssai,
        "gst_rate": r.gst_rate,
        "plan": r.plan,
        "is_active": r.is_active,
        "trial_ends_at": r.trial_ends_at,
        "created_at": r.created_at,
    }


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "phone": u.phone,
        "role": u.role,
        "is_active": u.is_active,
        "restaurant_id": u.restaurant_id,
        "created_at": u.created_at,
    }
