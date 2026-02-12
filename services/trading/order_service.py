from datetime import datetime
from sqlalchemy.future import select
from models.trade_history import TradeHistory
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger

logger = get_logger("order_service")

class OrderService:
    """
    매매 내역 관리 서비스
    """
    
    @classmethod
    def record_trade(cls, ticker: str, order_type: str, quantity: int, price: float, result_msg: str, strategy_name: str = "manual"):
        """매매 내역 기록"""
        session = StockMetaService.get_session()
        try:
            trade = TradeHistory(
                ticker=ticker,
                order_type=order_type,
                quantity=quantity,
                price=price,
                result_msg=result_msg,
                timestamp=datetime.now(),
                strategy_name=strategy_name
            )
            session.add(trade)
            session.commit()
            logger.info(f"💾 Trade recorded: {ticker} {order_type} {quantity} @ {price}")
            return trade
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error recording trade: {e}")
            return None

    @classmethod
    def get_trade_history(cls, limit: int = 50):
        """최근 매매 내역 조회"""
        session = StockMetaService.get_session()
        try:
            trades = session.query(TradeHistory).order_by(TradeHistory.timestamp.desc()).limit(limit).all()
            return [
                {
                    "id": t.id,
                    "ticker": t.ticker,
                    "order_type": t.order_type,
                    "quantity": t.quantity,
                    "price": t.price,
                    "result_msg": t.result_msg,
                    "timestamp": t.timestamp.isoformat(),
                    "strategy_name": t.strategy_name
                }
                for t in trades
            ]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history: {e}")
            return []
