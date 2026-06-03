"""add billing settings to restaurants

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('restaurants', sa.Column('whatsapp_sharing', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('restaurants', sa.Column('round_off', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('restaurants', sa.Column('loyalty_enabled', sa.Boolean(), nullable=False, server_default='true'))


def downgrade() -> None:
    op.drop_column('restaurants', 'loyalty_enabled')
    op.drop_column('restaurants', 'round_off')
    op.drop_column('restaurants', 'whatsapp_sharing')
