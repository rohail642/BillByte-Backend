"""add telegram daily reporting

Revision ID: c1d2e3f4a5b6
Revises: a8b9c0d1e2f3
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('telegram_chat_id', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('telegram_username', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('telegram_linked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('telegram_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('last_report_sent_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('last_delivery_status', sa.String(length=200), nullable=True))
    op.create_index('ix_users_telegram_chat_id', 'users', ['telegram_chat_id'])

    op.create_table(
        'telegram_link_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )

    op.create_table(
        'telegram_delivery_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id'), nullable=False, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('report_date', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('telegram_response', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('manual', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )


def downgrade() -> None:
    op.drop_table('telegram_delivery_logs')
    op.drop_table('telegram_link_tokens')
    op.drop_index('ix_users_telegram_chat_id', table_name='users')
    op.drop_column('users', 'last_delivery_status')
    op.drop_column('users', 'last_report_sent_at')
    op.drop_column('users', 'telegram_enabled')
    op.drop_column('users', 'telegram_linked_at')
    op.drop_column('users', 'telegram_username')
    op.drop_column('users', 'telegram_chat_id')
