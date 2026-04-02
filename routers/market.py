import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from typing import List, Dict, Any
from services.base.scheduler_service import SchedulerService
from services.market.news_service import NewsService
from services.market.market_data_service import MarketDataService
from models.schemas import NewsItem, WatchItem, TradingSignalsResponse

router = APIRouter(
    prefix="/market",
    tags=["Market"]
)


@router.get("/monitored", response_model=Dict[str, Any])
def get_monitored_stocks() -> Dict[str, Any]:
    """Return current prices for all monitored tickers.
    tier=high: WebSocket real-time (top 20 per market + holdings)
    tier=low : 5min polling (remaining 80 tickers)
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    return data


@router.get("/stream")
async def stream_market_prices(request: Request):
    """SSE - KIS WebSocket 가격을 브라우저로 실시간 push.
    변경된 ticker만 diff해서 전송 (1초 간격).
    """
    async def event_generator():
        prev_snapshot: dict = {}
        # 연결 시 전체 스냅샷 첫 전송
        full = MarketDataService.get_price_snapshots()
        if full:
            prev_snapshot = {t: info["price"] for t, info in full.items()}
            yield f"data: {json.dumps(full)}\n\n"

        while True:
            if await request.is_disconnected():
                break
            snapshot = MarketDataService.get_price_snapshots()
            changes = {}
            for ticker, info in snapshot.items():
                if prev_snapshot.get(ticker) != info["price"]:
                    changes[ticker] = info
                    prev_snapshot[ticker] = info["price"]
            if changes:
                yield f"data: {json.dumps(changes)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # nginx 버퍼링 비활성화
            "Connection": "keep-alive",
        },
    )


@router.get("/top20", response_model=Dict[str, Any])
def get_top20_realtime() -> Dict[str, Any]:
    """(Deprecated) Replaced by /api/market/monitored."""
    return get_monitored_stocks()


@router.get("/", response_model=Dict[str, Any])
def get_market_status() -> Dict[str, Any]:
    """Return summary of major indices (KOSPI, KOSDAQ, exchange rate, etc.)."""
    return NewsService.get_market_summary()


@router.get("/news/{ticker_input}", response_model=List[NewsItem])
def get_news(ticker_input: str) -> List[NewsItem]:
    """Return related news list."""
    from services.market.ticker_service import TickerService
    real_ticker = TickerService.resolve_ticker(ticker_input)
    if not real_ticker:
        return []
    return NewsService.get_news(real_ticker)


@router.get("/signals", response_model=TradingSignalsResponse)
def get_trading_signals() -> TradingSignalsResponse:
    """Return tickers with trading signals from current Top 20."""
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return TradingSignalsResponse(message="Data collection is starting...")
    return MarketDataService.build_trading_signals(data)


@router.get("/macro", response_model=Dict[str, Any])
def get_macro_data() -> Dict[str, Any]:
    """Macro economic indicators and market regime analysis (includes regime score out of 100)."""
    from services.market.macro_service import MacroService
    return MacroService.get_macro_data()


@router.get("/calendar/weekly", response_model=List[Dict[str, Any]])
def get_weekly_economic_calendar(days: int = 7) -> List[Dict[str, Any]]:
    """Weekly economic indicator release schedule (includes ET/KST times, sorted by date)."""
    from services.market.economic_calendar_service import EconomicCalendarService
    return [e.model_dump(mode="json") for e in EconomicCalendarService.get_weekly_calendar(days=days)]


@router.get("/regime/history", response_model=List[Dict[str, Any]])
def get_regime_history(days: int = 30) -> List[Dict[str, Any]]:
    """Return last N days of market regime history (newest first)."""
    from services.market.stock_meta_service import StockMetaService
    return StockMetaService.get_market_regime_history(days)


@router.get("/regime/{date}", response_model=Dict[str, Any])
def get_regime_for_date(date: str) -> Dict[str, Any]:
    """Return market regime for a specific date (YYYY-MM-DD). Calculates from historical data if not in DB."""
    from services.market.macro_service import MacroService
    from services.market.stock_meta_service import StockMetaService
    cached = StockMetaService.get_regime_for_date(date)
    if cached:
        return cached
    return MacroService.calculate_historical_regime(date)


@router.get("/watching", response_model=List[WatchItem])
def get_watching_list() -> List[WatchItem]:
    """Return list of currently watched tickers (receiving real-time data)."""
    return MarketDataService.get_watch_list()
