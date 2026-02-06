from fastapi import FastAPI, HTTPException, BackgroundTasks
from typing import List
from contextlib import asynccontextmanager
from models.schemas import StockRequest, ValuationResult, ReturnAnalysis, PriceAlert, NewsItem
from services.analysis_service import AnalysisService
from services.news_service import NewsService
from services.data_service import DataService
from services.ticker_service import TickerService
from services.scheduler_service import SchedulerService
from services.alert_service import AlertService
from services.financial_service import FinancialService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시 스케줄러 실행
    SchedulerService.start()
    yield
    # 앱 종료 시 정리

app = FastAPI(
    title="Sean's Stock Advisor", 
    description="FinanceDataReader + yfinance 기반 주식 분석 및 알림 API",
    version="2.0.0",
    lifespan=lifespan
)

# In-memory alert storage
alerts = []


def resolve_ticker_or_404(ticker_input: str) -> str:
    resolved = TickerService.resolve_ticker(ticker_input)
    if not resolved:
         raise HTTPException(status_code=404, detail=f"Could not find ticker for: {ticker_input}")
    return resolved

@app.get("/")
def read_root():
    return {"message": "Welcome to Sean's Stock Advisor API. Use /docs for documentation."}

@app.get("/valuation/{ticker_input}", response_model=ValuationResult)
def get_valuation(ticker_input: str):
    """
    해당 종목(이름 또는 티커)의 기술적 지표(RSI, 이동평균)를 기반으로 매수/매도 의견을 제시합니다.
    예: '삼성전자', '테슬라', '005930', 'TSLA'
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.evaluate_stock(real_ticker)
    
    # 덮어쓰기: 결과의 ticker 필드를 사용자가 검색한 이름(또는 매핑된 티커)와 연관지을 수 있게
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    # 편의상 티커명을 반환 결과에 업데이트 (선택 사항)
    # result['ticker'] = f"{ticker_input} ({real_ticker})"
    
    return result

@app.get("/returns/{ticker_input}", response_model=ReturnAnalysis)
def get_returns(ticker_input: str):
    """
    2024년 1월 1일부터 현재까지의 수익률과 MDD(최대 낙폭)를 분석합니다.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.analyze_returns(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail="Data not found")
    return result

@app.get("/news/{ticker_input}", response_model=List[NewsItem])
def get_news(ticker_input: str):
    """
    관련 뉴스 링크를 제공합니다.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    return NewsService.get_news(real_ticker)

@app.get("/market/top20")
def get_top20_realtime():
    """
    실시간으로 모니터링 중인 미국 시총 상위 20개 기업의 현재가를 반환합니다.
    (서버 백그라운드에서 1분마다 업데이트됨)
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    return data

@app.get("/market")
def get_market_status():
    """
    주요 지수(코스피, 코스닥, 나스닥 등) 현황을 조회합니다.
    """
    return NewsService.get_market_summary()

@app.post("/alerts")
def create_alert(alert: PriceAlert):
    """
    가격 알림을 설정합니다. (입력된 티커/이름 자동 변환)
    """
    real_ticker = TickerService.resolve_ticker(alert.ticker)
    alert.ticker = real_ticker # 변환된 티커로 저장
    
    alerts.append(alert)
    return {"message": f"Alert set for {alert.ticker} at {alert.target_price}"}

@app.get("/check-alerts")
def check_alerts():
    """
    설정된 알림 조건을 확인하고 트리거된 알림을 반환합니다.
    (주기적으로 호출하여 확인하는 용도)
    """
    triggered = []
    for alert in alerts:
        if not alert.is_active:
            continue
            
        current_price = DataService.get_current_price(alert.ticker)
        if current_price:
            if alert.condition == "above" and current_price >= alert.target_price:
                triggered.append(f"🔔 {alert.ticker} 도달! 현재가: {current_price} >= 목표가: {alert.target_price}")
            elif alert.condition == "below" and current_price <= alert.target_price:
                triggered.append(f"🔔 {alert.ticker} 도달! 현재가: {current_price} <= 목표가: {alert.target_price}")
    
    return {"triggered_alerts": triggered}

@app.get("/summary")
def get_daily_summary():
    """
    현재 Top 20 종목의 일일 요약 리포트를 생성합니다.
    과매도/과매수/저평가 종목을 한눈에 보여줍니다.
    """
    data = SchedulerService.get_all_cached_prices()
    if not data:
        return {"message": "Data collection is starting... please wait a moment."}
    
    return AlertService.generate_daily_summary(data)

@app.get("/metrics/{ticker_input}")
def get_financial_metrics(ticker_input: str):
    """
    종목의 재무 지표(PER, PBR, ROE, 시가총액 등)를 조회합니다.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    metrics = FinancialService.get_metrics(real_ticker)
    return {
        "ticker": real_ticker,
        "metrics": metrics
    }

@app.get("/signals")
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

