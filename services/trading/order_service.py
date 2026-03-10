"""Trade history recording and retrieval service."""
from datetime import datetime
from typing import List, Optional, Tuple

from models.schemas import TradeRecordDto
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
            ticker = holding["ticker"]
            name = holding.get("name", ticker)
            quantity = holding["quantity"]
            if quantity <= 0:
                continue
            logger.info(f"📤 {ticker} ({name}) attempting to sell {quantity} shares...")
            try:
                ok, err = cls.sell_single_holding(ticker, name, quantity, holding.get("current_price", 0))
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
    ):
        """Record trade history to DB. Returns TradeHistory entity on success, None on failure."""
        return TradeHistoryRepo.record(ticker, order_type, quantity, price, result_msg, strategy_name, buy_price=buy_price)

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
        """Retrieve recent trade history from KIS API. limit/date/action are somewhat loosely applied to the fetched batch."""
        from services.kis.kis_service import KisService
        from datetime import datetime, timedelta
        
        try:
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")
            
            kis_trades = []
            
            # Domestic
            if market in (None, "kr", "KR"):
                dom_raw = KisService.get_domestic_trade_history(start_date, end_date)
                for r in dom_raw:
                    # '01' is typically sell, '02' is buy in KIS
                    act = "sell" if r.get("sll_buy_dvsn_cd") in ("01", "1") else "buy"
                    qty = int(r.get("tot_ccld_qty", 0) or 0)
                    if qty > 0:
                        prdt_name = r.get("prdt_name") or r.get("pdno")
                        ts_str = f"{r.get('ord_dt', '')} {r.get('ord_tmd', '')}"
                        kis_trades.append(TradeRecordDto(
                            id=f"kr_{r.get('ord_dt')}_{r.get('odno')}",
                            ticker=r.get("pdno"),
                            order_type=act,
                            quantity=qty,
                            price=float(r.get("avg_prvs", 0) or 0),
                            result_msg="KIS Sync",
                            timestamp=ts_str,
                            strategy_name="KIS_BROKER",
                            name=prdt_name,
                            buy_price=None,
                            profit=None,
                            profit_pct=None
                        ))
                        
            # Overseas
            if market in (None, "us", "US"):
                ovs_raw = KisService.get_overseas_trade_history(start_date, end_date)
                for r in ovs_raw:
                    # '01' is sell, '02' is buy typically
                    act = "sell" if r.get("sll_buy_dvsn_cd") in ("01", "1") else "buy"
                    qty = int(float(r.get("ft_ccld_qty", 0) or 0))
                    if qty > 0:
                        prdt_name = r.get("prdt_name") or r.get("pdno")
                        ts_str = f"{r.get('ord_dt', '')} {r.get('ord_tmd', '')}"
                        kis_trades.append(TradeRecordDto(
                            id=f"us_{r.get('ord_dt')}_{r.get('odno')}",
                            ticker=r.get("pdno"),
                            order_type=act,
                            quantity=qty,
                            price=float(r.get("ft_ccld_pr3", 0) or 0),
                            result_msg="KIS Sync",
                            timestamp=ts_str,
                            strategy_name="KIS_BROKER",
                            name=prdt_name,
                            buy_price=None,
                            profit=None,
                            profit_pct=None
                        ))
            
            # Apply filters
            if action:
                kis_trades = [t for t in kis_trades if t.order_type == action.lower()]
            if date:
                kis_trades = [t for t in kis_trades if t.timestamp and t.timestamp.startswith(date.replace("-", ""))]
                
            # Sort descending by timestamp
            kis_trades = sorted(kis_trades, key=lambda x: x.timestamp or "", reverse=True)
            
            # Limit
            return kis_trades[:limit]
            
        except Exception as e:
            logger.error(f"❌ Error fetching trade history from KIS: {e}")
            return []

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