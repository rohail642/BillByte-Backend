from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from datetime import date, timedelta
import random

from app.db.session import get_db
from app.models.inventory import InventoryItem, PurchaseOrder, InventoryUsageLog, Supplier
from app.models.user import User
from app.schemas.inventory import (
    InventoryItemCreate, InventoryItemUpdate, InventoryItemOut,
    RestockRequest, ManualUsageRequest, UsageLogOut,
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut,
    SupplierCreate, SupplierUpdate, SupplierOut,
    InventoryAlertOut,
)
from app.core.security import get_current_user

router = APIRouter(prefix="/inventory", tags=["Inventory"])


# ── SUPPLIERS ─────────────────────────────────────────────────────────────────
@router.get("/suppliers", response_model=List[SupplierOut])
async def list_suppliers(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Supplier).where(Supplier.restaurant_id == u.restaurant_id, Supplier.is_active == True).order_by(Supplier.name))
    return r.scalars().all()

@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def create_supplier(body: SupplierCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    s = Supplier(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(s); await db.flush(); return s

@router.patch("/suppliers/{sid}", response_model=SupplierOut)
async def update_supplier(sid: int, body: SupplierUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Supplier).where(Supplier.id == sid, Supplier.restaurant_id == u.restaurant_id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404, "Supplier not found")
    for k, v in body.model_dump(exclude_unset=True).items(): setattr(s, k, v)
    return s

@router.delete("/suppliers/{sid}", status_code=204)
async def delete_supplier(sid: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(Supplier).where(Supplier.id == sid, Supplier.restaurant_id == u.restaurant_id))
    s = r.scalar_one_or_none()
    if not s: raise HTTPException(404, "Supplier not found")
    s.is_active = False


# ── INVENTORY ITEMS ───────────────────────────────────────────────────────────
@router.get("/items", response_model=List[InventoryItemOut])
async def list_items(low_stock_only: bool = False, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.restaurant_id == u.restaurant_id, InventoryItem.is_active == True).order_by(InventoryItem.name))
    items = r.scalars().all()
    if low_stock_only:
        items = [i for i in items if i.is_low_stock]
    return items

@router.post("/items", response_model=InventoryItemOut, status_code=201)
async def create_item(body: InventoryItemCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    item = InventoryItem(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(item); await db.flush(); return item

@router.patch("/items/{item_id}", response_model=InventoryItemOut)
async def update_item(item_id: int, body: InventoryItemUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    for k, v in body.model_dump(exclude_unset=True).items(): setattr(item, k, v)
    return item

@router.post("/items/{item_id}/restock", response_model=InventoryItemOut)
async def restock(item_id: int, body: RestockRequest, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    item.quantity = round(item.quantity + body.quantity_to_add, 3)
    # Log the restock as negative usage (addition)
    log = InventoryUsageLog(restaurant_id=u.restaurant_id, item_id=item.id,
                            quantity_used=-body.quantity_to_add, reason="restock", notes=body.notes)
    db.add(log)
    return item

@router.post("/items/{item_id}/deduct", response_model=InventoryItemOut)
async def manual_deduct(item_id: int, body: ManualUsageRequest, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    item.quantity = max(0, round(item.quantity - body.quantity_used, 3))
    log = InventoryUsageLog(restaurant_id=u.restaurant_id, item_id=item.id,
                            quantity_used=body.quantity_used, reason=body.reason, notes=body.notes)
    db.add(log)
    return item

@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.id == item_id, InventoryItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    item.is_active = False

@router.get("/items/{item_id}/usage", response_model=List[UsageLogOut])
async def get_usage_history(item_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(InventoryUsageLog)
        .where(InventoryUsageLog.item_id == item_id, InventoryUsageLog.restaurant_id == u.restaurant_id)
        .order_by(InventoryUsageLog.created_at.desc()).limit(50)
    )
    return r.scalars().all()


# ── ALERTS ────────────────────────────────────────────────────────────────────
@router.get("/alerts", response_model=InventoryAlertOut)
async def get_alerts(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(InventoryItem).where(InventoryItem.restaurant_id == u.restaurant_id, InventoryItem.is_active == True))
    all_items = r.scalars().all()
    low  = [i for i in all_items if i.is_low_stock]
    exp  = [i for i in all_items if i.is_expiring_soon]
    expd = [i for i in all_items if i.is_expired]
    return InventoryAlertOut(
        low_stock_count=len(low), expiring_soon_count=len(exp), expired_count=len(expd),
        low_stock_items=low, expiring_items=exp, expired_items=expd,
    )


# ── USAGE HISTORY (daily) ─────────────────────────────────────────────────────
@router.get("/usage-history")
async def usage_history(days: int = 7, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    from datetime import datetime, timedelta
    start = datetime.utcnow() - timedelta(days=days)
    r = await db.execute(
        select(
            func.date(InventoryUsageLog.created_at).label("day"),
            InventoryItem.name,
            func.sum(InventoryUsageLog.quantity_used).label("total_used"),
        )
        .join(InventoryItem, InventoryItem.id == InventoryUsageLog.item_id)
        .where(InventoryUsageLog.restaurant_id == u.restaurant_id,
               InventoryUsageLog.created_at >= start,
               InventoryUsageLog.quantity_used > 0)
        .group_by(func.date(InventoryUsageLog.created_at), InventoryItem.name)
        .order_by(func.date(InventoryUsageLog.created_at).desc())
    )
    return [{"date": str(row.day), "item": row.name, "used": round(row.total_used, 3)} for row in r]


# ── PURCHASE ORDERS ───────────────────────────────────────────────────────────
@router.get("/purchase-orders", response_model=List[PurchaseOrderOut])
async def list_pos(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(PurchaseOrder).where(PurchaseOrder.restaurant_id == u.restaurant_id).order_by(PurchaseOrder.created_at.desc()))
    return r.scalars().all()

@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
async def create_po(body: PurchaseOrderCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    po_num = f"PO-{date.today().strftime('%Y%m%d')}-{random.randint(100,999)}"
    po = PurchaseOrder(restaurant_id=u.restaurant_id, po_number=po_num, **body.model_dump())
    db.add(po); await db.flush(); return po

@router.patch("/purchase-orders/{po_id}", response_model=PurchaseOrderOut)
async def update_po(po_id: int, body: PurchaseOrderUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id, PurchaseOrder.restaurant_id == u.restaurant_id))
    po = r.scalar_one_or_none()
    if not po: raise HTTPException(404, "Purchase order not found")
    po.status = body.status
    return po