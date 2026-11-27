from sqlalchemy import String, Boolean, Integer, Float, ForeignKey, Text, DateTime, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.db.session import Base


class MenuCategory(Base):
    __tablename__ = "menu_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    items: Mapped[list["MenuItem"]] = relationship("MenuItem", back_populates="category")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("menu_categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    emoji: Mapped[str] = mapped_column(String(10), default="🍽️")
    food_type: Mapped[str] = mapped_column(String(20), default="veg")  # veg|non_veg|egg|vegan|na
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=True)  # NULL = stock not tracked
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Bumped on every photo upload; NULL = no photo. Clients build the (cacheable)
    # URL /api/public/menu-image/{id}/{image_version} from it.
    image_version: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    category: Mapped["MenuCategory"] = relationship("MenuCategory", back_populates="items")


class MenuItemImage(Base):
    """A dish photo, kept apart from menu_items so menu queries never load image
    bytes. Already compressed by the browser (≈50–150 KB WebP/JPEG)."""
    __tablename__ = "menu_item_images"

    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"), primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    mime: Mapped[str] = mapped_column(String(20))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
