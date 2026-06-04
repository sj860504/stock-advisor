"""add_trigger_reason_to_trade_history

Revision ID: a7b4c2e91d5f
Revises: c861d0959216
Create Date: 2026-06-04 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b4c2e91d5f'
down_revision: Union[str, Sequence[str], None] = 'c861d0959216'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """trade_history 테이블에 trigger_reason 컬럼 + 인덱스 추가.
    구조화된 매도/매수 사유 저장용 (분석 쿼리/그룹핑).
    이미 컬럼이 존재하는 DB에는 멱등성 보장.
    """
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("trade_history")]
    if "trigger_reason" not in existing_cols:
        with op.batch_alter_table("trade_history", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("trigger_reason", sa.String(length=50), nullable=True)
            )
        # 인덱스 — 분석 쿼리(GROUP BY trigger_reason) 빠르게.
        op.create_index(
            op.f("ix_trade_history_trigger_reason"),
            "trade_history",
            ["trigger_reason"],
            unique=False,
        )


def downgrade() -> None:
    """trigger_reason 컬럼/인덱스 제거."""
    op.drop_index(op.f("ix_trade_history_trigger_reason"), table_name="trade_history")
    with op.batch_alter_table("trade_history", schema=None) as batch_op:
        batch_op.drop_column("trigger_reason")
