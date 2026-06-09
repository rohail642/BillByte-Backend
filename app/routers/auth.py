from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta, timezone

from app.db.session import get_db
from app.models.user import User, Restaurant
from app.models.admin_models import ActivityLog, Announcement
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse,
    UserOut, UpdateProfileRequest, ProfileOut, ChangePasswordRequest
)
from app.core.security import hash_password, verify_password, create_access_token, get_current_user
from app.core.limiter import limiter
from app.core.sanitize import SafeStr, SafeOptStr
from app.core.validators import validate_password

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    restaurant = Restaurant(
        name=body.restaurant_name,
        plan="trial",
        trial_ends_at=datetime.now(timezone.utc) + timedelta(days=14),
    )
    db.add(restaurant)
    await db.flush()

    user = User(
        name=body.name,
        email=body.email,
        phone=body.phone,
        hashed_password=hash_password(body.password),
        role="owner",
        restaurant_id=restaurant.id,
    )
    db.add(user)
    await db.flush()

    token = create_access_token({"sub": str(user.id), "restaurant_id": str(restaurant.id), "token_version": user.token_version or 0})
    days_left = None
    if restaurant.trial_ends_at:
        exp_date = (restaurant.trial_ends_at if restaurant.trial_ends_at.tzinfo else restaurant.trial_ends_at.replace(tzinfo=timezone.utc)).date()
        days_left = (exp_date - datetime.now(timezone.utc).date()).days
    from app.routers.admin import DEFAULT_MODULES
    return TokenResponse(
        access_token=token, user_id=user.id,
        restaurant_id=restaurant.id, name=user.name, role=user.role,
        restaurant_name=restaurant.name, phone=user.phone,
        trial_ends_at=restaurant.trial_ends_at, days_left=days_left,
        enabled_modules=restaurant.enabled_modules or DEFAULT_MODULES,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account inactive")

    rest = await db.get(Restaurant, user.restaurant_id) if user.restaurant_id else None

    # Auto-deactivate restaurant if trial/license has expired
    if rest and rest.trial_ends_at and rest.is_active:
        trial_aware = rest.trial_ends_at if rest.trial_ends_at.tzinfo else rest.trial_ends_at.replace(tzinfo=timezone.utc)
        if trial_aware < datetime.now(timezone.utc):
            rest.is_active = False
            db.add(ActivityLog(
                admin_name="system",
                action="auto_deactivate",
                target_type="restaurant",
                target_id=rest.id,
                target_name=rest.name,
                details={"reason": "trial_expired", "expired_at": str(rest.trial_ends_at)},
            ))
            await db.commit()  # commit before raise so rollback in get_db doesn't undo this
            raise HTTPException(403, "Your restaurant's license has expired. Please contact BillByte support to renew.")

    if rest and not rest.is_active:
        raise HTTPException(403, "Restaurant is suspended. Please contact BillByte support.")

    token = create_access_token({"sub": str(user.id), "restaurant_id": str(user.restaurant_id), "token_version": user.token_version or 0})

    # Log super admin login
    if user.role == "super_admin":
        db.add(ActivityLog(
            admin_id=user.id,
            admin_name=user.name,
            action="admin_login",
            target_type="system",
            details={"email": user.email, "ip": request.client.host if request.client else None},
        ))
        await db.flush()

    days_left = None
    if rest and rest.trial_ends_at:
        exp_date = (rest.trial_ends_at if rest.trial_ends_at.tzinfo else rest.trial_ends_at.replace(tzinfo=timezone.utc)).date()
        days_left = (exp_date - datetime.now(timezone.utc).date()).days

    from app.routers.admin import DEFAULT_MODULES
    return TokenResponse(
        access_token=token, user_id=user.id,
        restaurant_id=user.restaurant_id, name=user.name, role=user.role,
        restaurant_name=rest.name if rest else None, phone=user.phone,
        trial_ends_at=rest.trial_ends_at if rest else None, days_left=days_left,
        enabled_modules=rest.enabled_modules or DEFAULT_MODULES if rest else None,
    )


@router.post("/admin-logout", status_code=204)
async def admin_logout(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if current_user.role == "super_admin":
        db.add(ActivityLog(
            admin_id=current_user.id,
            admin_name=current_user.name,
            action="admin_logout",
            target_type="system",
            details={"email": current_user.email},
        ))
        await db.flush()


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/profile", response_model=ProfileOut)
async def get_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rest = await db.get(Restaurant, current_user.restaurant_id)
    if not rest:
        raise HTTPException(404, "Restaurant not found")
    from app.routers.admin import DEFAULT_MODULES
    days_left = None
    if rest.trial_ends_at:
        exp_date = (rest.trial_ends_at if rest.trial_ends_at.tzinfo else rest.trial_ends_at.replace(tzinfo=timezone.utc)).date()
        days_left = (exp_date - datetime.now(timezone.utc).date()).days
    return ProfileOut(
        name=current_user.name,
        email=current_user.email,
        phone=current_user.phone,
        restaurant_name=rest.name,
        address=rest.address,
        city=rest.city,
        gstin=rest.gstin,
        fssai=rest.fssai,
        gst_rate=rest.gst_rate,
        restaurant_id=rest.id,
        zomato_enabled=rest.zomato_enabled,
        zomato_restaurant_id=rest.zomato_restaurant_id,
        swiggy_enabled=rest.swiggy_enabled,
        swiggy_restaurant_id=rest.swiggy_restaurant_id,
        razorpay_enabled=rest.razorpay_enabled,
        razorpay_key_id=rest.razorpay_key_id,
        pinelabs_enabled=rest.pinelabs_enabled,
        pinelabs_merchant_id=rest.pinelabs_merchant_id,
        pinelabs_terminal_id=rest.pinelabs_terminal_id,
        table_count=rest.table_count,
        table_sections=rest.table_sections or [],
        enabled_modules=rest.enabled_modules or DEFAULT_MODULES,
        plan=rest.plan,
        trial_ends_at=rest.trial_ends_at,
        days_left=days_left,
        is_active=rest.is_active,
        round_off=rest.round_off if rest.round_off is not None else False,
        loyalty_enabled=rest.loyalty_enabled if rest.loyalty_enabled is not None else True,
        show_gst_breakup=rest.show_gst_breakup if rest.show_gst_breakup is not None else True,
    )


@router.patch("/profile", response_model=ProfileOut)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    rest = await db.get(Restaurant, current_user.restaurant_id)
    if not rest:
        raise HTTPException(404, "Restaurant not found")

    # Update user fields (phone only — name/email managed by admin)
    if body.phone is not None: current_user.phone = body.phone

    # Update restaurant fields (admin-only: name, address, city)
    if body.gstin is not None:    rest.gstin    = body.gstin
    if body.fssai is not None:    rest.fssai    = body.fssai
    if body.gst_rate is not None: rest.gst_rate = body.gst_rate
    # Integrations
    if body.zomato_enabled is not None:       rest.zomato_enabled       = body.zomato_enabled
    if body.zomato_secret is not None:        rest.zomato_secret        = body.zomato_secret
    if body.zomato_restaurant_id is not None: rest.zomato_restaurant_id = body.zomato_restaurant_id
    if body.swiggy_enabled is not None:       rest.swiggy_enabled       = body.swiggy_enabled
    if body.swiggy_secret is not None:        rest.swiggy_secret        = body.swiggy_secret
    if body.swiggy_restaurant_id is not None: rest.swiggy_restaurant_id = body.swiggy_restaurant_id
    if body.razorpay_enabled is not None:     rest.razorpay_enabled     = body.razorpay_enabled
    if body.razorpay_key_id is not None:      rest.razorpay_key_id      = body.razorpay_key_id
    if body.razorpay_key_secret is not None:  rest.razorpay_key_secret  = body.razorpay_key_secret
    if body.pinelabs_enabled is not None:        rest.pinelabs_enabled        = body.pinelabs_enabled
    if body.pinelabs_merchant_id is not None:    rest.pinelabs_merchant_id    = body.pinelabs_merchant_id
    if body.pinelabs_terminal_id is not None:    rest.pinelabs_terminal_id    = body.pinelabs_terminal_id
    if body.pinelabs_security_token is not None: rest.pinelabs_security_token = body.pinelabs_security_token
    if body.table_count is not None:    rest.table_count    = body.table_count
    if body.table_sections is not None: rest.table_sections = body.table_sections
    # Billing settings
    if body.round_off is not None:        rest.round_off        = body.round_off
    if body.loyalty_enabled is not None:  rest.loyalty_enabled  = body.loyalty_enabled
    if body.show_gst_breakup is not None: rest.show_gst_breakup = body.show_gst_breakup

    await db.commit()
    from app.routers.admin import DEFAULT_MODULES
    return ProfileOut(
        name=current_user.name, email=current_user.email,
        phone=current_user.phone, restaurant_name=rest.name,
        address=rest.address, city=rest.city,
        gstin=rest.gstin, fssai=rest.fssai, gst_rate=rest.gst_rate,
        restaurant_id=rest.id,
        zomato_enabled=rest.zomato_enabled,
        zomato_restaurant_id=rest.zomato_restaurant_id,
        swiggy_enabled=rest.swiggy_enabled,
        swiggy_restaurant_id=rest.swiggy_restaurant_id,
        razorpay_enabled=rest.razorpay_enabled,
        razorpay_key_id=rest.razorpay_key_id,
        pinelabs_enabled=rest.pinelabs_enabled,
        pinelabs_merchant_id=rest.pinelabs_merchant_id,
        pinelabs_terminal_id=rest.pinelabs_terminal_id,
        table_count=rest.table_count,
        table_sections=rest.table_sections or [],
        enabled_modules=rest.enabled_modules or DEFAULT_MODULES,
        round_off=rest.round_off if rest.round_off is not None else False,
        loyalty_enabled=rest.loyalty_enabled if rest.loyalty_enabled is not None else True,
        show_gst_breakup=rest.show_gst_breakup if rest.show_gst_breakup is not None else True,
    )


# ── Printer config (restaurant-wide, owner-configured, shared to all devices) ───
class PrinterEntryIn(BaseModel):
    name: SafeOptStr = ""
    type: SafeOptStr = "network"
    ip: SafeOptStr = ""
    usbName: SafeOptStr = ""
    categories: List[int] = []


class PrinterConfigIn(BaseModel):
    printers: List[PrinterEntryIn] = []
    billPrinter: Optional[PrinterEntryIn] = None


@router.get("/printer-config")
async def get_printer_config(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Any authenticated device (incl. waiter phones) reads the shared config."""
    rest = await db.get(Restaurant, current_user.restaurant_id)
    if not rest:
        raise HTTPException(404, "Restaurant not found")
    return rest.printer_config or {"printers": [], "billPrinter": None}


@router.put("/printer-config")
async def save_printer_config(
    body: PrinterConfigIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Only the owner/manager configures the restaurant's printers."""
    if current_user.role not in ("owner", "manager"):
        raise HTTPException(403, "Only the owner can configure printers")
    rest = await db.get(Restaurant, current_user.restaurant_id)
    if not rest:
        raise HTTPException(404, "Restaurant not found")
    rest.printer_config = body.model_dump()
    await db.commit()
    return rest.printer_config


# ── Change Password ────────────────────────────────────────────────────────────
@router.post("/change-password", status_code=204)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a new password after verifying the user's current password."""
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(400, "Current password is incorrect.")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    if verify_password(body.new_password, current_user.hashed_password):
        raise HTTPException(400, "New password must be different from the current one.")
    current_user.hashed_password = hash_password(body.new_password)
    # Revoke every previously-issued token for this user.
    current_user.token_version = (current_user.token_version or 0) + 1
    db.add(current_user)
    await db.commit()


# ── Team / User Management ─────────────────────────────────────────────────────
from app.core.security import require_role
from app.core.security import hash_password

class TeamMemberCreate(BaseModel):
    name: SafeStr
    email: EmailStr
    password: str
    role: str = "waiter"  # waiter|cashier|manager|kitchen

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return validate_password(v)

class TeamMemberOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    class Config:
        from_attributes = True

class TeamMemberUpdate(BaseModel):
    name: SafeOptStr = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        return validate_password(v) if v is not None else v


@router.get("/team", response_model=List[TeamMemberOut])
async def list_team(
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_role("owner", "manager")),
):
    """List all users in the restaurant."""
    r = await db.execute(
        select(User).where(User.restaurant_id == u.restaurant_id, User.id != u.id, User.is_active == True)
        .order_by(User.name)
    )
    return r.scalars().all()


@router.post("/team", response_model=TeamMemberOut, status_code=201)
async def add_team_member(
    body: TeamMemberCreate,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_role("owner")),
):
    """Owner adds a new staff member with login access."""
    # Only owner can create managers, managers can't create managers
    if body.role not in ("waiter", "cashier", "manager", "kitchen"):
        raise HTTPException(400, "Invalid role. Use: waiter, cashier, manager, kitchen")

    existing = await db.execute(select(User).where(User.email == body.email))
    existing_user = existing.scalar_one_or_none()

    if existing_user:
        if existing_user.restaurant_id != u.restaurant_id or existing_user.is_active:
            raise HTTPException(400, "Email already in use")
        # Reactivate previously removed member
        existing_user.name = body.name
        existing_user.role = body.role
        existing_user.hashed_password = hash_password(body.password)
        existing_user.is_active = True
        await db.commit()
        await db.refresh(existing_user)
        return existing_user

    member = User(
        restaurant_id=u.restaurant_id,
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    db.add(member)
    await db.flush()
    return member


@router.patch("/team/{user_id}", response_model=TeamMemberOut)
async def update_team_member(
    user_id: int,
    body: TeamMemberUpdate,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_role("owner")),
):
    r = await db.execute(
        select(User).where(User.id == user_id, User.restaurant_id == u.restaurant_id)
    )
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Team member not found")
    if member.id == u.id:
        raise HTTPException(400, "Cannot modify yourself here")

    if body.name is not None:     member.name      = body.name
    if body.role is not None:     member.role      = body.role
    if body.is_active is not None: member.is_active = body.is_active
    if body.password is not None:
        member.hashed_password = hash_password(body.password)
        member.token_version = (member.token_version or 0) + 1  # revoke old sessions
    # Deactivating a member should also kill their active sessions
    if body.is_active is False:
        member.token_version = (member.token_version or 0) + 1

    return member


@router.delete("/team/{user_id}", status_code=204)
async def remove_team_member(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(require_role("owner")),
):
    r = await db.execute(
        select(User).where(User.id == user_id, User.restaurant_id == u.restaurant_id)
    )
    member = r.scalar_one_or_none()
    if not member:
        raise HTTPException(404, "Team member not found")
    if member.id == u.id:
        raise HTTPException(400, "Cannot remove yourself")
    member.is_active = False
    await db.commit()


@router.get("/announcements")
async def get_active_announcements(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Announcement)
        .where(Announcement.is_active == True)
        .order_by(Announcement.created_at.desc())
    )
    announcements = result.scalars().all()
    return [
        {"id": a.id, "title": a.title, "body": a.body, "created_at": a.created_at}
        for a in announcements
    ]