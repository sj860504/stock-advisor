from fastapi import APIRouter, HTTPException, Body, Query
from services.kis.kis_service import KisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.strategy.backtest_service import BacktestService
from services.trading.order_service import OrderService
from services.trading.portfolio_service import PortfolioService
from services.config.settings_service import SettingsService
from typing import Dict, List, Any, Optional
from utils.logger import get_logger
from models.schemas import (
    OrderRequest, TickTradingSettingsRequest,
    StatusMessageResponse, SettingUpdateResponse,
    TickSettingsResponse, TickSettingsUpdateResponse, SellAllRebuResponse,
    TradeRecordDto,
)

logger = get_logger("trading_router")

router = APIRouter(prefix="/trading", tags=["trading"])


def _build_tick_updates_from_payload(payload: TickTradingSettingsRequest) -> Dict[str, str]:
    """Convert tick trading settings payload to key-value dict for DB storage."""
    updates: Dict[str, str] = {}
    if payload.enabled is not None:
        updates["STRATEGY_TICK_ENABLED"] = "1" if payload.enabled else "0"
    if payload.ticker is not None:
        updates["STRATEGY_TICK_TICKER"] = payload.ticker.strip().upper()
    if payload.cash_ratio is not None:
        updates["STRATEGY_TICK_CASH_RATIO"] = str(payload.cash_ratio)
    if payload.entry_pct is not None:
        updates["STRATEGY_TICK_ENTRY_PCT"] = str(payload.entry_pct)
    if payload.add_pct is not None:
        updates["STRATEGY_TICK_ADD_PCT"] = str(payload.add_pct)
    if payload.take_profit_pct is not None:
        updates["STRATEGY_TICK_TAKE_PROFIT_PCT"] = str(payload.take_profit_pct)
    if payload.stop_loss_pct is not None:
        updates["STRATEGY_TICK_STOP_LOSS_PCT"] = str(payload.stop_loss_pct)
    if payload.close_minutes is not None:
        updates["STRATEGY_TICK_CLOSE_MINUTES"] = str(payload.close_minutes)
    return updates


@router.post("/order", response_model=Dict[str, Any])
async def place_order(order: OrderRequest) -> Dict[str, Any]:
    """Place stock buy/sell order."""
    try:
        result = KisService.send_order(order.ticker, order.quantity, order.price, order.order_type)
        if result.get("status") == "success":
            return result
        raise HTTPException(status_code=400, detail=result.get("msg", "Order failed"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/balance", response_model=Dict[str, Any])
async def get_balance() -> Dict[str, Any]:
    """Query stock balance."""
    try:
        from services.trading.portfolio_service import PortfolioService
        from services.base.scheduler_service import SchedulerService
        kis_balance = KisService.get_balance() or {}
        
        # user_id is hardcoded to "sean" in several places, use as default to fetch portfolio analysis
        analysis = PortfolioService.analyze_portfolio("sean", SchedulerService.get_all_cached_prices())
        
        total_eval = (
            analysis.get("summary", {}).get("total_current", 0) + 
            analysis.get("kr", {}).get("cash", 0) + 
            analysis.get("us", {}).get("cash_krw", 0)
        )
        
        from repositories.trade_history_repo import TradeHistoryRepo
        from utils.market import is_kr as _is_kr
        pending_trades = TradeHistoryRepo.get_pending_orders()
        pending_list = []
        pending_buy_krw = 0.0
        pending_buy_usd = 0.0
        pending_sell_krw = 0.0
        pending_sell_usd = 0.0
        for t in pending_trades:
            amt = t.quantity * t.price
            is_kr_ticker = _is_kr(t.ticker)
            if t.order_type == "buy":
                if is_kr_ticker:
                    pending_buy_krw += amt
                else:
                    pending_buy_usd += amt
            else:
                if is_kr_ticker:
                    pending_sell_krw += amt
                else:
                    pending_sell_usd += amt
            pending_list.append({
                "id": t.id, "ticker": t.ticker, "order_type": t.order_type,
                "quantity": t.quantity, "price": t.price,
                "timestamp": t.timestamp.strftime("%H:%M:%S") if t.timestamp else "",
            })

        return {
            "total_eval": total_eval,
            "cash_kr": analysis.get("kr", {}).get("cash", 0),
            "cash_us": analysis.get("us", {}).get("cash_usd", 0),
            "profit_loss": analysis.get("summary", {}).get("profit", 0),
            "holdings": analysis.get("holdings", []),
            "analysis": analysis,
            "summary": kis_balance.get("summary", []),
            "pending": {
                "orders": pending_list,
                "buy_krw": pending_buy_krw,
                "buy_usd": pending_buy_usd,
                "sell_krw": pending_sell_krw,
                "sell_usd": pending_sell_usd,
                "count": len(pending_list),
            },
        }
    except Exception as e:
        logger.error(f"Error fetching balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/waiting-list", response_model=Dict[str, Any])
async def get_waiting_list() -> Dict[str, Any]:
    """Get trade waiting list (BUY/SELL signals)."""
    try:
        return TradingStrategyService.get_waiting_list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=List[TradeRecordDto])
async def get_trade_history(
    limit: int = Query(default=50, ge=1, le=1000),
    market: Optional[str] = None,
    date: Optional[str] = None,
    action: Optional[str] = None,
) -> List[TradeRecordDto]:
    """Get trade history. market=kr/us, action=buy/sell, date=YYYY-MM-DD."""
    try:
        return OrderService.get_trade_history(limit, market=market, date=date, action=action)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell", response_model=Dict[str, Any])
async def execute_sell(
    ticker: str = Body(..., embed=True),
    quantity: int = Body(0, embed=True),
) -> Dict[str, Any]:
    """Execute sell (quantity 0 triggers full or split sell per strategy)."""
    try:
        return TradingStrategyService.execute_sell(ticker, quantity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings", response_model=List[Dict[str, Any]])
async def get_settings() -> List[Dict[str, Any]]:
    """Get settings."""
    try:
        return SettingsService.get_all_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings", response_model=SettingUpdateResponse)
async def update_setting(
    key: str = Body(..., embed=True),
    value: str = Body(..., embed=True),
) -> SettingUpdateResponse:
    """Update setting."""
    try:
        result = SettingsService.set_setting(key, value)
        if result:
            return SettingUpdateResponse(status="success", key=result.key, value=result.value)
        raise HTTPException(status_code=400, detail="Failed to update setting")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/start", response_model=StatusMessageResponse)
async def start_trading() -> StatusMessageResponse:
    """Start auto-trading (enable strategy)."""
    try:
        TradingStrategyService.set_enabled(True)
        return StatusMessageResponse(status="success", message="Trading Strategy Started")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stop", response_model=StatusMessageResponse)
async def stop_trading() -> StatusMessageResponse:
    """Stop auto-trading (disable strategy)."""
    try:
        TradingStrategyService.set_enabled(False)
        return StatusMessageResponse(status="success", message="Trading Strategy Stopped")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tick-settings", response_model=TickSettingsResponse)
async def get_tick_settings() -> TickSettingsResponse:
    """Get tick trading settings."""
    try:
        return TickSettingsResponse(**SettingsService.get_tick_settings())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/tick-settings", response_model=TickSettingsUpdateResponse)
async def update_tick_settings(payload: TickTradingSettingsRequest) -> TickSettingsUpdateResponse:
    """Update tick trading settings."""
    try:
        updates = _build_tick_updates_from_payload(payload)
        SettingsService.update_tick_settings(updates)
        return TickSettingsUpdateResponse(status="success", updated=updates)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest/portfolio", response_model=Dict[str, Any])
async def run_portfolio_backtest(
    years: int = Body(2, ge=1, le=3),
    initial_capital: float = Body(10_000_000),
) -> Dict[str, Any]:
    """Run portfolio-level backtest with multi-ticker RSI strategy."""
    try:
        return BacktestService.run_portfolio_backtest(
            years=years, initial_capital=initial_capital
        )
    except Exception as e:
        logger.error(f"Portfolio backtest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell-all-and-rebuy", response_model=SellAllRebuResponse)
async def sell_all_and_rebuy() -> SellAllRebuResponse:
    """Sell all holdings then re-buy per strategy."""
    try:
        result = TradingStrategyService.sell_all_and_rebuy()
        return SellAllRebuResponse(**result)
    except Exception as e:
        logger.error(f"❌ sell_all_and_rebuy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
