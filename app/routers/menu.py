from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.db.session import get_db
from app.models.menu import MenuItem, MenuCategory
from app.models.user import User
from app.schemas.menu import MenuItemCreate, MenuItemUpdate, MenuItemOut, CategoryCreate, CategoryOut
from app.core.security import get_current_user

router = APIRouter(prefix="/menu", tags=["Menu"])


@router.get("/categories", response_model=List[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(
        select(MenuCategory).where(MenuCategory.restaurant_id == u.restaurant_id)
        .order_by(MenuCategory.sort_order)
    )
    return r.scalars().all()


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    cat = MenuCategory(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(cat); await db.flush(); return cat


@router.delete("/categories/{cat_id}", status_code=204)
async def delete_category(cat_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(MenuCategory).where(MenuCategory.id == cat_id, MenuCategory.restaurant_id == u.restaurant_id))
    cat = r.scalar_one_or_none()
    if not cat: raise HTTPException(404, "Category not found")
    await db.delete(cat)


@router.get("/items", response_model=List[MenuItemOut])
async def list_items(
    category_id: int | None = None,
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    q = select(MenuItem).where(MenuItem.restaurant_id == u.restaurant_id)
    if category_id: q = q.where(MenuItem.category_id == category_id)
    if active_only: q = q.where(MenuItem.is_active == True)
    r = await db.execute(q.order_by(MenuItem.sort_order, MenuItem.name))
    return r.scalars().all()


@router.post("/items", response_model=MenuItemOut, status_code=201)
async def create_item(body: MenuItemCreate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    item = MenuItem(restaurant_id=u.restaurant_id, **body.model_dump())
    db.add(item); await db.flush(); return item


@router.get("/items/{item_id}", response_model=MenuItemOut)
async def get_item(item_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(MenuItem).where(MenuItem.id == item_id, MenuItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    return item


@router.patch("/items/{item_id}", response_model=MenuItemOut)
async def update_item(item_id: int, body: MenuItemUpdate, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(MenuItem).where(MenuItem.id == item_id, MenuItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(item, k, v)
    return item


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(get_current_user)):
    r = await db.execute(select(MenuItem).where(MenuItem.id == item_id, MenuItem.restaurant_id == u.restaurant_id))
    item = r.scalar_one_or_none()
    if not item: raise HTTPException(404, "Item not found")
    await db.delete(item)
