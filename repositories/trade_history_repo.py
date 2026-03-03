"""매매 내역 Repository."""
from datetime import datetime
from typing import List, Optional

from models.portfolio import PortfolioHolding
from models.trade_history import TradeHistory
from repositories.database import get_session, session_scope
from utils.logger import get_logger

logger = get_logger("trade_history_repo")


class TradeHistoryRepo:
    """TradeHistory 테이블 CRUD."""

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
    ) -> Optional[TradeHistory]:
        """매매 내역 DB 기록. 성공 시 detached TradeHistory, 실패 시 None."""
        try:
            with session_scope() as session:
                trade = TradeHistory(
                    ticker=ticker,
                    order_type=order_type,
                    quantity=quantity,
                    price=price,
                    buy_price_at_trade=buy_price,
                    result_msg=result_msg,
                    timestamp=datetime.now(),
                    strategy_name=strategy_name,
                )
                session.add(trade)
                session.flush()
                session.expunge(trade)
                logger.info(f"💾 Trade recorded: {ticker} {order_type} {quantity} @ {price}")
                return trade
        except Exception as e:
            logger.error(f"❌ Error recording trade: {e}")
            return None

    @classmethod
    def query(
        cls,
        market: Optional[str] = None,
        date: Optional[str] = None,
        limit: int = 50,
    ) -> List[TradeHistory]:
        """매매 내역 조회. market=kr/us/None(전체), date=YYYY-MM-DD."""
        session = get_session()
        try:
            q = session.query(TradeHistory)
            q = cls._apply_filters(q, market, date)
            return q.order_by(TradeHistory.timestamp.desc()).limit(limit).all()
        finally:
            session.close()

    @classmethod
    def query_by_date_range(
        cls,
        start_dt: datetime,
        end_dt: Optional[datetime] = None,
    ) -> List[TradeHistory]:
        """날짜 범위 매매 내역 조회 (오름차순)."""
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
        """{ticker: PortfolioHolding} 맵 반환."""
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
    def _apply_filters(query, market: Optional[str], date: Optional[str]):
        """market/date 필터 적용 후 query 반환."""
        if market == "kr":
            query = query.filter(TradeHistory.ticker.op("GLOB")("[0-9]*"))
        elif market == "us":
            query = query.filter(~TradeHistory.ticker.op("GLOB")("[0-9]*"))
        if date:
            start_dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
            end_dt = start_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(TradeHistory.timestamp >= start_dt, TradeHistory.timestamp <= end_dt)
        return query
