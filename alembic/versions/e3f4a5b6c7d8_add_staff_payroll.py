"""staff: weekly off, notes, advances, salary payouts, paid-leave setting

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('restaurants', sa.Column('paid_leaves_per_month', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('staff', sa.Column('weekly_off', sa.Integer(), nullable=True))
    op.add_column('staff', sa.Column('notes', sa.String(500), nullable=True))

    op.create_table(
        'staff_advances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_staff_advances_restaurant_id', 'staff_advances', ['restaurant_id'])
    op.create_index('ix_staff_advances_staff_id', 'staff_advances', ['staff_id'])
    op.create_index('ix_staff_advances_date', 'staff_advances', ['date'])

    op.create_table(
        'salary_payouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('restaurant_id', sa.Integer(), sa.ForeignKey('restaurants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('month', sa.Integer(), nullable=False),
        sa.Column('base_salary', sa.Float(), nullable=False),
        sa.Column('worked_days', sa.Float(), nullable=False),
        sa.Column('unpaid_days', sa.Float(), nullable=False),
        sa.Column('deduction', sa.Float(), nullable=False),
        sa.Column('advances', sa.Float(), nullable=False),
        sa.Column('net_payable', sa.Float(), nullable=False),
        sa.Column('paid_amount', sa.Float(), nullable=False),
        sa.Column('method', sa.String(20), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=False),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('staff_id', 'year', 'month', name='uq_salary_payout_staff_month'),
    )
    op.create_index('ix_salary_payouts_restaurant_id', 'salary_payouts', ['restaurant_id'])
    op.create_index('ix_salary_payouts_staff_id', 'salary_payouts', ['staff_id'])


def downgrade() -> None:
    op.drop_table('salary_payouts')
    op.drop_table('staff_advances')
    op.drop_column('staff', 'notes')
    op.drop_column('staff', 'weekly_off')
    op.drop_column('restaurants', 'paid_leaves_per_month')
