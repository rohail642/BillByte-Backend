from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case, exists, and_, delete
import asyncio
from typing import Optional
from datetime import datetime, timedelta, timezone
import csv
import io

from app.db.session import get_db
from app.models.user import User, Restaurant
from app.models.order import Order, OrderItem
from app.models.menu import MenuItem
from app.models.payment import PaymentTransaction
from app.models.inventory import InventoryUsageLog
from app.models.customer import LoyaltyTransaction
from app.models.admin_models import ActivityLog, Announcement
from app.core.security import require_super_admin, hash_password, create_access_token

router = APIRouter(prefix="/admin", tags=["Admin"])

VALID_PLANS = ("trial", "starter", "pro", "enterprise")
VALID_ROLES  = ("owner", "manager", "cashier", "waiter", "kitchen")
DEFAULT_MODULES = {"inventory": True, "reports": True, "crm": True, "staff": True}


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
    """Parse a date/datetime value and always return a timezone-aware UTC datetime.
    Date-only strings (YYYY-MM-DD) are set to 23:59:59 UTC so the full calendar
    day is covered regardless of the server or client timezone."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).strip()
        if len(s) == 10:  # date-only: YYYY-MM-DD
            y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
            return datetime(y, m, d, 23, 59, 59, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _restaurant_list_dict(r, total_orders=0, total_revenue=0.0, last_order_date=None,
                          owner_email=None, owner_name=None) -> dict:
    today = datetime.now(timezone.utc).date()
    days_left = None
    expiry_status = None
    if r.trial_ends_at:
        expiry_date = (r.trial_ends_at if r.trial_ends_at.tzinfo else r.trial_ends_at.replace(tzinfo=timezone.utc)).date()
        days_left = (expiry_date - today).days
        if days_left < 0:
            expiry_status = "expired"
        elif days_left <= 7:
            expiry_status = "critical"
        elif days_left <= 30:
            expiry_status = "warning"
        else:
            expiry_status = "ok"
    return {
        "id": r.id,
        "name": r.name,
        "city": r.city,
        "phone": r.phone,
        "plan": r.plan,
        "is_active": r.is_active,
        "created_at": r.created_at,
        "trial_ends_at": r.trial_ends_at,
        "days_left": days_left,
        "expiry_status": expiry_status,
        "owner_email": owner_email,
        "owner_name": owner_name,
        "total_orders": int(total_orders or 0),
        "total_revenue": float(total_revenue or 0.0),
        "last_order_date": last_order_date,
        "reminders_enabled": r.reminders_enabled if r.reminders_enabled is not None else True,
    }


def _restaurant_detail_dict(r: Restaurant) -> dict:
    today = datetime.now(timezone.utc).date()
    days_left = None
    expiry_status = None
    if r.trial_ends_at:
        expiry_date = (r.trial_ends_at if r.trial_ends_at.tzinfo else r.trial_ends_at.replace(tzinfo=timezone.utc)).date()
        days_left = (expiry_date - today).days
        if days_left < 0:
            expiry_status = "expired"
        elif days_left <= 7:
            expiry_status = "critical"
        elif days_left <= 30:
            expiry_status = "warning"
        else:
            expiry_status = "ok"
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
        "days_left": days_left,
        "expiry_status": expiry_status,
        "created_at": r.created_at,
        "enabled_modules":   r.enabled_modules or DEFAULT_MODULES,
        "reminders_enabled": r.reminders_enabled if r.reminders_enabled is not None else True,
        "notes":             r.notes or "",
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


async def _log(db: AsyncSession, admin: User, action: str, target_type: str,
               target_id: Optional[int] = None, target_name: Optional[str] = None,
               details: Optional[dict] = None):
    entry = ActivityLog(
        admin_id=admin.id,
        admin_name=admin.name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        details=details,
    )
    db.add(entry)


def _stats_query():
    """Subqueries for restaurant order stats."""
    order_stats = (
        select(
            Order.restaurant_id,
            func.count(Order.id).label("total_orders"),
            func.coalesce(
                func.sum(case((Order.payment_status == "paid", Order.total_amount), else_=0)), 0
            ).label("total_revenue"),
            func.max(Order.created_at).label("last_order_date"),
        )
        .group_by(Order.restaurant_id)
        .subquery()
    )
    owner_sq = (
        select(
            User.restaurant_id,
            func.min(User.email).label("owner_email"),
            func.min(User.name).label("owner_name"),
        )
        .where(User.role == "owner")
        .group_by(User.restaurant_id)
        .subquery()
    )
    return order_stats, owner_sq


# ── Restaurants List ──────────────────────────────────────────────────────────

@router.get("/restaurants")
async def list_restaurants(
    search: Optional[str] = None,
    plan: Optional[str] = None,
    is_active: Optional[bool] = None,
    expiring_in_days: Optional[int] = None,
    dead: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    # Auto-deactivate any expired restaurants before returning the list
    now = datetime.now(timezone.utc)
    expired_res = await db.execute(
        select(Restaurant).where(
            Restaurant.trial_ends_at < now,
            Restaurant.is_active == True,
        )
    )
    expired_list = expired_res.scalars().all()
    for exp_r in expired_list:
        exp_r.is_active = False
        db.add(ActivityLog(
            admin_name="system",
            action="auto_deactivate",
            target_type="restaurant",
            target_id=exp_r.id,
            target_name=exp_r.name,
            details={"reason": "trial_expired", "expired_at": str(exp_r.trial_ends_at)},
        ))
    if expired_list:
        await db.commit()

    order_stats, owner_sq = _stats_query()
    q = (
        select(
            Restaurant,
            func.coalesce(order_stats.c.total_orders, 0).label("total_orders"),
            func.coalesce(order_stats.c.total_revenue, 0).label("total_revenue"),
            order_stats.c.last_order_date,
            owner_sq.c.owner_email,
            owner_sq.c.owner_name,
        )
        .outerjoin(order_stats, Restaurant.id == order_stats.c.restaurant_id)
        .outerjoin(owner_sq, Restaurant.id == owner_sq.c.restaurant_id)
    )
    if search:
        owner_match = exists(
            select(User.id).where(
                User.restaurant_id == Restaurant.id,
                User.role == "owner",
                User.email.ilike(f"%{search}%") | User.name.ilike(f"%{search}%")
            )
        )
        q = q.where(
            Restaurant.name.ilike(f"%{search}%") |
            Restaurant.city.ilike(f"%{search}%") |
            owner_match
        )
    if plan:
        q = q.where(Restaurant.plan == plan)
    if is_active is not None:
        q = q.where(Restaurant.is_active == is_active)
    if expiring_in_days is not None:
        cutoff = datetime.now(timezone.utc) + timedelta(days=expiring_in_days)
        q = q.where(Restaurant.trial_ends_at <= cutoff)
    if dead:
        dead_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        q = q.where(
            Restaurant.is_active == True,
            Restaurant.created_at < datetime.now(timezone.utc) - timedelta(days=7),
            (order_stats.c.last_order_date < dead_cutoff) | (order_stats.c.last_order_date == None),
        )
    result = await db.execute(q.order_by(Restaurant.created_at.desc()))
    rows = result.all()
    return [
        _restaurant_list_dict(
            r, total_orders=tot_ord, total_revenue=tot_rev,
            last_order_date=last_ord, owner_email=oe, owner_name=on
        )
        for r, tot_ord, tot_rev, last_ord, oe, on in rows
    ]


# ── Create Restaurant + Owner ─────────────────────────────────────────────────

@router.post("/restaurants", status_code=201)
async def create_restaurant(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
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
    await _log(db, admin, "create_restaurant", "restaurant", restaurant.id, restaurant.name,
               {"owner_email": payload["owner_email"]})
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
    users_result = await db.execute(select(User).where(User.restaurant_id == restaurant_id))
    users = users_result.scalars().all()
    orders_count = await db.execute(select(func.count()).where(Order.restaurant_id == restaurant_id))
    revenue_result = await db.execute(
        select(func.coalesce(func.sum(Order.total_amount), 0))
        .where(Order.restaurant_id == restaurant_id, Order.payment_status == "paid")
    )
    last_order_result = await db.execute(
        select(func.max(Order.created_at)).where(Order.restaurant_id == restaurant_id)
    )
    owner = next((u for u in users if u.role == "owner"), None)

    # Onboarding completion
    menu_count = await db.execute(
        select(func.count()).select_from(MenuItem)
        .where(MenuItem.restaurant_id == restaurant_id)
    )
    onboarding = {
        "has_menu": (menu_count.scalar() or 0) > 0,
        "has_gstin": bool(r.gstin),
        "has_address": bool(r.address),
        "has_tables": (r.table_count or 0) > 0,
        "has_integrations": bool(r.zomato_enabled or r.swiggy_enabled or r.razorpay_enabled),
    }
    onboarding["score"] = sum(1 for v in onboarding.values() if v)

    return {
        **_restaurant_detail_dict(r),
        "owner": {"id": owner.id, "name": owner.name, "email": owner.email, "phone": owner.phone} if owner else None,
        "accounts": [_user_dict(u) for u in users],
        "total_orders": orders_count.scalar(),
        "total_revenue": float(revenue_result.scalar() or 0),
        "last_order_date": last_order_result.scalar(),
        "onboarding": onboarding,
    }


# ── Update Restaurant Details ─────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/details")
async def update_restaurant_details(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    allowed = {"name", "phone", "address", "city", "gstin", "fssai", "gst_rate"}
    changes = {}
    for key, value in payload.items():
        if key in allowed:
            old_val = getattr(r, key, None)
            if str(old_val) != str(value):
                changes[key] = {"from": old_val, "to": value}
            setattr(r, key, value)
    await _log(db, admin, "update_details", "restaurant", r.id, r.name, {"changes": changes} if changes else {"note": "no changes"})
    await db.commit()
    await db.refresh(r)
    return _restaurant_detail_dict(r)


# ── Update License ────────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/license")
async def update_license(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    plan = payload.get("plan")
    if plan and plan not in VALID_PLANS:
        raise HTTPException(400, f"Invalid plan. Choose: {', '.join(VALID_PLANS)}")
    r = await _get_restaurant_or_404(restaurant_id, db)
    changes = {}
    if plan and plan != r.plan:
        changes["plan"] = {"from": r.plan, "to": plan}
        r.plan = plan
    if "expiry_date" in payload:
        old_expiry = r.trial_ends_at.strftime("%Y-%m-%d") if r.trial_ends_at else None
        changes["expiry_date"] = {"from": old_expiry, "to": payload["expiry_date"]}
        r.trial_ends_at = _parse_date(payload["expiry_date"])
    await _log(db, admin, "update_license", "restaurant", r.id, r.name,
               {"plan": r.plan, "changes": changes} if changes else {"plan": r.plan, "note": "no changes"})
    await db.commit()
    await db.refresh(r)
    return {"plan": r.plan, "expiry_date": r.trial_ends_at, "created_at": r.created_at,
            "days_left": _restaurant_detail_dict(r)["days_left"],
            "expiry_status": _restaurant_detail_dict(r)["expiry_status"]}


# ── License History ──────────────────────────────────────────────────────────

@router.get("/restaurants/{restaurant_id}/license-history")
async def license_history(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)

    LICENSE_ACTIONS = {
        "update_license", "bulk_change_plan", "bulk_extend_trial",
        "bulk_activate", "bulk_suspend", "restaurant_auto_deactivated",
        "create_restaurant",
    }
    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.target_id == restaurant_id, ActivityLog.action.in_(LICENSE_ACTIONS))
        .order_by(ActivityLog.created_at.asc())
    )
    logs = result.scalars().all()

    # Always prepend the registration entry
    history = [{
        "id": None,
        "action": "create_restaurant",
        "date": r.created_at.isoformat(),
        "by": "System",
        "details": {"plan": "trial", "note": "Restaurant registered"},
    }]

    for log in logs:
        if log.action == "create_restaurant":
            continue  # already added above
        history.append({
            "id": log.id,
            "action": log.action,
            "date": log.created_at.isoformat(),
            "by": log.admin_name or "System",
            "details": log.details or {},
        })

    return history


# ── Update Modules ───────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/modules")
async def update_modules(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    current = r.enabled_modules or DEFAULT_MODULES
    new_modules = {**DEFAULT_MODULES, **{k: bool(v) for k, v in payload.items() if k in DEFAULT_MODULES}}
    changes = {k: {"from": current.get(k, True), "to": new_modules[k]} for k in new_modules if current.get(k, True) != new_modules[k]}
    r.enabled_modules = new_modules
    await _log(db, admin, "update_modules", "restaurant", r.id, r.name,
               {"changes": changes} if changes else {"note": "no changes"})
    await db.commit()
    return {"enabled_modules": r.enabled_modules}


# ── Toggle Reminders ─────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/reminders-toggle")
async def toggle_reminders(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.reminders_enabled = not (r.reminders_enabled if r.reminders_enabled is not None else True)
    await _log(db, admin, "update_details", "restaurant", r.id, r.name,
               {"changes": {"reminders_enabled": {"from": not r.reminders_enabled, "to": r.reminders_enabled}}})
    await db.commit()
    return {"reminders_enabled": r.reminders_enabled}


# ── Internal Notes ───────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/notes")
async def update_notes(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.notes = payload.get("notes", "")
    await db.commit()
    return {"notes": r.notes}


# ── Suspend / Activate ────────────────────────────────────────────────────────

@router.patch("/restaurants/{restaurant_id}/suspend")
async def suspend_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = False
    await _log(db, admin, "suspend", "restaurant", r.id, r.name)
    await db.commit()
    return {"message": f"Restaurant '{r.name}' suspended.", "is_active": False}


@router.patch("/restaurants/{restaurant_id}/activate")
async def activate_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    r.is_active = True
    await _log(db, admin, "activate", "restaurant", r.id, r.name)
    await db.commit()
    return {"message": f"Restaurant '{r.name}' activated.", "is_active": True}


# ── Delete Restaurant ─────────────────────────────────────────────────────────

@router.delete("/restaurants/{restaurant_id}")
async def delete_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    name = r.name
    await _log(db, admin, "delete_restaurant", "restaurant", r.id, name)
    await db.delete(r)
    await db.commit()
    return {"message": f"Restaurant '{name}' permanently deleted."}


# ── Delete ALL orders for a restaurant (test-data reset) ──────────────────────

@router.delete("/restaurants/{restaurant_id}/orders")
async def delete_all_orders(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Permanently delete every order/bill for a restaurant. Used to clear test
    data before a restaurant goes live. Order line-items cascade automatically;
    order-linked payment / inventory-usage / loyalty rows are removed first so
    the FK delete succeeds (manual, non-order rows are preserved). The menu,
    inventory stock, customers and staff are left untouched."""
    r = await _get_restaurant_or_404(restaurant_id, db)

    count = (await db.execute(
        select(func.count()).select_from(Order).where(Order.restaurant_id == restaurant_id)
    )).scalar_one()

    order_ids = select(Order.id).where(Order.restaurant_id == restaurant_id).scalar_subquery()
    # Detach rows that FK to these orders (no ON DELETE CASCADE on those refs).
    await db.execute(delete(LoyaltyTransaction).where(LoyaltyTransaction.order_id.in_(order_ids)))
    await db.execute(delete(InventoryUsageLog).where(InventoryUsageLog.order_id.in_(order_ids)))
    await db.execute(delete(PaymentTransaction).where(PaymentTransaction.order_id.in_(order_ids)))
    # order_items have ON DELETE CASCADE, so they go with the orders.
    await db.execute(delete(Order).where(Order.restaurant_id == restaurant_id))

    await _log(db, admin, "delete_all_orders", "restaurant", r.id, r.name, {"deleted_orders": int(count)})
    await db.commit()
    return {"message": f"Deleted all {int(count)} order(s) for '{r.name}'.", "deleted_orders": int(count)}


# ── Manually add a (back-dated) bill for a restaurant ─────────────────────────

@router.post("/restaurants/{restaurant_id}/orders", status_code=201)
async def admin_create_order(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Manually add a paid, itemized bill for a restaurant on a chosen day.
    Totals are computed from the restaurant's GST rate and the order is dated to
    that day so it appears in the restaurant's reports for that date. This does
    NOT deduct inventory or award loyalty points (it is a historical record)."""
    r = await _get_restaurant_or_404(restaurant_id, db)

    # Items
    items = []
    subtotal = 0.0
    for it in (payload.get("items") or []):
        name = str(it.get("name", "")).strip()
        try:
            qty = int(it.get("quantity", 1))
            price = float(it.get("price"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Each item needs a name, a numeric price and quantity.")
        if not name or qty < 1 or price < 0:
            raise HTTPException(400, "Each item needs a name, quantity >= 1 and price >= 0.")
        line_total = round(price * qty, 2)
        subtotal += line_total
        items.append(OrderItem(name=name, price=price, quantity=qty, total=line_total, kot_number=1))
    if not items:
        raise HTTPException(400, "Add at least one item.")

    # Payment method
    pay = payload.get("payment_method", "cash")
    if pay not in ("cash", "upi", "card"):
        raise HTTPException(400, "payment_method must be cash, upi or card.")

    # Date → noon UTC of the chosen calendar day (avoids timezone edge effects)
    day = _parse_date(payload.get("date"))
    if not day:
        raise HTTPException(400, "A valid date (YYYY-MM-DD) is required.")
    created = datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc)

    gst_rate = r.gst_rate if r.gst_rate is not None else 5.0
    subtotal = round(subtotal, 2)
    gst = round(subtotal * gst_rate / 100, 2)
    total = round(subtotal + gst, 2)

    order = Order(
        restaurant_id=restaurant_id,
        order_number="",  # assigned after flush, once id is known
        order_type=payload.get("order_type") or "dine_in",
        customer_name=(payload.get("customer_name") or None),
        gst_rate=gst_rate,
        subtotal=subtotal, gst_amount=gst, discount_amount=0.0, discount_percent=0.0,
        total_amount=total,
        status="paid", payment_status="paid", payment_method=pay,
        notes=(payload.get("notes") or None),
        created_by=admin.id,
        kot_statuses={"1": "served"},
        items=items,
        created_at=created, updated_at=created,
    )
    db.add(order)
    await db.flush()
    order.order_number = f"BB-{created.strftime('%Y%m%d')}-{order.id}"
    await _log(db, admin, "create_order", "restaurant", r.id, r.name,
               {"order_id": order.id, "date": created.strftime("%Y-%m-%d"), "total": total})
    await db.commit()
    return {
        "id": order.id,
        "order_number": order.order_number,
        "date": created.strftime("%Y-%m-%d"),
        "subtotal": subtotal,
        "gst_amount": gst,
        "total_amount": total,
        "payment_method": pay,
        "item_count": len(items),
    }


# ── Impersonate (generate login token for owner) ──────────────────────────────

@router.post("/restaurants/{restaurant_id}/impersonate")
async def impersonate_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    r = await _get_restaurant_or_404(restaurant_id, db)
    token = create_access_token({
        "sub": str(admin.id),
        "restaurant_id": str(restaurant_id),
        "role_override": "owner",
        "impersonate": True,
    })
    await _log(db, admin, "impersonate", "restaurant", r.id, r.name, {"admin": admin.email})
    await db.commit()
    return {
        "token": token,
        "admin_name": admin.name,
        "admin_email": admin.email,
        "restaurant_name": r.name,
        "restaurant_id": r.id,
    }


# ── Bulk Actions ──────────────────────────────────────────────────────────────

@router.post("/restaurants/bulk-action")
async def bulk_action(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    action = payload.get("action")
    ids = payload.get("ids", [])
    if not ids:
        raise HTTPException(400, "No restaurant IDs provided.")
    if action not in ("suspend", "activate", "change_plan", "extend_trial"):
        raise HTTPException(400, "Invalid action. Use: suspend, activate, change_plan, extend_trial")

    result = await db.execute(select(Restaurant).where(Restaurant.id.in_(ids)))
    restaurants = result.scalars().all()

    for r in restaurants:
        if action == "suspend":
            r.is_active = False
        elif action == "activate":
            r.is_active = True
        elif action == "change_plan":
            new_plan = payload.get("plan")
            if new_plan not in VALID_PLANS:
                raise HTTPException(400, f"Invalid plan: {new_plan}")
            r.plan = new_plan
        elif action == "extend_trial":
            days = int(payload.get("days", 14))
            base = r.trial_ends_at or datetime.now(timezone.utc)
            base_aware = base if base.tzinfo else base.replace(tzinfo=timezone.utc)
            r.trial_ends_at = base_aware + timedelta(days=days)
        log_details = {**payload, "plan": r.plan}
        if action == "extend_trial" and r.trial_ends_at:
            log_details["new_expiry"] = r.trial_ends_at.strftime("%Y-%m-%d")
        await _log(db, admin, f"bulk_{action}", "restaurant", r.id, r.name, log_details)

    await db.commit()
    return {"message": f"Action '{action}' applied to {len(restaurants)} restaurants."}


# ── CSV Export ────────────────────────────────────────────────────────────────

@router.get("/restaurants/export")
async def export_restaurants(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    order_stats, owner_sq = _stats_query()
    q = (
        select(
            Restaurant,
            func.coalesce(order_stats.c.total_orders, 0).label("total_orders"),
            func.coalesce(order_stats.c.total_revenue, 0).label("total_revenue"),
            order_stats.c.last_order_date,
            owner_sq.c.owner_email,
            owner_sq.c.owner_name,
        )
        .outerjoin(order_stats, Restaurant.id == order_stats.c.restaurant_id)
        .outerjoin(owner_sq, Restaurant.id == owner_sq.c.restaurant_id)
        .order_by(Restaurant.created_at.desc())
    )
    result = await db.execute(q)
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "City", "Phone", "Plan", "Active", "Owner Name", "Owner Email",
                     "Total Orders", "Total Revenue (₹)", "Last Order", "Trial Ends", "Created"])
    for r, tot_ord, tot_rev, last_ord, oe, on in rows:
        writer.writerow([
            r.id, r.name, r.city or "", r.phone or "",
            r.plan, "Yes" if r.is_active else "No",
            on or "", oe or "",
            int(tot_ord or 0), f"{float(tot_rev or 0):.2f}",
            last_ord.strftime("%Y-%m-%d") if last_ord else "",
            r.trial_ends_at.strftime("%Y-%m-%d") if r.trial_ends_at else "",
            r.created_at.strftime("%Y-%m-%d") if r.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=restaurants.csv"},
    )


# ── Accounts (per restaurant) ─────────────────────────────────────────────────

@router.post("/restaurants/{restaurant_id}/accounts", status_code=201)
async def create_account(
    restaurant_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
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
    await db.flush()
    await _log(db, admin, "create_account", "user", user.id, user.name,
               {"role": role, "restaurant_id": restaurant_id})
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/restaurants/{restaurant_id}/accounts/{user_id}")
async def update_account(
    restaurant_id: int,
    user_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    user = await _get_user_or_404(user_id, db)
    if user.restaurant_id != restaurant_id:
        raise HTTPException(404, "Account not found in this restaurant.")
    changes = {}
    if "name" in payload and payload["name"] != user.name:
        changes["name"] = {"from": user.name, "to": payload["name"]}
        user.name = payload["name"]
    if "phone" in payload and payload["phone"] != user.phone:
        changes["phone"] = {"from": user.phone, "to": payload["phone"]}
        user.phone = payload["phone"]
    if "email" in payload and payload["email"] != user.email:
        changes["email"] = {"from": user.email, "to": payload["email"]}
        user.email = payload["email"]
    if "password" in payload and payload["password"]:
        changes["password"] = "changed"
        user.hashed_password = hash_password(payload["password"])
    if "role" in payload and payload["role"] != user.role:
        changes["role"] = {"from": user.role, "to": payload["role"]}
        user.role = payload["role"]
    if "is_active" in payload and payload["is_active"] != user.is_active:
        changes["is_active"] = {"from": user.is_active, "to": payload["is_active"]}
        user.is_active = payload["is_active"]
    await _log(db, admin, "update_account", "user", user.id, user.name,
               {"changes": changes} if changes else {"note": "no changes"})
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.delete("/restaurants/{restaurant_id}/accounts/{user_id}")
async def delete_account(
    restaurant_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    user = await _get_user_or_404(user_id, db)
    if user.restaurant_id != restaurant_id:
        raise HTTPException(404, "Account not found in this restaurant.")
    await _log(db, admin, "delete_account", "user", user.id, user.name,
               {"restaurant_id": restaurant_id})
    await db.delete(user)
    await db.commit()
    return {"message": f"Account '{user.email}' deleted."}


# ── Global Users ──────────────────────────────────────────────────────────────

@router.get("/users")
async def list_all_users(
    role: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(User).where(User.role != "super_admin")
    if role:
        q = q.where(User.role == role)
    if search:
        q = q.where(
            User.name.ilike(f"%{search}%") |
            User.email.ilike(f"%{search}%") |
            User.phone.ilike(f"%{search}%")
        )
    result = await db.execute(q.order_by(User.created_at.desc()))
    return [_user_dict(u) for u in result.scalars().all()]


# ── Super Admin Management ────────────────────────────────────────────────────

@router.get("/super-admins")
async def list_super_admins(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    result = await db.execute(
        select(User).where(User.role == "super_admin").order_by(User.created_at.desc())
    )
    return [_user_dict(u) for u in result.scalars().all()]


@router.post("/users/super-admin", status_code=201)
async def create_super_admin(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
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
    await db.flush()
    await _log(db, admin, "create_super_admin", "user", user.id, user.name, {"email": payload["email"]})
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.patch("/super-admins/{user_id}")
async def update_super_admin(
    user_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot modify your own account here.")
    user = await _get_user_or_404(user_id, db)
    if user.role != "super_admin":
        raise HTTPException(404, "Super admin not found.")
    if "name" in payload:
        user.name = payload["name"]
    if "is_active" in payload:
        user.is_active = payload["is_active"]
    if "password" in payload and payload["password"]:
        user.hashed_password = hash_password(payload["password"])
    await _log(db, admin, "update_super_admin", "user", user.id, user.name)
    await db.commit()
    await db.refresh(user)
    return _user_dict(user)


@router.delete("/super-admins/{user_id}", status_code=204)
async def delete_super_admin(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete your own account.")
    user = await _get_user_or_404(user_id, db)
    if user.role != "super_admin":
        raise HTTPException(404, "Super admin not found.")
    await _log(db, admin, "delete_super_admin", "user", user.id, user.name)
    await db.delete(user)
    await db.commit()


# ── Maintenance: expire trials ───────────────────────────────────────────────

@router.post("/maintenance/expire-trials")
async def run_expiry_check(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Deactivates all restaurants whose trial/license has expired."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Restaurant).where(
            Restaurant.trial_ends_at < now,
            Restaurant.is_active == True,
        )
    )
    expired = result.scalars().all()
    for r in expired:
        r.is_active = False
        await _log(db, admin, "auto_deactivate", "restaurant", r.id, r.name,
                   {"reason": "trial_expired", "expired_at": str(r.trial_ends_at)})
    await db.commit()
    return {"deactivated": len(expired), "restaurants": [r.name for r in expired]}


# ── Dashboard Stats ───────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    now = datetime.now(timezone.utc)

    # Single query: all restaurant counters via conditional aggregation
    rest_stats = await db.execute(
        select(
            func.count().label("total"),
            func.sum(case((Restaurant.is_active == True, 1), else_=0)).label("active"),
            func.sum(case(
                (and_(Restaurant.trial_ends_at <= now + timedelta(days=7),
                      Restaurant.trial_ends_at >= now,
                      Restaurant.is_active == True), 1), else_=0
            )).label("exp_7"),
            func.sum(case(
                (and_(Restaurant.trial_ends_at <= now + timedelta(days=30),
                      Restaurant.trial_ends_at >= now,
                      Restaurant.is_active == True), 1), else_=0
            )).label("exp_30"),
            func.sum(case((Restaurant.trial_ends_at < now, 1), else_=0)).label("expired"),
        ).select_from(Restaurant)
    )
    rs = rest_stats.one()

    # Two remaining queries run concurrently
    users_q   = db.execute(select(func.count()).select_from(User).where(User.role != "super_admin"))
    orders_q  = db.execute(select(func.count()).select_from(Order))
    revenue_q = db.execute(
        select(func.coalesce(func.sum(Order.total_amount), 0)).where(Order.payment_status == "paid")
    )
    plans_q   = db.execute(select(Restaurant.plan, func.count()).group_by(Restaurant.plan))
    total_users, total_orders, total_revenue, plan_counts_result = await asyncio.gather(
        users_q, orders_q, revenue_q, plans_q
    )

    return {
        "total_restaurants":   rs.total,
        "active_restaurants":  rs.active,
        "total_users":         total_users.scalar(),
        "total_orders":        total_orders.scalar(),
        "total_revenue":       float(total_revenue.scalar() or 0),
        "restaurants_by_plan": {row[0]: row[1] for row in plan_counts_result.all()},
        "expiring_in_7_days":  rs.exp_7,
        "expiring_in_30_days": rs.exp_30,
        "expired_trials":      rs.expired,
    }


# ── Activity Log ──────────────────────────────────────────────────────────────

@router.get("/activity-log")
async def list_activity_log(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    search: Optional[str] = None,
    action: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    q = select(ActivityLog)
    if search:
        q = q.where(
            ActivityLog.admin_name.ilike(f"%{search}%") |
            ActivityLog.target_name.ilike(f"%{search}%")
        )
    if action:
        q = q.where(ActivityLog.action == action)
    if date_from:
        try:
            df = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            q = q.where(ActivityLog.created_at >= df)
        except Exception:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            q = q.where(ActivityLog.created_at <= dt)
        except Exception:
            pass

    count_q = select(func.count()).select_from(q.subquery())
    total = await db.execute(count_q)
    result = await db.execute(q.order_by(ActivityLog.created_at.desc()).limit(limit).offset(offset))
    logs = result.scalars().all()
    return {
        "total": total.scalar(),
        "logs": [
            {
                "id": l.id,
                "admin_id": l.admin_id,
                "admin_name": l.admin_name,
                "action": l.action,
                "target_type": l.target_type,
                "target_id": l.target_id,
                "target_name": l.target_name,
                "details": l.details,
                "created_at": l.created_at,
            }
            for l in logs
        ],
    }


# ── Announcements ─────────────────────────────────────────────────────────────

@router.get("/announcements")
async def list_announcements(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    result = await db.execute(
        select(Announcement).order_by(Announcement.created_at.desc())
    )
    return [_announcement_dict(a) for a in result.scalars().all()]


@router.post("/announcements", status_code=201)
async def create_announcement(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    if not payload.get("title") or not payload.get("body"):
        raise HTTPException(400, "Title and body are required.")
    a = Announcement(
        title=payload["title"],
        body=payload["body"],
        is_active=payload.get("is_active", True),
        created_by=admin.id,
    )
    db.add(a)
    await db.flush()
    await _log(db, admin, "create_announcement", "announcement", a.id, a.title)
    await db.commit()
    await db.refresh(a)
    return _announcement_dict(a)


@router.patch("/announcements/{ann_id}")
async def update_announcement(
    ann_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    result = await db.execute(select(Announcement).where(Announcement.id == ann_id))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Announcement not found.")
    if "title" in payload:
        a.title = payload["title"]
    if "body" in payload:
        a.body = payload["body"]
    if "is_active" in payload:
        a.is_active = payload["is_active"]
    await db.commit()
    await db.refresh(a)
    return _announcement_dict(a)


@router.delete("/announcements/{ann_id}", status_code=204)
async def delete_announcement(
    ann_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    result = await db.execute(select(Announcement).where(Announcement.id == ann_id))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Announcement not found.")
    await db.delete(a)
    await db.commit()


def _announcement_dict(a: Announcement) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "body": a.body,
        "is_active": a.is_active,
        "created_by": a.created_by,
        "created_at": a.created_at,
    }
