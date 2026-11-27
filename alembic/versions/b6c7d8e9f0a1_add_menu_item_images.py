"""dish photos: menu_items.image_version + menu_item_images

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-11-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('menu_items', sa.Column('image_version', sa.Integer(), nullable=True))
    op.create_table(
        'menu_item_images',
        sa.Column('menu_item_id', sa.Integer(), sa.ForeignKey('menu_items.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mime', sa.String(20), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_menu_item_images_restaurant_id', 'menu_item_images', ['restaurant_id'])


def downgrade() -> None:
    op.drop_table('menu_item_images')
    op.drop_column('menu_items', 'image_version')
