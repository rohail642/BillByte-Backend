"""
Webhook endpoints for Zomato and Swiggy.

When you go live and get POS integration approved:
1. Give them your webhook URL: https://yourdomain.com/api/webhooks/zomato
2. They'll give you a secret key — add it to .env as ZOMATO_WEBHOOK_SECRET / SWIGGY_WEBHOOK_SECRET
3. Everything below will start working automatically.

Until then, use the Online Orders page to enter orders manually.
"""

import hmac
import hashlib
import json
from datetime import date

from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.menu import MenuItem
from app.models.user import Restaurant
from app.core.config import settings
from fastapi import Depends

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _calc(subtotal: float, gst_rate: float = 5.0):
    gst = round(subtotal * gst_rate / 100, 2)
    return gst, round(subtotal + gst, 2)


async def _get_restaurant_by_id(restaurant_id: int, db: AsyncSession):
    """Get restaurant by BillByte restaurant ID."""
    return await db.get(Restaurant, restaurant_id)


# ── ZOMATO ────────────────────────────────────────────────────────────────────
def _verify_zomato_signature(payload: bytes, signature: str, secret: Optional[str]) -> bool:
    """Verify Zomato webhook signature. Only bypasses when secret is None (not configured at all)."""
    if secret is None:
        return True  # dev mode — secret not yet configured
    if not secret:
        return False  # empty string = misconfigured, fail closed
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def _parse_zomato_order(data: dict) -> dict:
    """
    Translate Zomato's order format into BillByte's format.
    Zomato webhook docs: https://developers.zomato.com/documentation
    """
    order_data = data.get('order', data)
    items = []
    for item in order_data.get('items', []):
        items.append({
            'name':     item.get('name', 'Unknown Item'),
            'price':    float(item.get('item_price', 0)),
            'quantity': int(item.get('quantity', 1)),
        })

    customer = order_data.get('customer', {})
    address  = order_data.get('delivery_address', {})
    address_str = ', '.join(filter(None, [
        address.get('flat_no'), address.get('building'),
        address.get('locality'), address.get('city'),
    ]))

    return {
        'platform':           'zomato',
        'platform_order_id':  str(order_data.get('order_id', '')),
        'customer_name':      customer.get('name', ''),
        'customer_phone':     customer.get('phone', ''),
        'delivery_address':   address_str,
        'items':              items,
        'payment_method':     'online' if order_data.get('payment_method') == 'prepaid' else 'cash',
        'subtotal':           float(order_data.get('subtotal', 0)),
        'notes':              f"Zomato Order · Delivery to: {address_str}",
    }


@router.post("/zomato/{restaurant_id}")
async def zomato_webhook(
    restaurant_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_zomato_signature: Optional[str] = Header(None),
):
    payload = await request.body()

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "Invalid JSON payload")

    event = data.get('event', 'order.placed')

    if event not in ('order.placed', 'new_order', 'order_placed'):
        return {"status": "ignored", "event": event}

    restaurant = await _get_restaurant_by_id(restaurant_id, db)
    dev_mode = restaurant and restaurant.zomato_secret is None
    if not restaurant or (not restaurant.zomato_enabled and not dev_mode):
        raise HTTPException(404, "Restaurant not found or Zomato not enabled")

    if not _verify_zomato_signature(payload, x_zomato_signature or '', restaurant.zomato_secret):
        raise HTTPException(401, "Invalid Zomato signature")

    parsed = _parse_zomato_order(data)

    # Check for duplicate (idempotency)
    if parsed['platform_order_id']:
        dup = await db.execute(
            select(Order).where(
                Order.platform_order_id == parsed['platform_order_id'],
                Order.platform == 'zomato',
            )
        )
        if dup.scalar_one_or_none():
            return {"status": "duplicate", "message": "Order already exists"}

    # Resolve menu_item_ids by name so inventory deduction can work
    item_names = [i['name'] for i in parsed['items']]
    menu_r = await db.execute(
        select(MenuItem).where(MenuItem.restaurant_id == restaurant.id, MenuItem.name.in_(item_names))
    )
    menu_map = {m.name.lower(): m.id for m in menu_r.scalars().all()}

    # Build items
    subtotal = parsed['subtotal'] or sum(i['price'] * i['quantity'] for i in parsed['items'])
    gst, total = _calc(subtotal, restaurant.gst_rate or 5.0)

    order_items = [
        OrderItem(
            name=i['name'],
            price=i['price'],
            quantity=i['quantity'],
            total=round(i['price'] * i['quantity'], 2),
            menu_item_id=menu_map.get(i['name'].lower()),
        )
        for i in parsed['items']
    ]

    order = Order(
        restaurant_id=restaurant.id,
        order_number="",  # assigned after flush once id is known
        order_type='delivery',
        platform='zomato',
        platform_order_id=parsed['platform_order_id'],
        customer_name=parsed['customer_name'],
        customer_phone=parsed['customer_phone'],
        notes=parsed['notes'],
        subtotal=round(subtotal, 2),
        gst_amount=gst,
        discount_amount=0,
        discount_percent=0,
        total_amount=total,
        payment_method=parsed['payment_method'],
        payment_status='paid' if parsed['payment_method'] == 'online' else 'unpaid',
        status='kot_sent',  # auto-accept Zomato orders
        items=order_items,
    )
    db.add(order)
    await db.flush()
    order.order_number = f"BB-{date.today().strftime('%Y%m%d')}-{order.id}"
    await db.refresh(order, ["items"])

    # Deduct inventory — same as when KOT is sent manually
    from app.routers.recipes import deduct_inventory_for_order
    await deduct_inventory_for_order(order.items, restaurant.id, order.id, db)

    await db.commit()

    return {"status": "success", "order_number": order.order_number}


# ── SWIGGY ────────────────────────────────────────────────────────────────────
def _verify_swiggy_signature(payload: bytes, signature: str, secret: Optional[str]) -> bool:
    """Verify Swiggy webhook signature. Only bypasses when secret is None (not configured at all)."""
    if secret is None:
        return True  # dev mode — secret not yet configured
    if not secret:
        return False  # empty string = misconfigured, fail closed
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def _parse_swiggy_order(data: dict) -> dict:
    """
    Translate Swiggy's order format into BillByte's format.
    Swiggy webhook docs: https://partner.swiggy.com/docs
    """
    order_data = data.get('order_details', data)
    items = []
    for item in order_data.get('order_items', []):
        items.append({
            'name':     item.get('item_name', 'Unknown Item'),
            'price':    float(item.get('item_price', 0)) / 100,  # Swiggy sends paise
            'quantity': int(item.get('quantity', 1)),
        })

    customer = order_data.get('customer_details', {})
    address  = order_data.get('delivery_address', {})
    address_str = address.get('formatted_address', '')

    return {
        'platform':           'swiggy',
        'platform_order_id':  str(order_data.get('order_id', '')),
        'customer_name':      customer.get('name', ''),
        'customer_phone':     customer.get('mobile', ''),
        'delivery_address':   address_str,
        'items':              items,
        'payment_method':     'online' if order_data.get('payment_method', '').lower() == 'prepaid' else 'cash',
        'subtotal':           float(order_data.get('order_total', 0)) / 100,
        'notes':              f"Swiggy Order · Delivery to: {address_str}",
    }


@router.post("/swiggy/{restaurant_id}")
async def swiggy_webhook(
    restaurant_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_swiggy_signature: Optional[str] = Header(None),
):
    payload = await request.body()

    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "Invalid JSON payload")

    event = data.get('event_type', data.get('event', 'order.placed'))

    if event not in ('order.placed', 'new_order', 'ORDER_PLACED'):
        return {"status": "ignored", "event": event}

    restaurant = await _get_restaurant_by_id(restaurant_id, db)
    dev_mode = restaurant and restaurant.swiggy_secret is None
    if not restaurant or (not restaurant.swiggy_enabled and not dev_mode):
        raise HTTPException(404, "Restaurant not found or Swiggy not enabled")

    if not _verify_swiggy_signature(payload, x_swiggy_signature or '', restaurant.swiggy_secret):
        raise HTTPException(401, "Invalid Swiggy signature")

    parsed = _parse_swiggy_order(data)

    # Idempotency check
    if parsed['platform_order_id']:
        dup = await db.execute(
            select(Order).where(
                Order.platform_order_id == parsed['platform_order_id'],
                Order.platform == 'swiggy',
            )
        )
        if dup.scalar_one_or_none():
            return {"status": "duplicate", "message": "Order already exists"}

    # Resolve menu_item_ids by name so inventory deduction can work
    item_names = [i['name'] for i in parsed['items']]
    menu_r = await db.execute(
        select(MenuItem).where(MenuItem.restaurant_id == restaurant.id, MenuItem.name.in_(item_names))
    )
    menu_map = {m.name.lower(): m.id for m in menu_r.scalars().all()}

    subtotal = parsed['subtotal'] or sum(i['price'] * i['quantity'] for i in parsed['items'])
    gst, total = _calc(subtotal, restaurant.gst_rate or 5.0)

    order_items = [
        OrderItem(
            name=i['name'],
            price=i['price'],
            quantity=i['quantity'],
            total=round(i['price'] * i['quantity'], 2),
            menu_item_id=menu_map.get(i['name'].lower()),
        )
        for i in parsed['items']
    ]

    order = Order(
        restaurant_id=restaurant.id,
        order_number="",  # assigned after flush once id is known
        order_type='delivery',
        platform='swiggy',
        platform_order_id=parsed['platform_order_id'],
        customer_name=parsed['customer_name'],
        customer_phone=parsed['customer_phone'],
        notes=parsed['notes'],
        subtotal=round(subtotal, 2),
        gst_amount=gst,
        discount_amount=0,
        discount_percent=0,
        total_amount=total,
        payment_method=parsed['payment_method'],
        payment_status='paid' if parsed['payment_method'] == 'online' else 'unpaid',
        status='kot_sent',  # auto-accept Swiggy orders
        items=order_items,
    )
    db.add(order)
    await db.flush()
    order.order_number = f"BB-{date.today().strftime('%Y%m%d')}-{order.id}"
    await db.refresh(order, ["items"])

    # Deduct inventory — same as when KOT is sent manually
    from app.routers.recipes import deduct_inventory_for_order
    await deduct_inventory_for_order(order.items, restaurant.id, order.id, db)

    await db.commit()

    return {"status": "success", "order_number": order.order_number}


# ── TEST ENDPOINT (remove in production) ─────────────────────────────────────
@router.post("/test")
async def test_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Test endpoint to simulate an incoming order.
    POST /api/webhooks/test with body: {"platform": "zomato", "customer_name": "Test"}
    """
    data = await request.json()
    return {"status": "test received", "data": data}