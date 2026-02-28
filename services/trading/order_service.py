"""매매 내역 기록 및 조회 서비스."""
from datetime import datetime
from typing import List, Optional, Tuple

from models.schemas import TradeRecordDto
from repositories.trade_history_repo import TradeHistoryRepo
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("order_service")

DEFAULT_TRADE_HISTORY_LIMIT = 50


class OrderService:
    """매매 내역 DB 기록 및 최근 내역 조회."""

    @classmethod
    def sell_single_holding(
        cls, ticker: str, name: str, quantity: int, current_price: float
    ) -> Tuple[bool, str]:
        """단일 종목 매도를 실행하고 (성공 여부, 오류 메시지)를 반환합니다."""
        from services.kis.kis_service import KisService
        is_us = not is_kr(ticker)
        if is_us:
            if current_price <= 0:
                return False, f"{ticker} 현재가 정보 없음"
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
        """보유 종목 전량 매도를 실행하고 (성공수, 실패수, 실패_티커_목록)을 반환합니다."""
        success_count, fail_count, failed_tickers = 0, 0, []
        for holding in holdings:
            ticker = holding["ticker"]
            name = holding.get("name", ticker)
            quantity = holding["quantity"]
            if quantity <= 0:
                continue
            logger.info(f"📤 {ticker} ({name}) {quantity}주 매도 시도...")
            try:
                ok, err = cls.sell_single_holding(ticker, name, quantity, holding.get("current_price", 0))
                if ok:
                    logger.info(f"✅ {ticker} ({name}) {quantity}주 매도 성공")
                    success_count += 1
                else:
                    logger.error(f"❌ {ticker} 매도 실패: {err}")
                    fail_count += 1
                    failed_tickers.append(ticker)
            except Exception as e:
                logger.error(f"❌ {ticker} 매도 중 오류: {e}")
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
    ):
        """매매 내역을 DB에 기록합니다. 성공 시 TradeHistory 엔티티, 실패 시 None 반환."""
        return TradeHistoryRepo.record(ticker, order_type, quantity, price, result_msg, strategy_name)

    @classmethod
    def _to_dto(cls, record, holdings_map: dict) -> TradeRecordDto:
        """TradeHistory 엔티티를 TradeRecordDto로 변환합니다."""
        holding = holdings_map.get(record.ticker)
        buy_price = holding.buy_price if holding and holding.buy_price else None
        profit = None
        if buy_price and record.order_type == "sell":
            profit = round((record.price - buy_price) * record.quantity, 2)
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
        )

    @classmethod
    def get_trade_history(
        cls,
        limit: int = DEFAULT_TRADE_HISTORY_LIMIT,
        market: Optional[str] = None,
        date: Optional[str] = None,
    ) -> List[TradeRecordDto]:
        """최근 매매 내역 조회. market=kr/us/None(전체), date=YYYY-MM-DD."""
        try:
            if market in ("kr", "us"):
                trades = TradeHistoryRepo.query(market=market, date=date, limit=limit)
            else:
                # 전체: 한국/미국 각각 half건씩 보장
                half = limit // 2
                kr_trades = TradeHistoryRepo.query(market="kr", date=date, limit=half)
                us_trades = TradeHistoryRepo.query(market="us", date=date, limit=half)
                trades = sorted(kr_trades + us_trades, key=lambda x: x.timestamp, reverse=True)
            holdings_map = TradeHistoryRepo.get_holdings_map([t.ticker for t in trades])
            return [cls._to_dto(r, holdings_map) for r in trades]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history: {e}")
            return []

    @classmethod
    def get_trade_history_by_date_range(
        cls, start_dt: datetime, end_dt: Optional[datetime] = None
    ) -> List[TradeRecordDto]:
        """지정된 날짜 범위의 매매 내역을 시간순으로 조회합니다."""
        try:
            trades = TradeHistoryRepo.query_by_date_range(start_dt, end_dt)
            holdings_map = TradeHistoryRepo.get_holdings_map([t.ticker for t in trades])
            return [cls._to_dto(r, holdings_map) for r in trades]
        except Exception as e:
            logger.error(f"❌ Error fetching trade history by date range: {e}")
            return []