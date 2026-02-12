from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime
from .stock_meta import Base

class TradeHistory(Base):
    """
    매매 내역 모델
    """
    __tablename__ = 'trade_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), index=True, nullable=False)
    order_type = Column(String(10), nullable=False)  # 'buy' or 'sell'
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    result_msg = Column(String(255))
    timestamp = Column(DateTime, default=datetime.now)
    strategy_name = Column(String(50), default="manual")  # 'manual', 'rsi_strategy', etc.

    def __repr__(self):
        return f"<TradeHistory(ticker='{self.ticker}', type='{self.order_type}', qty={self.quantity}, price={self.price})>"
