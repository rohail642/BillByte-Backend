from pydantic import BaseModel, Field
from typing import Optional

from app.core.sanitize import SafeStr, SafeOptStr


class CategoryCreate(BaseModel):
    name: SafeStr
    sort_order: int = 0


class CategoryOut(BaseModel):
    id: int
    name: str
    sort_order: int
    is_active: bool

    class Config:
        from_attributes = True


class MenuItemCreate(BaseModel):
    name: SafeStr
    price: float = Field(ge=0, le=1_000_000)
    category_id: Optional[int] = None
    description: SafeOptStr = None
    emoji: str = "🍽️"
    food_type: str = "veg"
    is_active: bool = True
    sort_order: int = 0
    stock_qty: Optional[int] = Field(default=None, ge=0, le=1_000_000)


class MenuItemUpdate(BaseModel):
    name: SafeOptStr = None
    price: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    category_id: Optional[int] = None
    description: SafeOptStr = None
    emoji: Optional[str] = None
    food_type: Optional[str] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    stock_qty: Optional[int] = Field(default=None, ge=0, le=1_000_000)  # explicit null = stop tracking


class MenuItemOut(BaseModel):
    id: int
    name: str
    price: float
    category_id: Optional[int]
    description: Optional[str]
    emoji: str
    food_type: str
    is_active: bool
    is_available: bool
    stock_qty: Optional[int]
    sort_order: int

    class Config:
        from_attributes = True
