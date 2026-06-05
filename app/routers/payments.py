from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import httpx
import uuid

from app.db.session import get_db
from app.models.user import User, Restaurant
from app.models.order import Order
from app.models.payment import PaymentTransaction
from app.core.security import get_current_user

router = APIRouter(prefix="/payments", tags=["Payments"])

PINELABS_BASE = "https://www.plutuscloud.in"
PAYMENT_MODE  = {"card": 1, "upi": 3}


def _require_credentials(rest: Restaurant):
    if not rest.pinelabs_enabled:
        raise HTTPException(400, "Pine Labs payments not enabled. Enable it in Settings → Integrations.")
    if not (rest.pinelabs_merchant_id and rest.pinelabs_terminal_id and rest.pinelabs_security_token):
        raise HTTPException(400, "Pine Labs credentials incomplete. Add Merchant ID, Terminal ID, and Security Token in Settings.")
    return rest.pinelabs_merchant_id, rest.pinelabs_terminal_id, rest.pinelabs_security_token


class InitiateRequest(BaseModel):
    order_id: int
    amount: float
    payment_mode: str  # "card" | "upi"


@router.post("/initiate")
async def initiate_payment(
    body: InitiateRequest,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Send payment request to Pine Labs terminal."""
    if body.payment_mode not in ("card", "upi"):
        raise HTTPException(400, "payment_mode must be 'card' or 'upi'")

    rest = await db.get(Restaurant, u.restaurant_id)
    merchant_id, terminal_id, security_token = _require_credentials(rest)

    # Validate order
    r = await db.execute(select(Order).where(Order.id == body.order_id, Order.restaurant_id == u.restaurant_id))
    order = r.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")
    if order.payment_status == "paid":
        raise HTTPException(400, "Order is already paid")

    # Prevent duplicate pending transactions
    dup = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.order_id == body.order_id,
            PaymentTransaction.status == "pending",
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(409, "A payment is already in progress for this order. Cancel it first.")

    txn_number = f"BB{order.order_number.replace('-','')}{uuid.uuid4().hex[:6].upper()}"
    amount_paise = int(round(body.amount * 100))

    payload = {
        "MerchantID": merchant_id,
        "SecurityToken": security_token,
        "TerminalID": terminal_id,
        "TransactionNumber": txn_number,
        "BillNumber": order.order_number,
        "Amount": amount_paise,
        "IMEI": "",
        "PaymentMode": PAYMENT_MODE[body.payment_mode],
        "TransactionType": 4001,
        "AutoCancelDuration": 90,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{PINELABS_BASE}/api/terminal/debitcard", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(408, "Terminal did not respond. Check the terminal is on and connected.")
    except Exception as e:
        raise HTTPException(502, f"Pine Labs error: {str(e)}")

    if data.get("ResponseCode") != 0:
        raise HTTPException(400, data.get("ResponseMessage", "Pine Labs rejected the request"))

    pinelabs_ref_id = str(data.get("PlutusTransactionReferenceID", ""))

    txn = PaymentTransaction(
        restaurant_id=u.restaurant_id,
        order_id=body.order_id,
        txn_number=txn_number,
        pinelabs_ref_id=pinelabs_ref_id,
        amount=body.amount,
        payment_mode=body.payment_mode,
        status="pending",
        raw_response=data,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)

    return {
        "txn_number": txn_number,
        "pinelabs_ref_id": pinelabs_ref_id,
        "status": "pending",
    }


@router.get("/{txn_number}/status")
async def poll_status(
    txn_number: str,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Poll Pine Labs for the latest transaction status."""
    r = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.txn_number == txn_number,
            PaymentTransaction.restaurant_id == u.restaurant_id,
        )
    )
    txn = r.scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")

    # Already resolved — return cached
    if txn.status in ("success", "failed", "cancelled", "timeout"):
        return _txn_out(txn)

    rest = await db.get(Restaurant, u.restaurant_id)
    merchant_id, _, security_token = _require_credentials(rest)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{PINELABS_BASE}/api/terminal/gettransactionstatus",
                json={
                    "MerchantID": merchant_id,
                    "SecurityToken": security_token,
                    "PlutusTransactionReferenceID": txn.pinelabs_ref_id,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        # Network issue — keep pending, frontend retries
        return _txn_out(txn)

    raw_status = (data.get("TransactionStatus") or "").upper()
    code = data.get("ResponseCode")
    approval = data.get("ApprovalCode")

    if raw_status == "SUCCESS" or (code == 0 and approval):
        new_status = "success"
    elif raw_status in ("FAILED", "FAILURE", "DECLINED"):
        new_status = "failed"
    elif raw_status in ("CANCELLED", "CANCELED"):
        new_status = "cancelled"
    else:
        new_status = "pending"

    txn.status = new_status
    txn.approval_code = data.get("ApprovalCode")
    txn.card_number = data.get("CardNumber")
    txn.card_type = data.get("CardType")
    txn.response_message = data.get("ResponseMessage")
    txn.raw_response = data

    # Mark order paid on success
    if new_status == "success":
        from app.routers.orders import _calc
        order = await db.get(Order, txn.order_id)
        if order and order.payment_status != "paid":
            disc, gst, total = _calc(order.subtotal, order.gst_rate, order.discount_percent or 0)
            order.payment_method  = txn.payment_mode
            order.payment_status  = "paid"
            order.status          = "paid"
            order.gst_amount      = gst
            order.discount_amount = disc
            order.total_amount    = total

    await db.commit()
    return _txn_out(txn)


@router.post("/{txn_number}/cancel")
async def cancel_payment(
    txn_number: str,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    """Cancel a pending Pine Labs transaction."""
    r = await db.execute(
        select(PaymentTransaction).where(
            PaymentTransaction.txn_number == txn_number,
            PaymentTransaction.restaurant_id == u.restaurant_id,
        )
    )
    txn = r.scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")
    if txn.status != "pending":
        return {"status": txn.status, "message": f"Already {txn.status}"}

    rest = await db.get(Restaurant, u.restaurant_id)
    merchant_id, _, security_token = _require_credentials(rest)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{PINELABS_BASE}/api/terminal/cancel",
                json={
                    "MerchantID": merchant_id,
                    "SecurityToken": security_token,
                    "PlutusTransactionReferenceID": txn.pinelabs_ref_id,
                },
            )
            data = resp.json()
    except Exception as e:
        data = {"ResponseMessage": f"Cancel request failed: {e}"}

    txn.status = "cancelled"
    txn.response_message = data.get("ResponseMessage", "Cancelled by cashier")
    txn.raw_response = data
    await db.commit()

    return {"status": "cancelled", "message": "Payment cancelled"}


def _txn_out(txn: PaymentTransaction) -> dict:
    return {
        "txn_number": txn.txn_number,
        "status": txn.status,
        "payment_mode": txn.payment_mode,
        "amount": txn.amount,
        "approval_code": txn.approval_code,
        "card_number": txn.card_number,
        "card_type": txn.card_type,
        "response_message": txn.response_message,
    }
