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
