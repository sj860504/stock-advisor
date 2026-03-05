from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.sql import func
from .stock_meta import Base


class StrategyState(Base):
    """사용자별 전략 런타임 상태 (쿨다운·패닉락·틱매매 등)."""

    __tablename__ = "strategy_state"

    user_id         = Column(String(50), primary_key=True)
    sell_cooldown   = Column(Text, nullable=False, default="{}")
    add_buy_cooldown = Column(Text, nullable=False, default="{}")
    panic_locks     = Column(Text, nullable=False, default="{}")
    tick_trade      = Column(Text, nullable=False, default="{}")
    split_orders    = Column(Text, nullable=False, default="{}")
    updated_at      = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<StrategyState(user_id='{self.user_id}')>"
