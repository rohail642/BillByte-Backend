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


_KOT_PRIORITY = ['kot_sent', 'preparing', 'ready', 'served']

def _derive_order_status(kot_statuses: dict) -> str:
    """Derive order-level status from all KOT statuses (minimum progress wins)."""
    statuses = list(kot_statuses.values())
    if not statuses:
        return 'kot_sent'
    if all(s == 'served' for s in statuses):
        return 'served'
    active = [s for s in statuses if s != 'served']
    return min(active, key=lambda s: _KOT_PRIORITY.index(s) if s in _KOT_PRIORITY else 99)


@router.post("/", response_model=OrderOut, status_code=201)
async def create_order(body: OrderCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0

    # ── For dine-in with a table, check if there is already an unpaid order today ──
    if body.order_type == "dine_in" and body.table_number:
        today_start = datetime.combine(date.today(), datetime.min.time())
        existing_r = await db.execute(
            select(Order)
            .where(
                Order.restaurant_id == u.restaurant_id,
                Order.table_number == body.table_number,
                Order.payment_status == "unpaid",
                Order.status != "cancelled",
                Order.order_type == "dine_in",
                Order.created_at >= today_start,
            )
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        existing = existing_r.scalar_one_or_none()
        if existing:
            # Determine next KOT number for this batch of new items
            next_kot = max((i.kot_number for i in existing.items), default=0) + 1
            new_subtotal = existing.subtotal
            for it in body.items:
                lt = round(it.price * it.quantity, 2)
                new_subtotal += lt
                existing.items.append(OrderItem(
                    menu_item_id=it.menu_item_id, name=it.name,
                    price=it.price, quantity=it.quantity, total=lt,
                    kot_number=next_kot,
                ))
            disc, gst, total = _calc(new_subtotal, gst_rate, existing.discount_percent)
            existing.subtotal = round(new_subtotal, 2)
            existing.gst_amount = gst
            existing.discount_amount = disc
            existing.total_amount = total
            # Track per-KOT status; preserve existing KOTs, add the new one
            statuses = dict(existing.kot_statuses or {})
            statuses[str(next_kot)] = 'kot_sent'
            existing.kot_statuses = statuses
            existing.status = _derive_order_status(statuses)
            await db.flush()
            await db.refresh(existing, ["items"])
            return existing

    # ── New order ──────────────────────────────────────────────────────────────────
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
        kot_statuses={'1': 'kot_sent'},
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






@router.get("/table/{table_number}", response_model=OrderOut)
async def get_table_order(
    table_number: str,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Get the active (unpaid) order for a table today."""
    from datetime import date
    today_start = datetime.combine(date.today(), datetime.min.time())
    r = await db.execute(
        select(Order)
        .where(
            Order.restaurant_id == u.restaurant_id,
            Order.table_number == table_number,
            Order.payment_status == "unpaid",
            Order.status != "cancelled",
            Order.order_type == "dine_in",
            Order.created_at >= today_start,
        )
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .limit(1)
    )
    order = r.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "No active order for this table")
    return order


@router.post("/{order_id}/add-items", response_model=OrderOut)
async def add_items_to_order(
    order_id: int,
    body: OrderCreate,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Add more items to an existing unpaid order (for additional KOTs)."""
    r = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.payment_status == "paid":
        raise HTTPException(400, "Cannot add items to a paid order")

    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0

    # Add new items with the next KOT number
    next_kot = max((i.kot_number for i in order.items), default=0) + 1
    new_subtotal = order.subtotal
    for it in body.items:
        lt = round(it.price * it.quantity, 2)
        new_subtotal += lt
        order.items.append(OrderItem(
            menu_item_id=it.menu_item_id, name=it.name,
            price=it.price, quantity=it.quantity, total=lt,
            kot_number=next_kot,
        ))

    # Recalculate totals and surface new items to kitchen
    disc, gst, total = _calc(new_subtotal, gst_rate, order.discount_percent)
    order.subtotal = round(new_subtotal, 2)
    order.gst_amount = gst
    order.discount_amount = disc
    order.total_amount = total
    statuses = dict(order.kot_statuses or {})
    statuses[str(next_kot)] = 'kot_sent'
    order.kot_statuses = statuses
    order.status = _derive_order_status(statuses)

    await db.flush()
    await db.refresh(order, ["items"])
    return order


@router.delete("/{order_id}/items/{item_id}", response_model=OrderOut)
async def remove_order_item(
    order_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Remove a specific item from an unpaid order."""
    r = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.payment_status == "paid":
        raise HTTPException(400, "Cannot modify a paid order")

    item = next((i for i in order.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Item not found")
    if item.cancelled_at:
        raise HTTPException(400, "Item is already cancelled")

    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0

    # Soft-delete so kitchen sees the void
    item.cancelled_at = datetime.utcnow()
    order.subtotal = round(order.subtotal - item.total, 2)
    await db.flush()

    # Auto-cancel if no active items remain
    active_items = [i for i in order.items if not i.cancelled_at]
    if not active_items:
        order.status = 'cancelled'
        order.subtotal = 0
        order.gst_amount = 0
        order.discount_amount = 0
        order.total_amount = 0
        await db.flush()
        await db.refresh(order, ["items"])
        return order

    disc, gst, total = _calc(order.subtotal, gst_rate, order.discount_percent)
    order.gst_amount = gst
    order.discount_amount = disc
    order.total_amount = total

    # Build a complete kot_statuses covering every KOT in this order so
    # _derive_order_status never sees a partial dict and mis-derives the status.
    cancelled_kot = item.kot_number or 1
    all_kot_nums  = {str(i.kot_number or 1) for i in order.items}
    statuses      = dict(order.kot_statuses or {})
    # Seed any KOT that has no entry yet with the current order status
    for k in all_kot_nums:
        if k not in statuses:
            statuses[k] = order.status if order.status in _KOT_PRIORITY else 'kot_sent'

    kot_items = [i for i in order.items if (i.kot_number or 1) == cancelled_kot]
    if kot_items and all(i.cancelled_at for i in kot_items):
        # All items in this KOT were voided — hide it from the kitchen
        statuses[str(cancelled_kot)] = 'served'
    elif order.status not in ('pending', 'paid', 'cancelled'):
        # Partial void: surface the card back to the kitchen
        statuses[str(cancelled_kot)] = 'kot_sent'

    order.kot_statuses = statuses
    order.status = _derive_order_status(statuses)

    await db.flush()
    await db.refresh(order, ["items"])
    return order



@router.patch("/table/{table_number}/collect-all")
async def collect_all_for_table(
    table_number: str,
    body: PaymentUpdate,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Collect payment for ALL unpaid orders on a table at once."""
    today_start = datetime.combine(date.today(), datetime.min.time())
    r = await db.execute(
        select(Order)
        .where(
            Order.restaurant_id == u.restaurant_id,
            Order.table_number == table_number,
            Order.payment_status == "unpaid",
            Order.status != "cancelled",
            Order.created_at >= today_start,
        )
        .options(selectinload(Order.items))
    )
    orders = r.scalars().all()
    if not orders:
        raise HTTPException(404, "No unpaid orders for this table")

    rest = await db.get(Restaurant, u.restaurant_id)
    gst_rate = rest.gst_rate if rest else 5.0
    total_collected = 0.0

    for order in orders:
        disc, gst, total = _calc(order.subtotal, gst_rate, order.discount_percent)
        order.gst_amount = gst
        order.discount_amount = disc
        order.total_amount = total
        order.payment_method = body.payment_method
        order.payment_status = "paid"
        order.status = "paid"
        total_collected += total

        # Award loyalty points and update customer stats
        if order.customer_id:
            cr = await db.execute(select(Customer).where(Customer.id == order.customer_id))
            cust = cr.scalar_one_or_none()
            if cust:
                net_amount = max(0, (order.subtotal or 0) - (order.discount_amount or 0))
                pts_earned = int(net_amount // 10)
                if pts_earned > 0:
                    cust.loyalty_points += pts_earned
                    db.add(LoyaltyTransaction(
                        customer_id=cust.id, restaurant_id=u.restaurant_id,
                        order_id=order.id, points=pts_earned,
                        description=f"Earned from order {order.order_number}",
                    ))
                cust.total_spent += total
                cust.total_visits += 1
                cust.last_visit_at = datetime.utcnow()

    return {"message": f"Collected ₹{round(total_collected,2)} from {len(orders)} order(s) on Table {table_number}", "total": round(total_collected, 2), "orders_paid": len(orders)}

@router.get("/active-tables")
async def get_active_tables(
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Returns all tables with unpaid dine-in orders today, with their items."""
    from datetime import date
    today_start = datetime.combine(date.today(), datetime.min.time())
    r = await db.execute(
        select(Order)
        .where(
            Order.restaurant_id == u.restaurant_id,
            Order.order_type == "dine_in",
            Order.payment_status == "unpaid",
            Order.status != "cancelled",
            Order.table_number != None,
            Order.created_at >= today_start,
        )
        .options(selectinload(Order.items))
        .order_by(Order.table_number, Order.created_at)
    )
    orders = r.scalars().all()

    # Group by table number
    tables = {}
    for o in orders:
        t = o.table_number
        if t not in tables:
            tables[t] = {
                "table_number": t,
                "orders": [],
                "total_amount": 0,
                "items_count": 0,
                "customer_name": o.customer_name,
            }
        tables[t]["orders"].append({
            "id": o.id,
            "order_number": o.order_number,
            "status": o.status,
            "total_amount": o.total_amount,
            "created_at": o.created_at.isoformat(),
            "items": [{"id": i.id, "name": i.name, "quantity": i.quantity, "price": i.price, "total": i.total, "menu_item_id": i.menu_item_id} for i in o.items if not i.cancelled_at],
        })
        tables[t]["total_amount"] += o.total_amount
        tables[t]["items_count"] += sum(i.quantity for i in o.items if not i.cancelled_at)
        if o.customer_name and not tables[t]["customer_name"]:
            tables[t]["customer_name"] = o.customer_name

    return list(tables.values())

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


@router.patch("/{order_id}/kot/{kot_number}/status", response_model=OrderOut)
async def update_kot_status(
    order_id: int, kot_number: int, body: OrderStatusUpdate,
    db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)
):
    r = await db.execute(
        select(Order).where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order: raise HTTPException(404, "Order not found")

    statuses = dict(order.kot_statuses or {str(kot_number): order.status})
    statuses[str(kot_number)] = body.status
    order.kot_statuses = statuses
    order.status = _derive_order_status(statuses)
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

    # CRM: award loyalty points and update customer stats
    if order.customer_id:
        cr = await db.execute(select(Customer).where(Customer.id == order.customer_id))
        cust = cr.scalar_one_or_none()
        if cust:
            # Handle points redemption atomically — deduct before awarding to prevent double-dipping
            redeemed = False
            if body.points_to_redeem > 0:
                pts_to_redeem = body.points_to_redeem
                if pts_to_redeem < 100:
                    raise HTTPException(400, "Minimum 100 points required to redeem")
                if cust.loyalty_points < pts_to_redeem:
                    raise HTTPException(400, f"Only {cust.loyalty_points} points available")
                cust.loyalty_points -= pts_to_redeem
                db.add(LoyaltyTransaction(
                    customer_id=cust.id, restaurant_id=u.restaurant_id,
                    order_id=order.id, points=-pts_to_redeem,
                    description=f"Redeemed {pts_to_redeem} points on order {order.order_number}",
                ))
                redeemed = True

            # Award earned points only if no redemption happened on this order
            if not redeemed:
                net_amount = max(0, (order.subtotal or 0) - (order.discount_amount or 0))
                pts_earned = int(net_amount // 10)
                if pts_earned > 0:
                    cust.loyalty_points += pts_earned
                    db.add(LoyaltyTransaction(
                        customer_id=cust.id, restaurant_id=u.restaurant_id,
                        order_id=order.id, points=pts_earned,
                        description=f"Earned from order {order.order_number}",
                    ))

            cust.total_spent += total
            cust.total_visits += 1
            cust.last_visit_at = datetime.utcnow()
    return order