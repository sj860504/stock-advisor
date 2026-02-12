from fastapi import APIRouter
from typing import List
from services.base.scheduler_service import SchedulerService
from services.market.news_service import NewsService
from models.schemas import NewsItem

router = APIRouter(
    prefix="/market",
    tags=["Market"]
)

@router.get("/top20")
def get_top20_realtime():
    """
    ?ㅼ떆媛꾩쑝濡?紐⑤땲?곕쭅 以묒씤 誘멸뎅 ?쒖킑 ?곸쐞 20媛?湲곗뾽???꾩옱媛瑜?諛섑솚?⑸땲??
    (?쒕쾭 諛깃렇?쇱슫?쒖뿉??1遺꾨쭏???낅뜲?댄듃??
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    return data

@router.get("/")
def get_market_status():
    """
    二쇱슂 吏??肄붿뒪?? 肄붿뒪?? ?섏뒪???? ?꾪솴??議고쉶?⑸땲??
    """
    return NewsService.get_market_summary()

@router.get("/news/{ticker_input}", response_model=List[NewsItem])
def get_news(ticker_input: str):
    """
    愿???댁뒪 留곹겕瑜??쒓났?⑸땲??
    """
    # Note: ticker_input resolving is needed. It's better to import resolve_ticker_or_404 if shared, or reimplement.
    # For simplicity, using TickerService directly here as resolve logic is simple.
    from services.market.ticker_service import TickerService
    real_ticker = TickerService.resolve_ticker(ticker_input)
    if not real_ticker:
        return [] # Or raise 404
        
    return NewsService.get_news(real_ticker)

@router.get("/signals")
def get_trading_signals():
    """
    ?꾩옱 Top 20 醫낅ぉ 以?留ㅻℓ ?좏샇媛 諛쒖깮??醫낅ぉ??諛섑솚?⑸땲??
    - 怨쇰ℓ??(RSI < 30)
    - 怨쇰ℓ??(RSI > 70)  
    - DCF ??됯? (?꾩옱媛 < DCF * 0.8)
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
