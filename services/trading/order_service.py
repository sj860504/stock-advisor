"""매매 내역 기록 및 조회 서비스."""
from datetime import datetime
from typing import List

from models.schemas import TradeRecordDto
from models.trade_history import TradeHistory
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger

logger = get_logger("order_service")

DEFAULT_TRADE_HISTORY_LIMIT = 50


class OrderService:
    """매매 내역 DB 기록 및 최근 내역 조회."""

    @classmethod
    def record_trade(
        cls,
        ticker: str,
        order_type: str,
        quantity: int,
        price: float,
        result_msg: str,
        strategy_name: str = "manual",
    ):
        """매매 내역을 DB에 기록합니다. 성공 시 TradeHistory 엔티티, 실패 시 None 반환."""
        session = StockMetaService.get_session()
        try:
            trade = TradeHistory(
                ticker=ticker,
                order_type=order_type,
                quantity=quantity,
                price=price,
                result_msg=result_msg,
                timestamp=datetime.now(),
                strategy_name=strategy_name,
            )
            session.add(trade)
            session.commit()
            logger.info(f"💾 Trade recorded: {ticker} {order_type} {quantity} @ {price}")
            if trade:
                session.expunge(trade)
            return trade
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error recording trade: {e}")
            return None
        finally:
            session.close()

    @classmethod
    def get_trade_history(cls, limit: int = DEFAULT_TRADE_HISTORY_LIMIT) -> List[TradeRecordDto]:
        """최근 매매 내역을 DTO 리스트로 조회합니다."""
        session = StockMetaService.get_session()
        try:
            trades = (
                session.query(TradeHistory)
                .order_by(TradeHistory.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                TradeRecordDto(
                    id=record.id,
                    ticker=record.ticker,
                    order_type=record.order_type,
                    quantity=record.quantity,
                    price=record.price,
                    result_msg=record.result_msg,
                    timestamp=record.timestamp.isoformat() if record.timestamp else None,
                    strategy_name=record.strategy_name or "manual",
                )
                for record in trades
            ]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history: {e}")
            return []
        finally:
            session.close()