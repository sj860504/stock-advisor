from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional, List, Dict, Any
from services.trading.portfolio_service import PortfolioService
from services.market.ticker_service import TickerService
from services.base.scheduler_service import SchedulerService
from models.schemas import PortfolioUploadResponse, PortfolioListResponse, HoldingActionResponse

router = APIRouter(
    prefix="/portfolio",
    tags=["Portfolio"]
)


@router.post("/upload", response_model=PortfolioUploadResponse)
async def upload_portfolio(
    file: UploadFile = File(...),
    user_id: str = Form(default="default"),
) -> PortfolioUploadResponse:
    """Upload Excel file to register portfolio."""
    try:
        content = await file.read()
        holdings = PortfolioService.upload_portfolio(content, file.filename, user_id)
        return PortfolioUploadResponse(
            message=f"Portfolio upload successful! {len(holdings)} tickers registered",
            holdings=holdings,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{user_id}", response_model=PortfolioListResponse)
def get_portfolio(user_id: str = "default") -> PortfolioListResponse:
    """Get saved portfolio."""
    holdings = PortfolioService.load_portfolio(user_id)
    if not holdings:
        return PortfolioListResponse(message="No registered portfolio found.", holdings=[])
    return PortfolioListResponse(holdings=holdings)


@router.post("/{user_id}/sync")
def sync_portfolio_with_kis(user_id: str = "sean") -> Dict[str, Any]:
    """Sync portfolio with KIS actual balance."""
    try:
        holdings = PortfolioService.sync_with_kis(user_id)
        return {
            "status": "success",
            "message": f"KIS 동기화 완료 ({len(holdings)}종목)",
            "holdings_count": len(holdings),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"KIS 동기화 실패: {str(e)}")


@router.get("/{user_id}/analysis", response_model=Dict[str, Any])
def analyze_portfolio(user_id: str = "default") -> Dict[str, Any]:
    """Analyze portfolio returns."""
    price_cache = SchedulerService.get_all_cached_prices()
    return PortfolioService.analyze_portfolio(user_id, price_cache)


@router.get("/{user_id}/full-report", response_model=Dict[str, Any])
def get_full_portfolio_report(user_id: str = "default") -> Dict[str, Any]:
    """Return detailed analysis data for all holdings with exchange rate."""
    from services.market.macro_service import MacroService
    price_cache = SchedulerService.get_all_cached_prices()
    holdings = PortfolioService.build_full_report(user_id, price_cache)
    from services.strategy.trading_strategy_service import TradingStrategyService
    user_state_dict = TradingStrategyService._load_state(user_id if user_id != "default" else "sean")
    user_state = user_state_dict.get(user_id if user_id != "default" else "sean")
    
    sell_cd = user_state.sell_cooldown if user_state else {}
    buy_cd = user_state.add_buy_cooldown if user_state else {}

    return {
        "holdings": holdings,
        "exchange_rate": MacroService.get_exchange_rate(),
        "cooldown": {
            "sell": sell_cd,
            "buy": {k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in buy_cd.items()}
        },
    }


@router.post("/{user_id}/add", response_model=HoldingActionResponse)
def add_holding(
    user_id: str,
    ticker: str,
    quantity: float,
    buy_price: float,
    name: Optional[str] = None,
) -> HoldingActionResponse:
    """Manually add a holding."""
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.add_holding_manual(user_id, resolved_ticker, quantity, buy_price, name)
    return HoldingActionResponse(message=f"{resolved_ticker} added", holdings=holdings)


@router.delete("/{user_id}/{ticker}", response_model=HoldingActionResponse)
def remove_holding(user_id: str, ticker: str) -> HoldingActionResponse:
    """Remove a holding."""
    from repositories.portfolio_repo import PortfolioRepo
    holdings_raw = PortfolioRepo.load_holdings(user_id)
    new_holdings = [h for h in holdings_raw if h.get("ticker") != ticker]
    PortfolioService.save_portfolio(user_id, new_holdings)
    return HoldingActionResponse(message=f"{ticker} removed", holdings=new_holdings)


@router.post("/{user_id}/trade", response_model=HoldingActionResponse)
def trade_holding(
    user_id: str, ticker: str, action: str, quantity: float, price: float,
) -> HoldingActionResponse:
    """Unified buy/sell processing. action: 'buy' or 'sell'."""
    resolved_ticker = TickerService.resolve_ticker(ticker)
    from repositories.portfolio_repo import PortfolioRepo
    holdings = PortfolioRepo.load_holdings(user_id)
    try:
        holdings = PortfolioService.apply_trade_action(holdings, resolved_ticker, action, quantity, price)
        PortfolioService.save_portfolio(user_id, holdings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return HoldingActionResponse(
        message=f"{resolved_ticker} {action.upper()} completed", holdings=holdings,
    )
