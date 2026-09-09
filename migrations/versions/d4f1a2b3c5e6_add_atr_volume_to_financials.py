"""add_atr_volume_to_financials

Revision ID: d4f1a2b3c5e6
Revises: b8e5d2f10a4c
Create Date: 2026-09-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f1a2b3c5e6'
down_revision: Union[str, Sequence[str], None] = 'b8e5d2f10a4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """financials 에 변동성/거래량 컬럼 2개 추가 (ATR 사이징·손절, 급락 확인용).
    - atr_pct        : ATR(14) / close × 100
    - avg_volume_20d : 20일 평균 거래량
    멱등성 보장 (이미 컬럼이 있으면 skip)."""
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("financials")]

    cols_to_add = [
        ("atr_pct", sa.Float()),
        ("avg_volume_20d", sa.Float()),
    ]
    new_cols = [(name, type_) for name, type_ in cols_to_add if name not in existing_cols]
    if not new_cols:
        return

    with op.batch_alter_table("financials", schema=None) as batch_op:
        for name, type_ in new_cols:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("financials", schema=None) as batch_op:
        for name in ("avg_volume_20d", "atr_pct"):
            batch_op.drop_column(name)
