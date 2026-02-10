from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.ticker_service import TickerService
from stock_advisor.services.scheduler_service import SchedulerService

router = APIRouter(
    prefix="/portfolio",
    tags=["Portfolio"]
)

@router.post("/upload")
async def upload_portfolio(
    file: UploadFile = File(...),
    user_id: str = Form(default="default")
):
    """
    엑셀 파일을 업로드하여 포트폴리오를 등록합니다.
    
    엑셀 형식:
    | 티커/종목명 | 수량 | 매수가 | (매수일) |
    |------------|------|--------|---------|
    | AAPL       | 10   | 150    | 2024-01-15 |
    | 삼성전자   | 50   | 70000  | 2024-02-01 |
    """
    try:
        content = await file.read()
        holdings = PortfolioService.parse_excel(content, file.filename)
        PortfolioService.save_portfolio(user_id, holdings)
        
        return {
            "message": f"포트폴리오 업로드 성공! {len(holdings)}개 종목 등록됨",
            "holdings": holdings
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{user_id}")
def get_portfolio(user_id: str = "default"):
    """
    저장된 포트폴리오를 조회합니다.
    """
    holdings = PortfolioService.load_portfolio(user_id)
    if not holdings:
        return {"message": "등록된 포트폴리오가 없습니다.", "holdings": []}
    return {"holdings": holdings}

@router.get("/{user_id}/analysis")
def analyze_portfolio(user_id: str = "default"):
    """
    포트폴리오 수익률을 분석합니다.
    """
    price_cache = SchedulerService.get_all_cached_prices()
    result = PortfolioService.analyze_portfolio(user_id, price_cache)
    return result

@router.get("/{user_id}/full-report")
def get_full_portfolio_report(user_id: str = "default"):
    """
    보유 종목 전체에 대한 상세 분석 데이터를 반환합니다.
    """
    holdings = PortfolioService.load_portfolio(user_id)
    price_cache = SchedulerService.get_all_cached_prices()
    
    report = []
    for item in holdings:
        ticker = item['ticker']
        if not ticker: continue
        
        # 캐시된 데이터 활용
        cached = price_cache.get(ticker, {})
        price = cached.get('price') or item['buy_price']
        
        profit_pct = ((price - item['buy_price']) / item['buy_price']) * 100 if item['buy_price'] > 0 else 0
        
        dcf = cached.get('fair_value_dcf')
        upside = 0
        if dcf and price:
            upside = ((dcf - price) / price) * 100
            
        report.append({
            "ticker": ticker,
            "name": item.get('name'),
            "price": price,
            "change": cached.get('change', 0),
            "change_pct": cached.get('change_pct', 0),
            "pre_price": cached.get('pre_price'),
            "pre_change_pct": cached.get('pre_change_pct'),
            "return_pct": round(profit_pct, 2),
            "rsi": cached.get('rsi'),
            "ema5": cached.get('ema5'),
            "ema10": cached.get('ema10'),
            "ema20": cached.get('ema20'),
            "ema60": cached.get('ema60'),
            "ema120": cached.get('ema120'),
            "ema200": cached.get('ema200'),
            "dcf_fair": dcf,
            "dcf_upside": round(upside, 1) if dcf else None
        })
        
    report.sort(key=lambda x: x['return_pct'], reverse=True)
    return report

@router.post("/{user_id}/add")
def add_holding(
    user_id: str,
    ticker: str,
    quantity: float,
    buy_price: float,
    name: Optional[str] = None
):
    """
    수동으로 보유 종목을 추가합니다.
    """
    # 티커 변환
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.add_holding(user_id, resolved_ticker, quantity, buy_price, name)
    return {"message": f"{resolved_ticker} 추가 완료", "holdings": holdings}

@router.delete("/{user_id}/{ticker}")
def remove_holding(user_id: str, ticker: str):
    """
    보유 종목을 제거합니다.
    """
    holdings = PortfolioService.remove_holding(user_id, ticker)
    return {"message": f"{ticker} 제거 완료", "holdings": holdings}

@router.post("/{user_id}/trade")
def trade_holding(
    user_id: str,
    ticker: str,
    action: str,
    quantity: float,
    price: float
):
    """
    주식 매수/매도 통합 처리
    - action: 'buy' 또는 'sell'
    """
    resolved_ticker = TickerService.resolve_ticker(ticker)
    
    try:
        if action.lower() == "buy":
            holdings = PortfolioService.add_holding(user_id, resolved_ticker, quantity, price)
        elif action.lower() == "sell":
            holdings = PortfolioService.sell_holding(user_id, resolved_ticker, quantity, price)
        else:
            raise HTTPException(status_code=400, detail="Invalid action. Use 'buy' or 'sell'.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    return {"message": f"{resolved_ticker} {action.upper()} completed", "holdings": holdings}
