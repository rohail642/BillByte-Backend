from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date


# ── Supplier ──────────────────────────────────────────────────────────────────
class SupplierCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

class SupplierOut(BaseModel):
    id: int
    name: str
    phone: Optional[str]
    email: Optional[str]
    address: Optional[str]
    notes: Optional[str]
    class Config: from_attributes = True


# ── Inventory Item ────────────────────────────────────────────────────────────
class InventoryItemCreate(BaseModel):
    name: str
    quantity: float = 0.0
    unit: str
    min_quantity: float = 0.0
    cost_per_unit: float = 0.0
    category: Optional[str] = None
    supplier_id: Optional[int] = None
    expiry_date: Optional[date] = None

class InventoryItemUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    min_quantity: Optional[float] = None
    cost_per_unit: Optional[float] = None
    category: Optional[str] = None
    supplier_id: Optional[int] = None
    expiry_date: Optional[date] = None

class RestockRequest(BaseModel):
    quantity_to_add: float
    notes: Optional[str] = None

class ManualUsageRequest(BaseModel):
    quantity_used: float
    reason: str = "manual"
    notes: Optional[str] = None

class InventoryItemOut(BaseModel):
    id: int
    name: str
    quantity: float
    unit: str
    min_quantity: float
    cost_per_unit: float
    category: Optional[str]
    supplier_id: Optional[int]
    expiry_date: Optional[date]
    is_low_stock: bool
    is_expiring_soon: bool
    is_expired: bool
    stock_value: float
    updated_at: datetime
    class Config: from_attributes = True


# ── Usage Log ─────────────────────────────────────────────────────────────────
class UsageLogOut(BaseModel):
    id: int
    item_id: int
    quantity_used: float
    reason: str
    notes: Optional[str]
    created_at: datetime
    class Config: from_attributes = True


# ── Purchase Order ────────────────────────────────────────────────────────────
class PurchaseOrderCreate(BaseModel):
    supplier_name: str
    supplier_id: Optional[int] = None
    items_description: str
    total_amount: float = 0.0
    notes: Optional[str] = None
    expected_delivery: Optional[date] = None

class PurchaseOrderUpdate(BaseModel):
    status: str

class PurchaseOrderOut(BaseModel):
    id: int
    po_number: str
    supplier_name: str
    supplier_id: Optional[int]
    items_description: str
    total_amount: float
    status: str
    notes: Optional[str]
    expected_delivery: Optional[date]
    created_at: datetime
    class Config: from_attributes = True


# ── Alerts summary ────────────────────────────────────────────────────────────
class InventoryAlertOut(BaseModel):
    low_stock_count: int
    expiring_soon_count: int
    expired_count: int
    low_stock_items: list[InventoryItemOut]
    expiring_items: list[InventoryItemOut]
    expired_items: list[InventoryItemOut]