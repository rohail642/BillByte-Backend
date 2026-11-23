"""add QR table self-ordering

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('restaurants', sa.Column('qr_ordering_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('restaurants', sa.Column('qr_auto_accept', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('restaurants', sa.Column('qr_token_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('restaurants', sa.Column('upi_vpa', sa.String(100), nullable=True))

    op.create_table(
        'table_order_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('table_number', sa.String(20), nullable=False),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('subtotal', sa.Float(), nullable=False, server_default='0'),
        sa.Column('guest_name', sa.String(100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('reject_reason', sa.String(200), nullable=True),
        sa.Column('access_key', sa.String(64), nullable=False),
        sa.Column('order_id', sa.Integer(), sa.ForeignKey('orders.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kot_number', sa.Integer(), nullable=True),
        sa.Column('handled_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_table_order_requests_restaurant_id', 'table_order_requests', ['restaurant_id'])
    op.create_index('ix_table_order_requests_status', 'table_order_requests', ['status'])


def downgrade() -> None:
    op.drop_index('ix_table_order_requests_status', table_name='table_order_requests')
    op.drop_index('ix_table_order_requests_restaurant_id', table_name='table_order_requests')
    op.drop_table('table_order_requests')
    op.drop_column('restaurants', 'upi_vpa')
    op.drop_column('restaurants', 'qr_token_version')
    op.drop_column('restaurants', 'qr_auto_accept')
    op.drop_column('restaurants', 'qr_ordering_enabled')
