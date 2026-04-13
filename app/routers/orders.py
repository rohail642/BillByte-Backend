from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import List
from datetime import datetime, date
import random

from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.customer import Customer, LoyaltyTransaction
from app.models.user import User, Restaurant
from app.schemas.order import OrderCreate, OrderOut, OrderStatusUpdate, PaymentUpdate
from app.core.security import get_current_user

router = APIRouter(prefix="/orders", tags=["Orders"])


def _make_order_number() -> str:
    return f"BB-{date.today().strftime('%Y%m%d')}-{random.randint(1000,9999)}"


def _calc(subtotal: float, gst_rate: float, disc_pct: float):
    disc = round(subtotal * disc_pct / 100, 2)
    after = subtotal - disc
    gst = round(after * gst_rate / 100, 2)
    return disc, gst, round(after + gst, 2)


@router.post("/", response_model=OrderOut, status_code=201)
async def create_order(body: OrderCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0

    subtotal = 0.0
    items = []
    for it in body.items:
        lt = round(it.price * it.quantity, 2)
        subtotal += lt
        items.append(OrderItem(menu_item_id=it.menu_item_id, name=it.name,
                               price=it.price, quantity=it.quantity, total=lt, notes=it.notes))

    disc, gst, total = _calc(subtotal, gst_rate, body.discount_percent)
    order = Order(
        restaurant_id=u.restaurant_id,
        order_number=_make_order_number(),
        order_type=body.order_type,
        table_number=body.table_number,
        customer_id=body.customer_id,
        customer_name=body.customer_name,
        customer_phone=body.customer_phone,
        subtotal=round(subtotal, 2), gst_amount=gst,
        discount_amount=disc, discount_percent=body.discount_percent,
        total_amount=total, status="pending", payment_status="unpaid",
        platform=body.platform, platform_order_id=body.platform_order_id,
        notes=body.notes, created_by=u.id, items=items,
    )
    db.add(order)
    await db.flush()
    await db.refresh(order, ["items"])
    return order


@router.get("/", response_model=List[OrderOut])
async def list_orders(
    status: str | None = None,
    order_type: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    q = (select(Order).where(Order.restaurant_id == u.restaurant_id)
         .options(selectinload(Order.items)).order_by(Order.created_at.desc()).limit(limit))
    if status: q = q.where(Order.status == status)
    if order_type: q = q.where(Order.order_type == order_type)
    r = await db.execute(q)
    return r.scalars().all()


@router.get("/summary", tags=["Dashboard"])
async def dashboard_summary(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rid = u.restaurant_id

    rev = await db.execute(
        select(func.coalesce(func.sum(Order.total_amount), 0))
        .where(Order.restaurant_id == rid, Order.payment_status == "paid", Order.created_at >= today)
    )
    cnt = await db.execute(
        select(func.count(Order.id)).where(Order.restaurant_id == rid, Order.created_at >= today)
    )
    pending = await db.execute(
        select(func.count(Order.id)).where(Order.restaurant_id == rid, Order.status == "pending")
    )
    revenue = rev.scalar()
    orders = cnt.scalar()
    return {
        "today_revenue": revenue,
        "today_orders": orders,
        "avg_bill": round(revenue / orders, 2) if orders else 0,
        "pending_orders": pending.scalar(),
    }


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(Order).where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order: raise HTTPException(404, "Order not found")
    return order


@router.patch("/{order_id}/status", response_model=OrderOut)
async def update_status(order_id: int, body: OrderStatusUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(Order).where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order: raise HTTPException(404, "Order not found")

    prev_status = order.status
    order.status = body.status

    # Auto-deduct inventory when KOT is sent (only once — not if already past pending)
    if body.status == "kot_sent" and prev_status == "pending":
        from app.routers.recipes import deduct_inventory_for_order
        await deduct_inventory_for_order(order.items, u.restaurant_id, order.id, db)

    return order


@router.patch("/{order_id}/pay", response_model=OrderOut)
async def collect_payment(order_id: int, body: PaymentUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(Order).where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order: raise HTTPException(404, "Order not found")

    # Capture previous status before we change anything
    order_prev_status = order.status
    prev_pay_status   = order.payment_status

    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0
    disc, gst, total = _calc(order.subtotal, gst_rate, body.discount_percent)

    order.discount_percent = body.discount_percent
    order.discount_amount = disc
    order.gst_amount = gst
    order.total_amount = total
    order.payment_method = body.payment_method
    order.payment_status = "paid"
    order.status = "paid"

    # Auto-deduct inventory only if KOT was never sent (order went straight to paid)
    if prev_pay_status != 'paid' and order_prev_status not in ('kot_sent', 'preparing', 'ready', 'served'):
        from app.routers.recipes import deduct_inventory_for_order
        await deduct_inventory_for_order(order.items, u.restaurant_id, order.id, db)

    # Award loyalty points — 1 pt per ₹10
    if order.customer_id:
        cr = await db.execute(select(Customer).where(Customer.id == order.customer_id))
        cust = cr.scalar_one_or_none()
        if cust:
            pts = int(total // 10)
            cust.loyalty_points += pts
            cust.total_spent += total
            cust.total_visits += 1
            cust.last_visit_at = datetime.utcnow()
            if pts > 0:
                db.add(LoyaltyTransaction(
                    customer_id=cust.id, restaurant_id=u.restaurant_id,
                    order_id=order.id, points=pts,
                    description=f"Earned from order {order.order_number}",
                ))
    return order