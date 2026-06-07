from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta

from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter(prefix="/reports", tags=["Reports"])


def _start(period: str) -> datetime:
    now = datetime.utcnow()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        return now - timedelta(days=7)
    else:
        return now - timedelta(days=30)


@router.get("/sales")
async def sales_report(period: str = "today", db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    start = _start(period)
    rid = u.restaurant_id

    totals = await db.execute(
        select(
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            func.count(Order.id).label("orders"),
            func.coalesce(func.sum(Order.gst_amount), 0).label("gst"),
            func.coalesce(func.sum(Order.discount_amount), 0).label("discounts"),
        ).where(Order.restaurant_id == rid, Order.payment_status == "paid", Order.created_at >= start)
    )
    row = totals.one()

    by_type = await db.execute(
        select(Order.order_type, func.sum(Order.total_amount).label("rev"))
        .where(Order.restaurant_id == rid, Order.payment_status == "paid", Order.created_at >= start)
        .group_by(Order.order_type)
    )

    by_payment = await db.execute(
        select(Order.payment_method, func.count(Order.id).label("count"))
        .where(Order.restaurant_id == rid, Order.payment_status == "paid", Order.created_at >= start)
        .group_by(Order.payment_method)
    )

    return {
        "period": period,
        "revenue": round(row.revenue, 2),
        "orders": row.orders,
        "gst_collected": round(row.gst, 2),
        "discounts_given": round(row.discounts, 2),
        "avg_bill": round(row.revenue / row.orders, 2) if row.orders else 0,
        "by_order_type": {r.order_type: round(r.rev, 2) for r in by_type},
        "by_payment_method": {r.payment_method: r.count for r in by_payment if r.payment_method},
    }


@router.get("/top-dishes")
async def top_dishes(period: str = "today", limit: int = 10, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    start = _start(period)
    r = await db.execute(
        select(
            OrderItem.name,
            func.sum(OrderItem.quantity).label("qty"),
            func.sum(OrderItem.total).label("revenue"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.restaurant_id == u.restaurant_id, Order.created_at >= start)
        .group_by(OrderItem.name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
    )
    return [{"name": row.name, "quantity": int(row.qty), "revenue": round(row.revenue, 2)} for row in r]


@router.get("/revenue-trend")
async def revenue_trend(days: int = 7, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    """Daily revenue for the past N days — feeds the dashboard chart."""
    start = datetime.utcnow() - timedelta(days=days)
    r = await db.execute(
        select(
            func.date(Order.created_at).label("day"),
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            func.count(Order.id).label("orders"),
        )
        .where(Order.restaurant_id == u.restaurant_id, Order.payment_status == "paid", Order.created_at >= start)
        .group_by(func.date(Order.created_at))
        .order_by(func.date(Order.created_at))
    )
    return [{"date": str(row.day), "revenue": round(row.revenue, 2), "orders": row.orders} for row in r]


@router.get("/gst-summary")
async def gst_summary(
    month: int | None = None,
    year:  int | None = None,
    date_from: str | None = None,
    date_to:   str | None = None,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """GST summary — accepts either date_from/date_to range or month/year."""
    from datetime import date, timedelta
    today = date.today()

    if date_from and date_to:
        month = date.fromisoformat(date_from).month
        year  = date.fromisoformat(date_from).year
    else:
        month = month or today.month
        year  = year  or today.year

    base = select(
        Order.order_number, Order.created_at, Order.order_type,
        Order.subtotal, Order.gst_amount, Order.total_amount, Order.payment_method,
    ).where(Order.restaurant_id == u.restaurant_id, Order.payment_status == "paid")

    if date_from and date_to:
        base = base.where(
            func.date(Order.created_at) >= date_from,
            func.date(Order.created_at) <= date_to,
        )
    else:
        start = datetime(year, month, 1)
        end   = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        base  = base.where(Order.created_at >= start, Order.created_at < end)

    r = await db.execute(base.order_by(Order.created_at))
    orders = r.all()

    total_taxable = sum(o.subtotal or 0 for o in orders)
    total_gst     = sum(o.gst_amount or 0 for o in orders)
    cgst          = round(total_gst / 2, 2)
    sgst          = round(total_gst / 2, 2)

    return {
        "month": month,
        "year": year,
        "total_orders": len(orders),
        "total_taxable_value": round(total_taxable, 2),
        "total_gst": round(total_gst, 2),
        "cgst": cgst,
        "sgst": sgst,
        "orders": [
            {
                "order_number": o.order_number,
                "date": o.created_at.strftime("%d-%b-%Y"),
                "time": o.created_at.strftime("%I:%M %p"),
                "order_type": o.order_type,
                "taxable_value": round(o.subtotal or 0, 2),
                "gst": round(o.gst_amount or 0, 2),
                "cgst": round((o.gst_amount or 0) / 2, 2),
                "sgst": round((o.gst_amount or 0) / 2, 2),
                "total": round(o.total_amount or 0, 2),
                "payment": o.payment_method or "",
            }
            for o in orders
        ],
    }



@router.get("/inventory-report")
async def inventory_report(
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Current inventory snapshot — stock levels, value, low stock."""
    from app.models.inventory import InventoryItem
    r = await db.execute(
        select(InventoryItem)
        .where(InventoryItem.restaurant_id == u.restaurant_id, InventoryItem.is_active == True)
        .order_by(InventoryItem.name)
    )
    items = r.scalars().all()

    total_value = sum(i.stock_value for i in items)
    low_count   = sum(1 for i in items if i.is_low_stock)

    return {
        "total_items": len(items),
        "total_value": round(total_value, 2),
        "low_stock_count": low_count,
        "items": [
            {
                "name": i.name,
                "category": i.category or "",
                "quantity": i.quantity,
                "unit": i.unit,
                "min_quantity": i.min_quantity,
                "cost_per_unit": i.cost_per_unit,
                "stock_value": i.stock_value,
                "status": "Low Stock" if i.is_low_stock else "OK",
                "expiry_date": str(i.expiry_date) if i.expiry_date else "",
            }
            for i in items
        ],
    }