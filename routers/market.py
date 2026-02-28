from fastapi import APIRouter
from typing import List, Dict, Any
from services.base.scheduler_service import SchedulerService
from services.market.news_service import NewsService
from services.market.market_data_service import MarketDataService
from models.schemas import NewsItem, WatchItem, TradingSignalsResponse

router = APIRouter(
    prefix="/market",
    tags=["Market"]
)


@router.get("/top20", response_model=Dict[str, Any])
def get_top20_realtime() -> Dict[str, Any]:
    """실시간으로 모니터링 중인 미국 주식 상위 20개 기업의 현재가를 반환합니다."""
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    return data


@router.get("/", response_model=Dict[str, Any])
def get_market_status() -> Dict[str, Any]:
    """주요 지수(코스피, 코스닥, 환율 등) 요약 정보를 반환합니다."""
    return NewsService.get_market_summary()


@router.get("/news/{ticker_input}", response_model=List[NewsItem])
def get_news(ticker_input: str) -> List[NewsItem]:
    """관련 뉴스 목록을 반환합니다."""
    from services.market.ticker_service import TickerService
    real_ticker = TickerService.resolve_ticker(ticker_input)
    if not real_ticker:
        return []
    return NewsService.get_news(real_ticker)


@router.get("/signals", response_model=TradingSignalsResponse)
def get_trading_signals() -> TradingSignalsResponse:
    """현재 Top 20 종목 중 매매 신호가 발생한 종목을 반환합니다."""
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return TradingSignalsResponse(message="Data collection is starting...")
    return MarketDataService.build_trading_signals(data)


@router.get("/macro", response_model=Dict[str, Any])
def get_macro_data() -> Dict[str, Any]:
    """거시경제 지표 및 시장 국면 분석 (100점 기준 레짐 점수 포함)."""
    from services.market.macro_service import MacroService
    return MacroService.get_macro_data()


@router.get("/calendar/weekly", response_model=List[Dict[str, Any]])
def get_weekly_economic_calendar(days: int = 7) -> List[Dict[str, Any]]:
    """이번 주 경제지표 발표 일정 (ET·KST 시각 포함, 날짜순)."""
    from services.market.economic_calendar_service import EconomicCalendarService
    return EconomicCalendarService.get_weekly_calendar(days=days)


@router.get("/regime/history", response_model=List[Dict[str, Any]])
def get_regime_history(days: int = 30) -> List[Dict[str, Any]]:
    """최근 N일 시장 국면(regime) 이력 반환 (최신순)."""
    from services.market.stock_meta_service import StockMetaService
    return StockMetaService.get_market_regime_history(days)


@router.get("/regime/{date}", response_model=Dict[str, Any])
def get_regime_for_date(date: str) -> Dict[str, Any]:
    """특정 날짜(YYYY-MM-DD)의 시장 국면 반환. DB에 없으면 역사적 데이터로 계산."""
    from services.market.macro_service import MacroService
    from services.market.stock_meta_service import StockMetaService
    cached = StockMetaService.get_regime_for_date(date)
    if cached:
        return cached
    return MacroService.calculate_historical_regime(date)


@router.get("/watching", response_model=List[WatchItem])
def get_watching_list() -> List[WatchItem]:
    """현재 감시 중인(실시간 데이터 수신 중인) 종목 목록을 반환합니다."""
    return MarketDataService.get_watch_list()
