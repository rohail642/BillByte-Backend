from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List

from app.db.session import get_db
from app.models.recipe import Recipe, RecipeIngredient
from app.models.menu import MenuItem
from app.models.inventory import InventoryItem, InventoryUsageLog
from app.models.user import User
from app.schemas.recipe import RecipeCreate, RecipeOut, RecipeUpdate
from app.core.security import get_current_user

router = APIRouter(prefix="/recipes", tags=["Recipes"])


def _to_out(recipe: Recipe, menu_item_name: str = None) -> dict:
    return {
        "id": recipe.id,
        "menu_item_id": recipe.menu_item_id,
        "menu_item_name": menu_item_name,
        "is_active": recipe.is_active,
        "ingredients": [
            {
                "id": ing.id,
                "inventory_item_id": ing.inventory_item_id,
                "inventory_item_name": ing.inventory_item.name if ing.inventory_item else None,
                "quantity": ing.quantity,
                "unit": ing.unit,
            }
            for ing in recipe.ingredients
        ],
    }


@router.get("/", response_model=List[RecipeOut])
async def list_recipes(
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    r = await db.execute(
        select(Recipe)
        .where(Recipe.restaurant_id == u.restaurant_id, Recipe.is_active == True)
        .options(
            selectinload(Recipe.ingredients).selectinload(RecipeIngredient.inventory_item)
        )
    )
    recipes = r.scalars().all()
    # fetch menu item names
    menu_ids = [rec.menu_item_id for rec in recipes]
    names_r = await db.execute(select(MenuItem).where(MenuItem.id.in_(menu_ids)))
    name_map = {m.id: m.name for m in names_r.scalars().all()}
    return [_to_out(rec, name_map.get(rec.menu_item_id)) for rec in recipes]


@router.get("/{menu_item_id}", response_model=RecipeOut)
async def get_recipe(
    menu_item_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    r = await db.execute(
        select(Recipe)
        .where(Recipe.menu_item_id == menu_item_id, Recipe.restaurant_id == u.restaurant_id)
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.inventory_item))
    )
    recipe = r.scalar_one_or_none()
    if not recipe:
        raise HTTPException(404, "No recipe found for this menu item")
    menu = await db.get(MenuItem, menu_item_id)
    return _to_out(recipe, menu.name if menu else None)


@router.post("/", response_model=RecipeOut, status_code=201)
async def create_or_update_recipe(
    body: RecipeCreate,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    # Check if recipe already exists — if so, replace ingredients
    r = await db.execute(
        select(Recipe)
        .where(Recipe.menu_item_id == body.menu_item_id, Recipe.restaurant_id == u.restaurant_id)
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.inventory_item))
    )
    recipe = r.scalar_one_or_none()

    if recipe:
        # Delete old ingredients and replace
        for ing in recipe.ingredients:
            await db.delete(ing)
        await db.flush()
    else:
        recipe = Recipe(menu_item_id=body.menu_item_id, restaurant_id=u.restaurant_id)
        db.add(recipe)
        await db.flush()

    # Add new ingredients
    for ing_data in body.ingredients:
        ing = RecipeIngredient(
            recipe_id=recipe.id,
            inventory_item_id=ing_data.inventory_item_id,
            quantity=ing_data.quantity,
            unit=ing_data.unit,
        )
        db.add(ing)

    await db.flush()
    await db.refresh(recipe, ["ingredients"])
    # reload with inventory_item names
    r2 = await db.execute(
        select(Recipe).where(Recipe.id == recipe.id)
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.inventory_item))
    )
    recipe = r2.scalar_one()
    menu = await db.get(MenuItem, body.menu_item_id)
    return _to_out(recipe, menu.name if menu else None)


@router.delete("/{menu_item_id}", status_code=204)
async def delete_recipe(
    menu_item_id: int,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    r = await db.execute(
        select(Recipe)
        .where(Recipe.menu_item_id == menu_item_id, Recipe.restaurant_id == u.restaurant_id)
    )
    recipe = r.scalar_one_or_none()
    if not recipe:
        raise HTTPException(404, "Recipe not found")
    recipe.is_active = False


async def deduct_inventory_for_order(order_items, restaurant_id: int, order_id: int, db: AsyncSession):
    """
    Called after an order is marked as KOT Sent.
    For each order item, find its recipe and deduct ingredients from inventory.
    """
    for order_item in order_items:
        if not order_item.menu_item_id:
            continue

        # Find recipe for this menu item
        r = await db.execute(
            select(Recipe)
            .where(Recipe.menu_item_id == order_item.menu_item_id, Recipe.restaurant_id == restaurant_id, Recipe.is_active == True)
            .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.inventory_item))
        )
        recipe = r.scalar_one_or_none()
        if not recipe:
            continue

        # Deduct each ingredient × quantity ordered
        for ing in recipe.ingredients:
            inv_item = ing.inventory_item
            if not inv_item:
                continue

            total_to_deduct = round(ing.quantity * order_item.quantity, 4)

            # Deduct from stock (don't go below 0)
            inv_item.quantity = max(0, round(inv_item.quantity - total_to_deduct, 4))

            # Log the usage
            db.add(InventoryUsageLog(
                item_id=inv_item.id,
                restaurant_id=restaurant_id,
                quantity_used=total_to_deduct,
                reason="order",
                order_id=order_id,
                notes=f"Used in order #{order_item.order_id} — {order_item.name} ×{order_item.quantity}",
            ))