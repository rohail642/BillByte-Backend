from pydantic import BaseModel
from typing import Optional


class CategoryCreate(BaseModel):
    name: str
    sort_order: int = 0


class CategoryOut(BaseModel):
    id: int
    name: str
    sort_order: int
    is_active: bool

    class Config:
        from_attributes = True


class MenuItemCreate(BaseModel):
    name: str
    price: float
    category_id: Optional[int] = None
    description: Optional[str] = None
    emoji: str = "🍽️"
    food_type: str = "veg"
    is_active: bool = True
    sort_order: int = 0


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[float] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    emoji: Optional[str] = None
    food_type: Optional[str] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None


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
    sort_order: int

    class Config:
        from_attributes = True
