"""add_split_orders_to_strategy_state

Revision ID: c861d0959216
Revises: 84f0a3a04ce0
Create Date: 2026-03-05 17:24:43.931657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c861d0959216'
down_revision: Union[str, Sequence[str], None] = '84f0a3a04ce0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """strategy_state 테이블에 split_orders 컬럼 추가.
    SQLite batch_alter 사용 (DROP COLUMN 미지원 우회).
    이미 컬럼이 존재하는 DB에는 멱등성 보장.
    """
    import sqlalchemy as sa
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("strategy_state")]
    if "split_orders" not in existing_cols:
        with op.batch_alter_table("strategy_state", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("split_orders", sa.Text(), nullable=False, server_default="{}")
            )


def downgrade() -> None:
    """split_orders 컬럼 제거."""
    with op.batch_alter_table("strategy_state", schema=None) as batch_op:
        batch_op.drop_column("split_orders")
