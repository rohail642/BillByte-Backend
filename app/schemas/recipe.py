from pydantic import BaseModel
from typing import Optional


class RecipeIngredientCreate(BaseModel):
    inventory_item_id: int
    quantity: float
    unit: str


class RecipeIngredientOut(BaseModel):
    id: int
    inventory_item_id: int
    inventory_item_name: Optional[str] = None
    quantity: float
    unit: str

    class Config:
        from_attributes = True


class RecipeCreate(BaseModel):
    menu_item_id: int
    ingredients: list[RecipeIngredientCreate]


class RecipeOut(BaseModel):
    id: int
    menu_item_id: int
    menu_item_name: Optional[str] = None
    is_active: bool
    ingredients: list[RecipeIngredientOut]

    class Config:
        from_attributes = True


class RecipeUpdate(BaseModel):
    ingredients: list[RecipeIngredientCreate]