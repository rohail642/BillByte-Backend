"""table QR: call waiter / ask for bill

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'table_service_calls',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('table_number', sa.String(20), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('pay_by', sa.String(20), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='open'),
        sa.Column('handled_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_table_service_calls_restaurant_id', 'table_service_calls', ['restaurant_id'])
    op.create_index('ix_table_service_calls_status', 'table_service_calls', ['status'])


def downgrade() -> None:
    op.drop_table('table_service_calls')
