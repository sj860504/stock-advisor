"""Trade history repository."""
from datetime import datetime
from typing import List, Optional

from models.portfolio import PortfolioHolding
from models.trade_history import TradeHistory
from repositories.database import get_session, session_scope
from utils.logger import get_logger

logger = get_logger("trade_history_repo")


class TradeHistoryRepo:
    """TradeHistory table CRUD."""

    @classmethod
    def record(
        cls,
        ticker: str,
        order_type: str,
        quantity: int,
        price: float,
        result_msg: str,
        strategy_name: str = "manual",
        buy_price: Optional[float] = None,
        status: str = "filled",
        trigger_reason: Optional[str] = None,
    ) -> Optional[TradeHistory]:
        """Record trade to DB. Returns detached TradeHistory on success, None on failure.
        trigger_reason: 구조화된 사유 (분석/그룹핑용). 예: 'trailing_stop', 'take_profit',
                        'stop_loss', 'score_buy', 'score_sell', 'add_position', 'budget_buy', 'manual'."""
        try:
            with session_scope() as session:
                trade = TradeHistory(
                    ticker=ticker,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    buy_price_at_trade=buy_price,
                    result_msg=result_msg,
                    trigger_reason=trigger_reason,
                    timestamp=datetime.now(),
                    strategy_name=strategy_name,
                    status=status,
                )
                session.add(trade)
                session.flush()
                session.expunge(trade)
                logger.info(f"💾 Trade recorded: {ticker} {order_type} {quantity} @ {price} (status={status}, reason={trigger_reason})")
                return trade
        except Exception as e:
            logger.error(f"❌ Error recording trade: {e}")
            return None

    @classmethod
    def get_pending_orders(cls, ticker: str = None) -> List[TradeHistory]:
        """Fetch pending (unfilled) orders, optionally filtered by ticker."""
        session = get_session()
        try:
            q = session.query(TradeHistory).filter(TradeHistory.status == "pending")
            if ticker:
                q = q.filter(TradeHistory.ticker == ticker)
            return q.order_by(TradeHistory.timestamp.desc()).all()
        finally:
            session.close()

    @classmethod
    def update_status(cls, trade_id: int, status: str) -> bool:
        """Update the status of a trade record."""
        try:
            with session_scope() as session:
                trade = session.query(TradeHistory).filter_by(id=trade_id).first()
                if trade:
                    trade.status = status
                    return True
                return False
        except Exception as e:
            logger.error(f"❌ Error updating trade status (id={trade_id}): {e}")
            return False

    @classmethod
    def mark_filled(cls, trade_id: int) -> bool:
        """Mark a pending order as filled."""
        return cls.update_status(trade_id, "filled")

    @classmethod
    def query(
        cls,
        market: Optional[str] = None,
        date: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
    ) -> List[TradeHistory]:
        """Query trade history. market=kr/us/None(all), date=YYYY-MM-DD."""
        session = get_session()
        try:
            q = session.query(TradeHistory)
            q = cls._apply_filters(q, market, date, action)
            return q.order_by(TradeHistory.timestamp.desc()).limit(limit).all()
        finally:
            session.close()

    @classmethod
    def query_by_date_range(
        cls,
        start_dt: datetime,
        end_dt: Optional[datetime] = None,
    ) -> List[TradeHistory]:
        """Query trade history by date range (ascending order)."""
        session = get_session()
        try:
            q = session.query(TradeHistory).filter(TradeHistory.timestamp >= start_dt)
            if end_dt:
                q = q.filter(TradeHistory.timestamp < end_dt)
            return q.order_by(TradeHistory.timestamp.asc()).all()
        finally:
            session.close()

    @classmethod
    def get_holdings_map(cls, tickers: list[str]) -> dict[str, PortfolioHolding]:
        """Return {ticker: PortfolioHolding} map."""
        if not tickers:
            return {}
        session = get_session()
        try:
            return {
                h.ticker: h
                for h in session.query(PortfolioHolding)
                .filter(PortfolioHolding.ticker.in_(tickers))
                .all()
            }
        finally:
            session.close()

    @staticmethod
    def _apply_filters(query, market: Optional[str], date: Optional[str], action: Optional[str] = None):
        """Apply market/date/action filters and return query."""
        if market == "kr":
            query = query.filter(TradeHistory.ticker.op("GLOB")("[0-9]*"))
        elif market == "us":
            query = query.filter(~TradeHistory.ticker.op("GLOB")("[0-9]*"))
        if action:
            query = query.filter(TradeHistory.order_type.ilike(f"%{action}%"))
        if date:
            start_dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
            end_dt = start_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(TradeHistory.timestamp >= start_dt, TradeHistory.timestamp <= end_dt)
        return query
