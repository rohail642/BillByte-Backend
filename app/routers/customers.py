from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from typing import List
from datetime import datetime

from app.db.session import get_db
from app.models.customer import Customer, LoyaltyTransaction
from app.models.order import Order, OrderItem
from app.models.user import User
from app.schemas.customer import CustomerCreate, CustomerUpdate, CustomerOut, RedeemPointsRequest
from app.core.security import get_current_user

router = APIRouter(prefix="/customers", tags=["CRM"])


@router.get("/", response_model=List[CustomerOut])
async def list_customers(
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    q = (select(Customer)
         .where(Customer.restaurant_id == u.restaurant_id, Customer.is_active == True)
         .order_by(Customer.total_visits.desc()))
    if search:
        q = q.where(or_(Customer.name.ilike(f"%{search}%"), Customer.phone.ilike(f"%{search}%")))
    r = await db.execute(q)
    return r.scalars().all()


@router.get("/lookup")
async def lookup_by_phone(
    phone: str,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Used in Billing to find customer by phone and show their points."""
    r = await db.execute(
        select(Customer).where(
            Customer.restaurant_id == u.restaurant_id,
            Customer.phone.ilike(f"%{phone}%"),
            Customer.is_active == True,
        )
    )
    cust = r.scalar_one_or_none()
    if not cust:
        return None
    return {
        "id": cust.id,
        "name": cust.name,
        "phone": cust.phone,
        "loyalty_points": cust.loyalty_points,
        "total_visits": cust.total_visits,
        "total_spent": cust.total_spent,
        "points_value": cust.loyalty_points,  # 1 point = ₹1
    }


@router.post("/", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    existing = await db.execute(
        select(Customer).where(Customer.restaurant_id == u.restaurant_id, Customer.phone == body.phone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Customer with this phone already exists")
    cust = Customer(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(cust)
    await db.flush()
    return cust


@router.get("/{cust_id}", response_model=CustomerOut)
async def get_customer(cust_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Customer).where(Customer.id == cust_id, Customer.restaurant_id == u.restaurant_id))
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(404, "Customer not found")
    return cust


@router.patch("/{cust_id}", response_model=CustomerOut)
async def update_customer(cust_id: int, body: CustomerUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Customer).where(Customer.id == cust_id, Customer.restaurant_id == u.restaurant_id))
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(404, "Customer not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(cust, k, v)
    return cust


@router.get("/{cust_id}/history")
async def get_order_history(
    cust_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Full order + loyalty history for a customer."""
    r = await db.execute(select(Customer).where(Customer.id == cust_id, Customer.restaurant_id == u.restaurant_id))
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(404, "Customer not found")

    # Get orders linked by customer_id OR by customer_name+phone
    orders_r = await db.execute(
        select(Order)
        .where(Order.restaurant_id == u.restaurant_id, Order.customer_id == cust_id)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .limit(20)
    )
    orders = orders_r.scalars().all()

    # Get loyalty transactions
    lt_r = await db.execute(
        select(LoyaltyTransaction)
        .where(LoyaltyTransaction.customer_id == cust_id)
        .order_by(LoyaltyTransaction.created_at.desc())
        .limit(50)
    )
    transactions = lt_r.scalars().all()

    return {
        "orders": [
            {
                "id": o.id,
                "order_number": o.order_number,
                "order_type": o.order_type,
                "table_number": o.table_number,
                "total_amount": o.total_amount,
                "payment_method": o.payment_method,
                "payment_status": o.payment_status,
                "status": o.status,
                "created_at": o.created_at.isoformat(),
                "items": [{"name": i.name, "quantity": i.quantity, "total": i.total} for i in o.items],
            }
            for o in orders
        ],
        "loyalty_transactions": [
            {
                "id": t.id,
                "points": t.points,
                "description": t.description,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ],
    }


@router.post("/{cust_id}/redeem", response_model=CustomerOut)
async def redeem_points(cust_id: int, body: RedeemPointsRequest, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Customer).where(Customer.id == cust_id, Customer.restaurant_id == u.restaurant_id))
    cust = r.scalar_one_or_none()
    if not cust:
        raise HTTPException(404, "Customer not found")
    if body.points < 100:
        raise HTTPException(400, "Minimum 100 points required to redeem")
    if cust.loyalty_points < body.points:
        raise HTTPException(400, f"Only {cust.loyalty_points} points available")
    cust.loyalty_points -= body.points
    db.add(LoyaltyTransaction(
        customer_id=cust.id, restaurant_id=u.restaurant_id,
        points=-body.points,
        description=f"Redeemed {body.points} points = ₹{body.points} discount",
    ))
    return cust