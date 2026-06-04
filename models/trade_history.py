from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from .stock_meta import Base


class TradeHistory(Base):
    """
    Trade history model.
    """
    __tablename__ = 'trade_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), index=True, nullable=False)
    order_type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    buy_price_at_trade = Column(Float, nullable=True)  # Average buy price at trade time (for P&L calculation)
    result_msg = Column(String(255))
    # 구조화된 매도/매수 사유 — 분석 쿼리/그룹핑용.
    # 값: 'trailing_stop' | 'tight_stop' | 'stop_loss' | 'take_profit'
    #    | 'score_buy' | 'score_sell' | 'add_position' | 'budget_buy'
    #    | 'panic_reentry' | 'manual' | 'asset_mgmt_sell' | 'sector_rebalance'
    trigger_reason = Column(String(50), index=True, nullable=True)
    timestamp = Column(DateTime, default=datetime.now)
    strategy_name = Column(String(50), default="manual")  # 'manual', 'rsi_strategy', etc.
    status = Column(String(20), nullable=False, default="filled")  # 'pending' or 'filled'

    def __repr__(self):
        return f"<TradeHistory(ticker='{self.ticker}', type='{self.order_type}', qty={self.quantity}, price={self.price}, status='{self.status}')>"
