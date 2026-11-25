"""QR table self-ordering.

Guests scan a per-table QR → browse the menu → place an order request. Staff
accept it (→ normal Order + KOT, exactly like a waiter sending one) or reject it.
No external services: UPI payment is a plain `upi://pay` deep link.

public_router  — unauthenticated, rate-limited, scoped by a signed table token
staff_router   — authenticated POS endpoints (queue, accept/reject, settings, QR list)
"""
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from slowapi.util import get_remote_address
from app.core.limiter import limiter
from app.core.sanitize import SafeOptStr
from app.core.security import get_current_user, require_owner
from app.core.table_token import (
    make_table_token, parse_table_token, verify_table_token, restaurant_tables,
)
from app.db.session import get_db
from app.models.menu import MenuItem, MenuCategory
from app.models.order import Order
from app.models.table_request import TableOrderRequest, TableServiceCall
from app.models.user import User, Restaurant
from app.schemas.order import OrderCreate, OrderItemCreate, OrderStatusUpdate

public_router = APIRouter(prefix="/public/table", tags=["QR Ordering (public)"])
staff_router = APIRouter(prefix="/table-ordering", tags=["QR Ordering"])

MAX_LINES = 30
MAX_QTY_PER_LINE = 20
MAX_PENDING_PER_TABLE = 3
CALL_TTL = timedelta(hours=3)        # an unanswered call older than this is stale
PAID_SHOW = timedelta(hours=3)       # a phone that saw the open bill can see it turn "Paid" this long
_VPA_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,64}$")


# ── helpers ────────────────────────────────────────────────────────────────────

async def _resolve_table(token: str, db: AsyncSession) -> tuple[Restaurant, str]:
    """Verify a table token; 404 for anything invalid so tokens can't be probed."""
    parsed = parse_table_token(token)
    if not parsed:
        raise HTTPException(404, "This QR code is not valid")
    rid, table, sig = parsed
    rest = await db.get(Restaurant, rid)
    if (
        not rest
        or not verify_table_token(rid, table, sig, rest.qr_token_version or 0)
        or table not in restaurant_tables(rest)
    ):
        raise HTTPException(404, "This QR code is not valid")
    if not rest.is_active or not rest.qr_ordering_enabled:
        raise HTTPException(403, "Ordering from the table is not available right now. Please ask a staff member.")
    return rest, table


def _upi_link(rest: Restaurant, amount: float, note: str) -> Optional[str]:
    if not rest.upi_vpa:
        return None
    from urllib.parse import quote
    return (
        f"upi://pay?pa={quote(rest.upi_vpa)}&pn={quote(rest.name or 'Restaurant')}"
        f"&am={amount:.2f}&cu=INR&tn={quote(note)}"
    )


def _bill_out(order: Order, rest: Restaurant, table: str) -> dict:
    return {
        "order_number": order.order_number,
        "items": [
            {"name": i.name, "quantity": i.quantity, "total": i.total}
            for i in order.items if not i.cancelled_at
        ],
        "subtotal": order.subtotal, "gst_amount": order.gst_amount,
        "discount_amount": order.discount_amount, "total_amount": order.total_amount,
        "payment_status": order.payment_status,
        "upi_link": None if order.payment_status == "paid"
        else _upi_link(rest, order.total_amount, f"{order.order_number} Table {table}"),
    }


def _table_day_start() -> datetime:
    # Same window create_order uses to merge KOTs into a running table bill.
    return datetime.combine(date.today(), time.min)


async def close_bill_calls(db: AsyncSession, restaurant_id: int, table: Optional[str], u: User) -> None:
    """Once a table has paid, its "Ask for bill" call is answered."""
    if not table:
        return
    calls = (await db.execute(
        select(TableServiceCall).where(
            TableServiceCall.restaurant_id == restaurant_id, TableServiceCall.table_number == str(table),
            TableServiceCall.kind == "bill", TableServiceCall.status == "open")
    )).scalars().all()
    for c in calls:
        c.status = "done"
        c.handled_by = u.id or None
        c.handled_at = datetime.now(timezone.utc)


def _call_out(c: TableServiceCall) -> dict:
    return {"id": c.id, "table_number": c.table_number, "kind": c.kind, "pay_by": c.pay_by,
            "status": c.status, "created_at": c.created_at}


def _request_out(req: TableOrderRequest) -> dict:
    return {
        "id": req.id,
        "table_number": req.table_number,
        "items": req.items,
        "subtotal": req.subtotal,
        "guest_name": req.guest_name,
        "notes": req.notes,
        "status": req.status,
        "reject_reason": req.reject_reason,
        "order_id": req.order_id,
        "kot_number": req.kot_number,
        "created_at": req.created_at,
        "handled_at": req.handled_at,
    }


async def _accept(req: TableOrderRequest, db: AsyncSession, u: User) -> Order:
    """Turn a guest request into a real order/KOT using the same code path the
    waiter app uses (create_order → status kot_sent), so stock deduction, KOT
    numbering and table merging behave identically."""
    from app.routers.orders import create_order, update_status

    body = OrderCreate(
        order_type="dine_in",
        table_number=req.table_number,
        items=[OrderItemCreate(**{k: it.get(k) for k in ("menu_item_id", "name", "price", "quantity", "notes")})
               for it in req.items],
        notes=(f"QR order{' — ' + req.guest_name if req.guest_name else ''}"
               f"{': ' + req.notes if req.notes else ''}"),
        platform="qr",
    )
    order = await create_order(body, db, u)
    if order.status == "pending":  # brand-new order (not merged into a running table)
        order = await update_status(order.id, OrderStatusUpdate(status="kot_sent"), db, u)

    req.status = "accepted"
    req.order_id = order.id
    req.kot_number = max((i.kot_number for i in order.items), default=1)
    req.handled_by = u.id if u.id else None
    req.handled_at = datetime.now(timezone.utc)
    await db.flush()
    return order


# ── public (guest) endpoints ───────────────────────────────────────────────────

def _table_key(request: Request) -> str:
    """Rate-limit per IP *and* table. Guests at one restaurant share its Wi-Fi IP
    (and strangers share carrier CGNAT IPs), so a plain per-IP limit would make
    a busy restaurant's guests block each other. Spam is also capped by
    MAX_PENDING_PER_TABLE and staff confirmation."""
    return f"{get_remote_address(request)}|{request.path_params.get('token', '')[:64]}"


@public_router.get("/{token}/menu")
@limiter.limit("60/minute", key_func=_table_key)
async def guest_menu(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    rest, table = await _resolve_table(token, db)
    cats = (await db.execute(
        select(MenuCategory)
        .where(MenuCategory.restaurant_id == rest.id, MenuCategory.is_active == True)  # noqa: E712
        .order_by(MenuCategory.sort_order, MenuCategory.name)
    )).scalars().all()
    items = (await db.execute(
        select(MenuItem)
        .where(
            MenuItem.restaurant_id == rest.id,
            MenuItem.is_active == True,  # noqa: E712
            MenuItem.is_available == True,  # noqa: E712
            (MenuItem.stock_qty.is_(None)) | (MenuItem.stock_qty > 0),
        )
        .order_by(MenuItem.sort_order, MenuItem.name)
    )).scalars().all()
    cat_ids = {c.id for c in cats}
    return {
        "restaurant_name": rest.name,
        "table_number": table,
        "gst_rate": rest.gst_rate,
        "upi_enabled": bool(rest.upi_vpa),
        "auto_accept": bool(rest.qr_auto_accept),
        "categories": [{"id": c.id, "name": c.name} for c in cats],
        "items": [
            {
                "id": i.id, "name": i.name, "description": i.description,
                "price": i.price, "emoji": i.emoji, "food_type": i.food_type,
                "category_id": i.category_id if i.category_id in cat_ids else None,
            }
            for i in items
        ],
    }


class GuestItem(BaseModel):
    menu_item_id: int
    quantity: int = Field(ge=1, le=MAX_QTY_PER_LINE)
    notes: SafeOptStr = Field(default=None, max_length=200)


class GuestOrderIn(BaseModel):
    items: List[GuestItem] = Field(min_length=1, max_length=MAX_LINES)
    guest_name: SafeOptStr = Field(default=None, max_length=60)
    notes: SafeOptStr = Field(default=None, max_length=300)


@public_router.post("/{token}/orders", status_code=201)
@limiter.limit("12/minute", key_func=_table_key)
async def guest_place_order(request: Request, token: str, body: GuestOrderIn, db: AsyncSession = Depends(get_db)):
    rest, table = await _resolve_table(token, db)

    pending = (await db.execute(
        select(func.count(TableOrderRequest.id)).where(
            TableOrderRequest.restaurant_id == rest.id,
            TableOrderRequest.table_number == table,
            TableOrderRequest.status == "pending",
        )
    )).scalar()
    if pending >= MAX_PENDING_PER_TABLE:
        raise HTTPException(429, "This table already has orders waiting for staff. Please wait a moment.")

    # Prices and names come from the DB — never from the guest's browser.
    ids = {it.menu_item_id for it in body.items}
    menu = {m.id: m for m in (await db.execute(
        select(MenuItem).where(MenuItem.restaurant_id == rest.id, MenuItem.id.in_(ids))
    )).scalars().all()}

    lines, subtotal = [], 0.0
    for it in body.items:
        m = menu.get(it.menu_item_id)
        if not m or not m.is_active or not m.is_available or (m.stock_qty is not None and m.stock_qty < it.quantity):
            name = m.name if m else "An item"
            raise HTTPException(409, f"{name} is no longer available. Please update your order.")
        lines.append({
            "menu_item_id": m.id, "name": m.name, "price": m.price,
            "quantity": it.quantity, "notes": it.notes or None,
        })
        subtotal += m.price * it.quantity

    req = TableOrderRequest(
        restaurant_id=rest.id, table_number=table, items=lines,
        subtotal=round(subtotal, 2), guest_name=body.guest_name or None,
        notes=body.notes or None, status="pending",
        access_key=secrets.token_urlsafe(24),
    )
    db.add(req)
    await db.flush()

    if rest.qr_auto_accept:
        # Act as the restaurant owner account for order attribution.
        owner = (await db.execute(
            select(User).where(User.restaurant_id == rest.id, User.role == "owner").limit(1)
        )).scalar_one_or_none()
        if owner:
            await _accept(req, db, owner)

    return {"request_id": req.id, "access_key": req.access_key, "status": req.status}


@public_router.get("/{token}/orders/{request_id}")
@limiter.limit("120/minute", key_func=_table_key)
async def guest_order_status(request: Request, token: str, request_id: int, key: str, db: AsyncSession = Depends(get_db)):
    parsed = parse_table_token(token)
    if not parsed:
        raise HTTPException(404, "Not found")
    rid, table, _ = parsed
    req = await db.get(TableOrderRequest, request_id)
    if (not req or req.restaurant_id != rid or req.table_number != table
            or not secrets.compare_digest(req.access_key, key)):
        raise HTTPException(404, "Not found")

    out = {
        "id": req.id, "status": req.status, "reject_reason": req.reject_reason,
        "items": req.items, "subtotal": req.subtotal, "kitchen_status": None, "bill": None,
    }
    if req.status == "accepted" and req.order_id:
        order = (await db.execute(
            select(Order).where(Order.id == req.order_id).options(selectinload(Order.items))
        )).scalar_one_or_none()
        if order:
            kots = order.kot_statuses or {}
            out["kitchen_status"] = (
                "paid" if order.payment_status == "paid"
                else "cancelled" if order.status == "cancelled"
                else kots.get(str(req.kot_number), order.status)
            )
            rest = await db.get(Restaurant, rid)
            out["bill"] = _bill_out(order, rest, table)
    return out


@public_router.get("/{token}/table")
@limiter.limit("120/minute", key_func=_table_key)
async def guest_table_view(request: Request, token: str, seen: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """What everyone at this table can see: the running bill (all KOTs, whoever
    ordered), orders still waiting for staff, and open waiter/bill calls. Lets a
    second guest see what's already ordered instead of ordering it twice."""
    rest, table = await _resolve_table(token, db)
    base = (Order.restaurant_id == rest.id, Order.table_number == table,
            Order.order_type == "dine_in", Order.status != "cancelled")
    order = (await db.execute(
        select(Order).where(*base, Order.payment_status == "unpaid", Order.created_at >= _table_day_start())
        .options(selectinload(Order.items)).order_by(Order.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not order and seen:
        # Just paid? Only a phone that already saw this bill open (it sends the
        # order number back) gets to see it as "Paid" — the next group sitting
        # at the table must not see the previous group's bill.
        order = (await db.execute(
            select(Order).where(*base, Order.payment_status == "paid", Order.order_number == seen[:50],
                                Order.updated_at >= datetime.now(timezone.utc) - PAID_SHOW)
            .options(selectinload(Order.items)).order_by(Order.updated_at.desc()).limit(1)
        )).scalar_one_or_none()

    pending = (await db.execute(
        select(TableOrderRequest).where(
            TableOrderRequest.restaurant_id == rest.id, TableOrderRequest.table_number == table,
            TableOrderRequest.status == "pending")
        .order_by(TableOrderRequest.created_at)
    )).scalars().all()
    calls = (await db.execute(
        select(TableServiceCall).where(
            TableServiceCall.restaurant_id == rest.id, TableServiceCall.table_number == table,
            TableServiceCall.status == "open",
            TableServiceCall.created_at >= datetime.now(timezone.utc) - CALL_TTL)
    )).scalars().all()
    return {
        "bill": _bill_out(order, rest, table) if order else None,
        "pending": [{"id": r.id, "guest_name": r.guest_name, "created_at": r.created_at,
                     "items": [{"name": i["name"], "quantity": i["quantity"]} for i in r.items]}
                    for r in pending],
        "calls": sorted({c.kind for c in calls}),
    }


class GuestCallIn(BaseModel):
    kind: Literal["waiter", "bill"]
    pay_by: Optional[Literal["cash", "upi", "card"]] = None


@public_router.post("/{token}/call")
@limiter.limit("10/minute", key_func=_table_key)
async def guest_call(request: Request, token: str, body: GuestCallIn, db: AsyncSession = Depends(get_db)):
    """Call waiter / ask for the bill. Repeated taps reuse the open call."""
    rest, table = await _resolve_table(token, db)
    existing = (await db.execute(
        select(TableServiceCall).where(
            TableServiceCall.restaurant_id == rest.id, TableServiceCall.table_number == table,
            TableServiceCall.kind == body.kind, TableServiceCall.status == "open",
            TableServiceCall.created_at >= datetime.now(timezone.utc) - CALL_TTL)
        .limit(1)
    )).scalar_one_or_none()
    if existing:
        if body.pay_by:
            existing.pay_by = body.pay_by
        return {"ok": True, "kind": body.kind, "already": True}
    db.add(TableServiceCall(restaurant_id=rest.id, table_number=table, kind=body.kind,
                            pay_by=body.pay_by if body.kind == "bill" else None, status="open"))
    await db.flush()
    return {"ok": True, "kind": body.kind, "already": False}


# ── staff endpoints ────────────────────────────────────────────────────────────

@staff_router.get("/requests")
async def list_requests(status: str = "pending", db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    q = select(TableOrderRequest).where(TableOrderRequest.restaurant_id == u.restaurant_id)
    if status != "all":
        q = q.where(TableOrderRequest.status == status)
    rows = (await db.execute(q.order_by(TableOrderRequest.created_at.desc()).limit(50))).scalars().all()
    return [_request_out(r) for r in rows]


async def _get_pending(request_id: int, db: AsyncSession, u: User) -> TableOrderRequest:
    req = (await db.execute(
        select(TableOrderRequest)
        .where(TableOrderRequest.id == request_id, TableOrderRequest.restaurant_id == u.restaurant_id)
        .with_for_update()
    )).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != "pending":
        raise HTTPException(409, f"Already {req.status}")
    return req


@staff_router.post("/requests/{request_id}/accept")
async def accept_request(request_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    req = await _get_pending(request_id, db, u)
    order = await _accept(req, db, u)
    return {"request": _request_out(req), "order_id": order.id, "order_number": order.order_number}


class RejectIn(BaseModel):
    reason: SafeOptStr = Field(default=None, max_length=200)


@staff_router.post("/requests/{request_id}/reject")
async def reject_request(request_id: int, body: RejectIn, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    req = await _get_pending(request_id, db, u)
    req.status = "rejected"
    req.reject_reason = body.reason or None
    req.handled_by = u.id
    req.handled_at = datetime.now(timezone.utc)
    return _request_out(req)


@staff_router.get("/calls")
async def list_calls(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    rows = (await db.execute(
        select(TableServiceCall).where(
            TableServiceCall.restaurant_id == u.restaurant_id, TableServiceCall.status == "open",
            TableServiceCall.created_at >= datetime.now(timezone.utc) - CALL_TTL)
        .order_by(TableServiceCall.created_at)
    )).scalars().all()
    return [_call_out(c) for c in rows]


@staff_router.post("/calls/{call_id}/done")
async def finish_call(call_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    c = (await db.execute(
        select(TableServiceCall).where(TableServiceCall.id == call_id,
                                       TableServiceCall.restaurant_id == u.restaurant_id)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Call not found")
    if c.status == "open":
        c.status = "done"
        c.handled_by = u.id or None
        c.handled_at = datetime.now(timezone.utc)
    return _call_out(c)


class QRSettingsIn(BaseModel):
    qr_ordering_enabled: Optional[bool] = None
    qr_auto_accept: Optional[bool] = None
    upi_vpa: Optional[str] = Field(default=None, max_length=100)


def _settings_out(rest: Restaurant) -> dict:
    return {
        "qr_ordering_enabled": bool(rest.qr_ordering_enabled),
        "qr_auto_accept": bool(rest.qr_auto_accept),
        "upi_vpa": rest.upi_vpa or "",
    }


@staff_router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    return _settings_out(await db.get(Restaurant, u.restaurant_id))


@staff_router.put("/settings")
async def put_settings(body: QRSettingsIn, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    rest = await db.get(Restaurant, u.restaurant_id)
    if body.qr_ordering_enabled is not None:
        rest.qr_ordering_enabled = body.qr_ordering_enabled
    if body.qr_auto_accept is not None:
        rest.qr_auto_accept = body.qr_auto_accept
    if body.upi_vpa is not None:
        vpa = body.upi_vpa.strip()
        if vpa and not _VPA_RE.match(vpa):
            raise HTTPException(422, "UPI ID looks invalid (expected something like name@okhdfcbank)")
        rest.upi_vpa = vpa or None
    return _settings_out(rest)


@staff_router.get("/tables")
async def table_qr_list(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Tokens for every table — the frontend turns them into printable QR codes."""
    rest = await db.get(Restaurant, u.restaurant_id)
    v = rest.qr_token_version or 0
    sec: dict[str, str] = {}
    for s_ in rest.table_sections or []:
        for t in s_.get("tables") or []:
            sec.setdefault(str(t), s_.get("name") or "")
    return {
        "restaurant_name": rest.name,
        "tables": [{"table_number": t, "token": make_table_token(rest.id, t, v), "section": sec.get(t)}
                   for t in restaurant_tables(rest)],
    }


@staff_router.post("/tables/regenerate")
async def regenerate_qr(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Invalidate every printed QR code (e.g. if a photo of one is circulating)."""
    rest = await db.get(Restaurant, u.restaurant_id)
    rest.qr_token_version = (rest.qr_token_version or 0) + 1
    return {"ok": True}
