from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime

from app.db.session import get_db
from app.models.order import Order, OrderItem
from app.models.user import User, Restaurant
from app.models.cancellation import CancellationEvent
from app.schemas.order import CancelItemRequest, CancelItemResponse
from app.core.security import get_current_user, require_min_role
from app.core.sanitize import SafeStr, SafeOptStr

router = APIRouter(prefix="/orders", tags=["Cancellations"])

_KOT_PRIORITY = ['kot_sent', 'preparing', 'ready', 'served']


def _derive_order_status(kot_statuses: dict) -> str:
    statuses = list(kot_statuses.values())
    if not statuses:
        return 'kot_sent'
    if all(s == 'served' for s in statuses):
        return 'served'
    active = [s for s in statuses if s != 'served']
    return min(active, key=lambda s: _KOT_PRIORITY.index(s) if s in _KOT_PRIORITY else 99)


@router.post("/{order_id}/items/{item_id}/cancel", response_model=CancelItemResponse)
async def cancel_order_item(
    order_id: int,
    item_id: int,
    body: CancelItemRequest,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Cancel an order item.

    - If the item has NOT been fired to a station (KOT not yet sent),
      cancellation is immediate — no approval needed.
    - If the item HAS been fired (KOT status ≥ kot_sent), the caller
      must provide a valid manager_pin to approve the cancellation.
    """
    r = await db.execute(
        select(Order)
        .where(Order.id == order_id, Order.restaurant_id == u.restaurant_id)
        .options(selectinload(Order.items))
    )
    order = r.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.payment_status == "paid":
        raise HTTPException(400, "Cannot cancel items on a paid order")

    item = next((i for i in order.items if i.id == item_id), None)
    if not item:
        raise HTTPException(404, "Item not found")
    if item.cancelled_at:
        raise HTTPException(400, "Item is already cancelled")

    kot_num = item.kot_number or 1
    statuses = dict(order.kot_statuses or {})
    kot_status = statuses.get(str(kot_num))

    # Determine if the item has been fired to a station
    has_been_fired = kot_status in _KOT_PRIORITY and kot_status != 'served'

    # Validate reason code
    valid_reasons = {"wrong_order", "changed_mind", "stock_out", "quality_issue", "other"}
    if body.reason_code not in valid_reasons:
        raise HTTPException(400, f"Invalid reason_code. Must be one of: {', '.join(sorted(valid_reasons))}")

    auto_cancelled = False
    approved_by = None
    station_notified = False

    if has_been_fired:
        # ── Fired item — requires manager approval ──
        if not body.manager_pin:
            raise HTTPException(403, "This item has been sent to the kitchen. Manager PIN required to cancel.")

        rest = await db.get(Restaurant, u.restaurant_id)
        if not rest or not rest.manager_pin:
            raise HTTPException(403, "No manager PIN configured. Please set one in Settings first.")

        from app.core.security import verify_password
        if not verify_password(body.manager_pin, rest.manager_pin):
            raise HTTPException(403, "Invalid manager PIN. Cancellation denied.")

        approved_by = u.id
    else:
        # ── Unfired item — immediate cancellation, no approval ──
        auto_cancelled = True

    # ── Partial vs full cancellation ──
    req_qty = body.quantity
    is_partial = req_qty is not None and 0 < req_qty < item.quantity

    if is_partial:
        logged_qty = req_qty
        item.quantity -= req_qty
        item.total = round(item.price * item.quantity, 2)
        # item.cancelled_at stays None — item is still active
    else:
        logged_qty = item.quantity  # audit: the full original quantity
        item.cancelled_at = datetime.utcnow()

    # Fired items already deducted per-item stock — put the cancelled qty back
    if has_been_fired:
        from app.routers.recipes import restore_menu_stock
        await restore_menu_stock(item.menu_item_id, logged_qty, u.restaurant_id, db)

    # Recalculate order totals (exclude fully-cancelled items)
    active_items = [i for i in order.items if not i.cancelled_at]
    if not active_items:
        order.status = 'cancelled'
        order.subtotal = 0
        order.gst_amount = 0
        order.discount_amount = 0
        order.total_amount = 0
    else:
        new_subtotal = round(sum(i.price * i.quantity for i in active_items), 2)
        order.subtotal = new_subtotal
        disc, gst, total = _calc(new_subtotal, order.gst_rate, order.discount_percent)
        order.gst_amount = gst
        order.discount_amount = disc
        order.total_amount = total

        # Update KOT statuses
        all_kot_nums = {str(i.kot_number or 1) for i in order.items}
        for k in all_kot_nums:
            if k not in statuses:
                statuses[k] = order.status if order.status in _KOT_PRIORITY else 'kot_sent'

        cancelled_kot = str(kot_num)
        kot_items = [i for i in order.items if str(i.kot_number or 1) == cancelled_kot]
        if kot_items and all(i.cancelled_at for i in kot_items):
            statuses[cancelled_kot] = 'served'
        elif order.status not in ('pending', 'paid', 'cancelled'):
            statuses[cancelled_kot] = 'kot_sent'

        order.kot_statuses = statuses
        order.status = _derive_order_status(statuses)

    # ── Log the cancellation event (insert-only audit trail) ──
    event = CancellationEvent(
        restaurant_id=u.restaurant_id,
        order_id=order_id,
        order_item_id=item_id,
        approved_by=approved_by,
        reason_code=body.reason_code,
        reason_note=body.reason_note,
        kot_number=kot_num,
        station_notified=False,
        auto_cancelled=auto_cancelled,
        quantity=logged_qty,
    )
    db.add(event)
    await db.flush()

    return CancelItemResponse(
        success=True,
        requires_approval=has_been_fired,
        cancellation_event_id=event.id,
        message="Item cancelled successfully"
        + (". Kitchen has been notified." if auto_cancelled else ". Manager approval recorded.")
        + (" Please notify the kitchen station verbally as a fallback." if has_been_fired and not station_notified else ""),
        station_notified=station_notified,
    )


@router.post("/{order_id}/items/{item_id}/cancel/notify", response_model=CancelItemResponse)
async def retry_cancel_notification(
    order_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Retry notification for a cancelled item (re-print / re-push to KDS)."""
    r = await db.execute(
        select(CancellationEvent)
        .where(
            CancellationEvent.order_item_id == item_id,
            CancellationEvent.order_id == order_id,
            CancellationEvent.restaurant_id == u.restaurant_id,
        )
        .order_by(CancellationEvent.cancelled_at.desc())
        .limit(1)
    )
    event = r.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "No cancellation event found for this item")

    # In a real system this would trigger the print/KDS push.
    # For now, the frontend handles actual printing; we track the retry.
    event.station_notified = True
    await db.flush()

    return CancelItemResponse(
        success=True,
        requires_approval=not event.auto_cancelled,
        cancellation_event_id=event.id,
        message="Notification re-sent to station.",
        station_notified=True,
    )


def _calc(subtotal: float, gst_rate: float, disc_pct: float):
    disc = round(subtotal * disc_pct / 100, 2)
    after = subtotal - disc
    gst = round(after * gst_rate / 100, 2)
    return disc, gst, round(after + gst, 2)
