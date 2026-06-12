"""add_uptrend_state_to_strategy_state

Revision ID: b8e5d2f10a4c
Revises: a7b4c2e91d5f
Create Date: 2026-06-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e5d2f10a4c'
down_revision: Union[str, Sequence[str], None] = 'a7b4c2e91d5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """strategy_state 에 Uptrend DCA 상태 컬럼 4개 추가.
    - partial_take_done : {ticker: True}
    - remaining_high    : {ticker: float}  (부분익절 후 잔여 trailing 고점)
    - dca_done          : {ticker: {-3:True, -8:False, -15:False}}
    - stop_loss_streak  : {ticker: {days:int, last_date:str}}
    SQLite batch_alter 사용. 멱등성 보장 (이미 컬럼이 있으면 skip).
    """
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("strategy_state")]

    cols_to_add = [
        ("partial_take_done", sa.Text(), "{}"),
        ("remaining_high",    sa.Text(), "{}"),
        ("dca_done",          sa.Text(), "{}"),
        ("stop_loss_streak",  sa.Text(), "{}"),
    ]
    new_cols = [(name, type_, default) for name, type_, default in cols_to_add if name not in existing_cols]
    if not new_cols:
        return

    with op.batch_alter_table("strategy_state", schema=None) as batch_op:
        for name, type_, default in new_cols:
            batch_op.add_column(sa.Column(name, type_, nullable=False, server_default=default))


def downgrade() -> None:
    """4개 컬럼 제거."""
    with op.batch_alter_table("strategy_state", schema=None) as batch_op:
        for name in ("stop_loss_streak", "dca_done", "remaining_high", "partial_take_done"):
            batch_op.drop_column(name)
