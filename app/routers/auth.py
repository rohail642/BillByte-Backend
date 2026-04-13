from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta

from app.db.session import get_db
from app.models.user import User, Restaurant
from app.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse,
    UserOut, UpdateProfileRequest, ProfileOut
)
from app.core.security import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    restaurant = Restaurant(
        name=body.restaurant_name,
        plan="trial",
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
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

    token = create_access_token({"sub": str(user.id), "restaurant_id": str(restaurant.id)})
    return TokenResponse(
        access_token=token, user_id=user.id,
        restaurant_id=restaurant.id, name=user.name, role=user.role,
        restaurant_name=restaurant.name, phone=user.phone,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account inactive")

    rest = await db.get(Restaurant, user.restaurant_id)
    token = create_access_token({"sub": str(user.id), "restaurant_id": str(user.restaurant_id)})
    return TokenResponse(
        access_token=token, user_id=user.id,
        restaurant_id=user.restaurant_id, name=user.name, role=user.role,
        restaurant_name=rest.name if rest else None, phone=user.phone,
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/profile", response_model=ProfileOut)
async def get_profile(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rest = await db.get(Restaurant, current_user.restaurant_id)
    if not rest:
        raise HTTPException(404, "Restaurant not found")
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

    # Update user fields
    if body.name is not None:       current_user.name  = body.name
    if body.phone is not None:      current_user.phone = body.phone

    # Update restaurant fields
    if body.restaurant_name is not None: rest.name    = body.restaurant_name
    if body.address is not None:         rest.address = body.address
    if body.city is not None:            rest.city    = body.city
    if body.gstin is not None:           rest.gstin   = body.gstin
    if body.fssai is not None:           rest.fssai   = body.fssai
    if body.gst_rate is not None:        rest.gst_rate= body.gst_rate

    await db.flush()
    return ProfileOut(
        name=current_user.name, email=current_user.email,
        phone=current_user.phone, restaurant_name=rest.name,
        address=rest.address, city=rest.city,
        gstin=rest.gstin, fssai=rest.fssai, gst_rate=rest.gst_rate,
    )
