from sqlalchemy import String, Integer, Float, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class Recipe(Base):
    """Links a menu item to its ingredients (inventory items) with quantities."""
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient", back_populates="recipe", cascade="all, delete-orphan"
    )


class RecipeIngredient(Base):
    """One ingredient line in a recipe."""
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)   # how much to deduct per dish served
    unit: Mapped[str] = mapped_column(String(20))    # kg | L | g | ml | pcs

    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="ingredients")
    inventory_item: Mapped["InventoryItem"] = relationship("InventoryItem")