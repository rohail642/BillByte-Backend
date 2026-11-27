from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func as sa_func
from typing import List
from io import BytesIO
import openpyxl

from app.db.session import get_db
from app.models.menu import MenuItem, MenuCategory, MenuItemImage
from app.core.limiter import limiter
from app.models.recipe import Recipe
from app.models.order import OrderItem
from app.models.user import User
from app.schemas.menu import MenuItemCreate, MenuItemUpdate, MenuItemOut, CategoryCreate, CategoryOut
from app.core.security import get_current_user, require_owner

_FOOD_TYPE_MAP = {
    'veg': 'veg', 'vegetarian': 'veg',
    'non_veg': 'non_veg', 'non-veg': 'non_veg', 'nonveg': 'non_veg',
    'non vegetarian': 'non_veg', 'non-vegetarian': 'non_veg',
    'vegan': 'vegan',
    'egg': 'egg', 'eggetarian': 'egg', 'contains egg': 'egg',
}

router = APIRouter(prefix="/menu", tags=["Menu"])
public_router = APIRouter(prefix="/public/menu-image", tags=["Menu (public)"])


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


@router.delete("/all")
async def clear_menu(db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    """Owner-only: wipe the entire menu — all items AND all categories — for the
    restaurant. Recipes for those items are removed and any order-item references
    are detached (order history keeps its denormalized item names)."""
    rid = u.restaurant_id

    item_count = (await db.execute(
        select(sa_func.count()).select_from(MenuItem).where(MenuItem.restaurant_id == rid)
    )).scalar_one()
    cat_count = (await db.execute(
        select(sa_func.count()).select_from(MenuCategory).where(MenuCategory.restaurant_id == rid)
    )).scalar_one()

    # Remove recipes first (recipe_ingredients cascade at DB level via ondelete).
    await db.execute(delete(Recipe).where(Recipe.restaurant_id == rid))
    # Detach historical order lines so the FK delete below doesn't violate.
    item_ids = (await db.execute(select(MenuItem.id).where(MenuItem.restaurant_id == rid))).scalars().all()
    if item_ids:
        await db.execute(
            update(OrderItem).where(OrderItem.menu_item_id.in_(item_ids)).values(menu_item_id=None)
        )
    await db.execute(delete(MenuItem).where(MenuItem.restaurant_id == rid))
    await db.execute(delete(MenuCategory).where(MenuCategory.restaurant_id == rid))

    return {"deleted_items": item_count, "deleted_categories": cat_count}


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


# ── Dish photos ────────────────────────────────────────────────────────────────
# The browser compresses photos before upload (≈640 px WebP/JPEG). They live in
# menu_item_images (never loaded with menu queries) and are served from a
# versioned URL with a one-year immutable cache, plus a small in-memory cache
# here, so each device downloads a photo once and the DB is rarely touched.

MAX_IMAGE_BYTES = 300 * 1024
_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def _sniff_image(data: bytes):
    """Trust the bytes, not the filename/content-type header. SVG is never accepted."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for sig, mime in _SIGNATURES:
        if data.startswith(sig):
            return mime
    return None


class _ImageCache:
    """Tiny LRU of recently served photos (bounded by total bytes)."""
    def __init__(self, max_bytes: int = 24 * 1024 * 1024):
        self.max_bytes, self.size, self.items = max_bytes, 0, OrderedDict()

    def get(self, key):
        v = self.items.get(key)
        if v is not None:
            self.items.move_to_end(key)
        return v

    def drop_item(self, item_id: int):
        """Forget every cached version of a dish's photo (after replace/delete)."""
        for key in [k for k in self.items if k[0] == item_id]:
            self.size -= len(self.items.pop(key)[1])

    def put(self, key, value):
        if key in self.items:
            return
        self.items[key] = value
        self.size += len(value[1])
        while self.size > self.max_bytes and self.items:
            _, (_, data) = self.items.popitem(last=False)
            self.size -= len(data)


_image_cache = _ImageCache()


async def _own_item(item_id: int, db: AsyncSession, u: User) -> MenuItem:
    item = (await db.execute(
        select(MenuItem).where(MenuItem.id == item_id, MenuItem.restaurant_id == u.restaurant_id)
    )).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Item not found")
    return item


@router.put("/items/{item_id}/image", response_model=MenuItemOut)
async def upload_item_image(item_id: int, file: UploadFile = File(...),
                            db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    item = await _own_item(item_id, db, u)
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Photo is too large (max 300 KB after compression).")
    mime = _sniff_image(data)
    if not mime:
        raise HTTPException(415, "Please upload a JPEG, PNG or WebP photo.")
    row = await db.get(MenuItemImage, item.id)
    if row:
        row.data, row.mime = data, mime
    else:
        db.add(MenuItemImage(menu_item_id=item.id, restaurant_id=u.restaurant_id, mime=mime, data=data))
    item.image_version = (item.image_version or 0) + 1
    await db.flush()
    _image_cache.drop_item(item.id)
    return item


@router.delete("/items/{item_id}/image", response_model=MenuItemOut)
async def delete_item_image(item_id: int, db: AsyncSession = Depends(get_db), u: User = Depends(require_owner)):
    item = await _own_item(item_id, db, u)
    await db.execute(delete(MenuItemImage).where(MenuItemImage.menu_item_id == item.id))
    item.image_version = None
    await db.flush()
    _image_cache.drop_item(item.id)
    return item


@public_router.get("/{item_id}/{version}")
@limiter.limit("600/minute")
async def get_item_image(request: Request, item_id: int, version: int, db: AsyncSession = Depends(get_db)):
    """Public (guest menus show photos). The version in the URL changes on every
    upload, so responses can be cached forever."""
    key = (item_id, version)
    hit = _image_cache.get(key)
    if hit is None:
        row = (await db.execute(
            select(MenuItemImage.mime, MenuItemImage.data, MenuItem.image_version)
            .join(MenuItem, MenuItem.id == MenuItemImage.menu_item_id)
            .where(MenuItemImage.menu_item_id == item_id)
        )).first()
        if not row or row.image_version != version:
            raise HTTPException(404, "Not found")
        hit = (row.mime, bytes(row.data))
        _image_cache.put(key, hit)
    mime, data = hit
    return Response(content=data, media_type=mime, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "Cross-Origin-Resource-Policy": "cross-origin",
    })
