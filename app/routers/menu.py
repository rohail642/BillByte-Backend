from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from io import BytesIO
import openpyxl

from app.db.session import get_db
from app.models.menu import MenuItem, MenuCategory
from app.models.user import User
from app.schemas.menu import MenuItemCreate, MenuItemUpdate, MenuItemOut, CategoryCreate, CategoryOut
from app.core.security import get_current_user

_FOOD_TYPE_MAP = {
    'veg': 'veg', 'vegetarian': 'veg',
    'non_veg': 'non_veg', 'non-veg': 'non_veg', 'nonveg': 'non_veg',
    'non vegetarian': 'non_veg', 'non-vegetarian': 'non_veg',
    'vegan': 'vegan',
}

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


@router.post("/import-excel")
async def import_menu_from_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    u: User = Depends(get_current_user),
):
    if not (file.filename or '').lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "Only .xlsx or .xls files are supported")

    contents = await file.read()
    try:
        wb = openpyxl.load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(400, "Could not read the file. Make sure it is a valid Excel file.")

    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise HTTPException(400, "The spreadsheet is empty.")

    headers = [str(h).strip().lower() if h is not None else '' for h in rows[0]]
    if 'name' not in headers or 'price' not in headers:
        raise HTTPException(400, f"Sheet must have at minimum 'Name' and 'Price' columns. Found: {[h for h in rows[0]]}")

    col = {h: headers.index(h) for h in headers if h}

    r = await db.execute(select(MenuCategory).where(MenuCategory.restaurant_id == u.restaurant_id))
    cat_map = {c.name.lower(): c.id for c in r.scalars().all()}

    created_categories = 0
    created_items = 0
    skipped = 0
    errors: list[str] = []

    for i, row in enumerate(rows[1:], start=2):
        try:
            raw_name = row[col['name']] if col.get('name') is not None else None
            name = str(raw_name).strip() if raw_name not in (None, '') else ''
            if not name or name.lower() == 'none':
                skipped += 1
                continue

            price_raw = row[col['price']] if col.get('price') is not None else None
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                errors.append(f"Row {i}: invalid price '{price_raw}' for '{name}'")
                skipped += 1
                continue

            cat_id = None
            if 'category' in col:
                raw_cat = row[col['category']]
                cat_name = str(raw_cat).strip() if raw_cat not in (None, '') else ''
                if cat_name and cat_name.lower() != 'none':
                    cat_key = cat_name.lower()
                    if cat_key not in cat_map:
                        new_cat = MenuCategory(
                            restaurant_id=u.restaurant_id,
                            name=cat_name,
                            sort_order=len(cat_map),
                        )
                        db.add(new_cat)
                        await db.flush()
                        cat_map[cat_key] = new_cat.id
                        created_categories += 1
                    cat_id = cat_map[cat_key]

            food_type = 'veg'
            if 'type' in col:
                raw_type = row[col['type']]
                if raw_type not in (None, ''):
                    food_type = _FOOD_TYPE_MAP.get(str(raw_type).strip().lower(), 'veg')

            db.add(MenuItem(
                restaurant_id=u.restaurant_id,
                name=name,
                price=price,
                category_id=cat_id,
                food_type=food_type,
                emoji='🍽️',
                is_active=True,
            ))
            created_items += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            skipped += 1

    return {
        "created_categories": created_categories,
        "created_items": created_items,
        "skipped": skipped,
        "errors": errors[:10],
    }
