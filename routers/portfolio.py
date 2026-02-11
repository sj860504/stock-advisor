from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional
from services.portfolio_service import PortfolioService
from services.ticker_service import TickerService
from services.scheduler_service import SchedulerService

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
    ?묒? ?뚯씪???낅줈?쒗븯???ы듃?대━?ㅻ? ?깅줉?⑸땲??
    
    ?묒? ?뺤떇:
    | ?곗빱/醫낅ぉ紐?| ?섎웾 | 留ㅼ닔媛 | (留ㅼ닔?? |
    |------------|------|--------|---------|
    | AAPL       | 10   | 150    | 2024-01-15 |
    | ?쇱꽦?꾩옄   | 50   | 70000  | 2024-02-01 |
    """
    try:
        content = await file.read()
        holdings = PortfolioService.parse_excel(content, file.filename)
        PortfolioService.save_portfolio(user_id, holdings)
        
        return {
            "message": f"?ы듃?대━???낅줈???깃났! {len(holdings)}媛?醫낅ぉ ?깅줉??,
            "holdings": holdings
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/{user_id}")
def get_portfolio(user_id: str = "default"):
    """
    ??λ맂 ?ы듃?대━?ㅻ? 議고쉶?⑸땲??
    """
    holdings = PortfolioService.load_portfolio(user_id)
    if not holdings:
        return {"message": "?깅줉???ы듃?대━?ㅺ? ?놁뒿?덈떎.", "holdings": []}
    return {"holdings": holdings}

@router.get("/{user_id}/analysis")
def analyze_portfolio(user_id: str = "default"):
    """
    ?ы듃?대━???섏씡瑜좎쓣 遺꾩꽍?⑸땲??
    """
    price_cache = SchedulerService.get_all_cached_prices()
    result = PortfolioService.analyze_portfolio(user_id, price_cache)
    return result

@router.get("/{user_id}/full-report")
def get_full_portfolio_report(user_id: str = "default"):
    """
    蹂댁쑀 醫낅ぉ ?꾩껜??????곸꽭 遺꾩꽍 ?곗씠?곕? 諛섑솚?⑸땲??
    """
    holdings = PortfolioService.load_portfolio(user_id)
    price_cache = SchedulerService.get_all_cached_prices()
    
    report = []
    for item in holdings:
        ticker = item['ticker']
        if not ticker: continue
        
        # 罹먯떆???곗씠???쒖슜
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
    ?섎룞?쇰줈 蹂댁쑀 醫낅ぉ??異붽??⑸땲??
    """
    # ?곗빱 蹂??
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.add_holding(user_id, resolved_ticker, quantity, buy_price, name)
    return {"message": f"{resolved_ticker} 異붽? ?꾨즺", "holdings": holdings}

@router.delete("/{user_id}/{ticker}")
def remove_holding(user_id: str, ticker: str):
    """
    蹂댁쑀 醫낅ぉ???쒓굅?⑸땲??
    """
    holdings = PortfolioService.remove_holding(user_id, ticker)
    return {"message": f"{ticker} ?쒓굅 ?꾨즺", "holdings": holdings}

@router.post("/{user_id}/trade")
def trade_holding(
    user_id: str,
    ticker: str,
    action: str,
    quantity: float,
    price: float
):
    """
    二쇱떇 留ㅼ닔/留ㅻ룄 ?듯빀 泥섎━
    - action: 'buy' ?먮뒗 'sell'
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
