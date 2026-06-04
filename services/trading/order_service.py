"""Trade history recording and retrieval service."""
from datetime import datetime
from typing import List, Optional, Tuple

from models.schemas import TradeRecordDto
from repositories.stock_meta_repo import StockMetaRepo
from repositories.trade_history_repo import TradeHistoryRepo
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("order_service")

DEFAULT_TRADE_HISTORY_LIMIT = 50


class OrderService:
    """Trade history DB recording and recent history retrieval."""

    @classmethod
    def sell_single_holding(
        cls, ticker: str, name: str, quantity: int, current_price: float
    ) -> Tuple[bool, str]:
        """Execute a single holding sell and return (success, error_message)."""
        from services.kis.kis_service import KisService
        is_us = not is_kr(ticker)
        if is_us:
            # Refresh real-time price just before limit order (prevent stale in-memory price)
            try:
                from services.kis.fetch.kis_fetcher import KisFetcher
                token = KisService.get_access_token()
                fresh = KisFetcher.fetch_overseas_price(token, ticker)
                fresh_price = fresh.get("price", 0)
                if fresh_price > 0:
                    logger.info(f"🔄 {ticker} pre-sell price refresh: ${fresh_price:.2f} (previous: ${current_price:.2f})")
                    current_price = fresh_price
            except Exception as e:
                logger.warning(f"⚠️ {ticker} price refresh failed, using previous price: {e}")
            if current_price <= 0:
                return False, f"{ticker} current price unavailable"
            res = KisService.send_overseas_order(
                ticker=ticker, quantity=quantity,
                price=round(float(current_price), 2), order_type="sell",
            )
        else:
            res = KisService.send_order(ticker, quantity, 0, "sell")
        if res.get("status") == "success":
            return True, ""
        return False, res.get("msg", "Unknown error")

    @classmethod
    def execute_mass_sell(cls, holdings: list) -> Tuple[int, int, List[str]]:
        """Execute mass sell of all holdings and return (success_count, fail_count, failed_tickers)."""
        success_count, fail_count, failed_tickers = 0, 0, []
        for holding in holdings:
            ticker = holding.ticker
            name = (holding.name or ticker)
            quantity = holding.quantity
            if quantity <= 0:
                continue
            logger.info(f"📤 {ticker} ({name}) attempting to sell {quantity} shares...")
            try:
                current_price = holding.current_price or 0
                ok, err = cls.sell_single_holding(ticker, name, quantity, current_price)
                if ok:
                    logger.info(f"✅ {ticker} ({name}) sold {quantity} shares successfully")
                    success_count += 1
                else:
                    logger.error(f"❌ {ticker} sell failed: {err}")
                    fail_count += 1
                    failed_tickers.append(ticker)
            except Exception as e:
                logger.error(f"❌ {ticker} error during sell: {e}")
                fail_count += 1
                failed_tickers.append(ticker)
        return success_count, fail_count, failed_tickers

    @classmethod
    def record_trade(
        cls,
        ticker: str,
        order_type: str,
        quantity: int,
        price: float,
        result_msg: str,
        strategy_name: str = "manual",
        buy_price: Optional[float] = None,
        status: str = "pending",
        trigger_reason: Optional[str] = None,
    ):
        """Record trade history to DB. Default status is 'pending' (verified later).
        Returns TradeHistory entity on success, None on failure.
        trigger_reason: 구조화 사유 ('trailing_stop', 'take_profit', etc.)."""
        return TradeHistoryRepo.record(
            ticker, order_type, quantity, price, result_msg, strategy_name,
            buy_price=buy_price, status=status, trigger_reason=trigger_reason,
        )

    @classmethod
    def verify_and_update_pending_orders(cls) -> List["OrderVerificationResult"]:
        """DB의 pending 주문을 KIS 미체결 API로 확인하여 상태 갱신.
        - KIS 미체결 목록에 없으면 → filled로 업데이트
        - KIS 미체결 목록에 있으면 → pending 유지
        Returns list of verification results."""
        from services.kis.kis_service import KisService
        from models.schemas import OrderVerificationResult

        pending_trades = TradeHistoryRepo.get_pending_orders()
        if not pending_trades:
            return []

        # KR/US 미체결 조회 (pending 있을 때만)
        has_kr = any(is_kr(t.ticker) for t in pending_trades)
        has_us = any(not is_kr(t.ticker) for t in pending_trades)
        kr_unfilled = KisService.get_unfilled_orders_kr() if has_kr else None
        us_unfilled = KisService.get_unfilled_orders_us() if has_us else None

        results = []
        for trade in pending_trades:
            ticker = trade.ticker
            unfilled = kr_unfilled if is_kr(ticker) else us_unfilled
            if unfilled and unfilled.has_pending(ticker, trade.order_type):
                rmn = unfilled.pending_qty(ticker, trade.order_type)
                results.append(OrderVerificationResult(
                    ticker=ticker, order_type=trade.order_type,
                    is_filled=False, remaining_qty=rmn,
                    message=f"{ticker} 미체결 {rmn}주 대기 중",
                ))
            else:
                TradeHistoryRepo.mark_filled(trade.id)
                results.append(OrderVerificationResult(
                    ticker=ticker, order_type=trade.order_type,
                    is_filled=True, remaining_qty=0,
                    message=f"{ticker} 체결 완료 확인",
                ))
                logger.info(f"✅ {ticker} {trade.order_type} 체결 확인 → status=filled (id={trade.id})")

        return results

    @classmethod
    def has_pending_order(cls, ticker: str, order_type: str = None) -> bool:
        """특정 종목에 DB상 pending 주문이 있는지 확인."""
        pending = TradeHistoryRepo.get_pending_orders(ticker)
        if not pending:
            return False
        if order_type:
            return any(t.order_type == order_type for t in pending)
        return True

    @classmethod
    def _to_dto(cls, record, holdings_map: dict) -> TradeRecordDto:
        """Convert TradeHistory entity to TradeRecordDto."""
        holding = holdings_map.get(record.ticker)
        # Average buy price at trade time: DB stored value first, fallback to current holding data
        buy_price = (
            record.buy_price_at_trade
            or (holding.buy_price if holding and holding.buy_price else None)
        )
        profit = None
        profit_pct = None
        if buy_price and record.order_type == "sell":
            profit = round((record.price - buy_price) * record.quantity, 2)
            profit_pct = round((record.price - buy_price) / buy_price * 100, 2)
        return TradeRecordDto(
            id=record.id,
            ticker=record.ticker,
            order_type=record.order_type,
            quantity=record.quantity,
            price=record.price,
            result_msg=record.result_msg,
            timestamp=record.timestamp.isoformat() if record.timestamp else None,
            strategy_name=record.strategy_name or "manual",
            name=holding.name if holding else None,
            buy_price=buy_price,
            profit=profit,
            profit_pct=profit_pct,
        )

    @classmethod
    def get_trade_history(
        cls,
        limit: int = DEFAULT_TRADE_HISTORY_LIMIT,
        market: Optional[str] = None,
        date: Optional[str] = None,
        action: Optional[str] = None,
    ) -> List[TradeRecordDto]:
        """Retrieve recent trade history from DB."""
        try:
            # Normalize market filter
            mkt = market.lower() if market else None
            trades = TradeHistoryRepo.query(market=mkt, date=date, action=action, limit=limit)
            tickers = list(set(t.ticker for t in trades))
            name_map = StockMetaRepo.get_name_map(tickers)
            return [cls._to_trade_record_dto(t, name_map) for t in trades]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history from DB: {e}")
            return []

    @staticmethod
    def _to_trade_record_dto(t, name_map: dict = None) -> TradeRecordDto:
        """Convert TradeHistory DB model to TradeRecordDto."""
        profit = None
        profit_pct = None
        if t.order_type == "sell" and t.buy_price_at_trade and t.buy_price_at_trade > 0:
            profit = round((t.price - t.buy_price_at_trade) * t.quantity, 2)
            profit_pct = round((t.price - t.buy_price_at_trade) / t.buy_price_at_trade * 100, 2)
        name = name_map.get(t.ticker) if name_map else None
        return TradeRecordDto(
            id=str(t.id),
            ticker=t.ticker,
            order_type=t.order_type,
            quantity=t.quantity,
            price=t.price,
            result_msg=t.result_msg,
            timestamp=t.timestamp.strftime("%Y-%m-%d %H:%M:%S") if t.timestamp else None,
            strategy_name=t.strategy_name or "",
            name=name,
            buy_price=t.buy_price_at_trade,
            profit=profit,
            profit_pct=profit_pct,
        )

    @classmethod
    def get_trade_history_by_date_range(
        cls, start_dt: datetime, end_dt: Optional[datetime] = None
    ) -> List[TradeRecordDto]:
        """Retrieve trade history within the specified date range in chronological order."""
        try:
            trades = TradeHistoryRepo.query_by_date_range(start_dt, end_dt)
            holdings_map = TradeHistoryRepo.get_holdings_map([t.ticker for t in trades])
            return [cls._to_dto(r, holdings_map) for r in trades]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history by date range: {e}")
            return []