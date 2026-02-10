from fastapi import APIRouter
from typing import List
from stock_advisor.services.scheduler_service import SchedulerService
from stock_advisor.services.news_service import NewsService
from stock_advisor.models.schemas import NewsItem

router = APIRouter(
    prefix="/market",
    tags=["Market"]
)

@router.get("/top20")
def get_top20_realtime():
    """
    실시간으로 모니터링 중인 미국 시총 상위 20개 기업의 현재가를 반환합니다.
    (서버 백그라운드에서 1분마다 업데이트됨)
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    return data

@router.get("/")
def get_market_status():
    """
    주요 지수(코스피, 코스닥, 나스닥 등) 현황을 조회합니다.
    """
    return NewsService.get_market_summary()

@router.get("/news/{ticker_input}", response_model=List[NewsItem])
def get_news(ticker_input: str):
    """
    관련 뉴스 링크를 제공합니다.
    """
    # Note: ticker_input resolving is needed. It's better to import resolve_ticker_or_404 if shared, or reimplement.
    # For simplicity, using TickerService directly here as resolve logic is simple.
    from stock_advisor.services.ticker_service import TickerService
    real_ticker = TickerService.resolve_ticker(ticker_input)
    if not real_ticker:
        return [] # Or raise 404
        
    return NewsService.get_news(real_ticker)

@router.get("/signals")
def get_trading_signals():
    """
    현재 Top 20 종목 중 매매 신호가 발생한 종목을 반환합니다.
    - 과매도 (RSI < 30)
    - 과매수 (RSI > 70)  
    - DCF 저평가 (현재가 < DCF * 0.8)
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting..."}
    
    signals = {
        "oversold": [],
        "overbought": [],
        "undervalued": [],
        "ema200_support": []
    }
    
    for ticker, info in data.items():
        rsi = info.get('rsi')
        price = info.get('price')
        dcf = info.get('fair_value_dcf')
        ema200 = info.get('ema200')
        
        if rsi and rsi < 30:
            signals["oversold"].append({
                "ticker": ticker,
                "rsi": rsi,
                "price": price,
                "signal": "BUY"
            })
        
        if rsi and rsi > 70:
            signals["overbought"].append({
                "ticker": ticker,
                "rsi": rsi,
                "price": price,
                "signal": "SELL"
            })
        
        if dcf and price and price < dcf * 0.8:
            upside = ((dcf - price) / price) * 100
            signals["undervalued"].append({
                "ticker": ticker,
                "price": price,
                "dcf": round(dcf, 2),
                "upside_pct": round(upside, 1),
                "signal": "BUY"
            })
        
        if ema200 and price and abs(price - ema200) / ema200 < 0.02:
            signals["ema200_support"].append({
                "ticker": ticker,
                "price": price,
                "ema200": round(ema200, 2),
                "signal": "WATCH"
            })
    
    return signals
