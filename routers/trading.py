from fastapi import APIRouter, HTTPException, Body, Query
from services.kis.kis_service import KisService
from services.strategy.trading_strategy_service import TradingStrategyService
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
    """틱매매 설정 payload를 DB 저장용 key-value dict로 변환합니다."""
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
    """주식 매수/매도 주문."""
    try:
        result = KisService.send_order(order.ticker, order.quantity, order.price, order.order_type)
        if result.get("status") == "success":
            return result
        raise HTTPException(status_code=400, detail=result.get("msg", "Order failed"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/balance", response_model=Dict[str, Any])
async def get_balance() -> Dict[str, Any]:
    """주식 잔고 조회."""
    try:
        balance = KisService.get_balance()
        if balance:
            return balance
        raise HTTPException(status_code=400, detail="Failed to fetch balance")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/waiting-list", response_model=Dict[str, Any])
async def get_waiting_list() -> Dict[str, Any]:
    """매매 대기 목록 조회 (BUY/SELL 시그널)."""
    try:
        return TradingStrategyService.get_waiting_list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history", response_model=List[TradeRecordDto])
async def get_trade_history(
    limit: int = Query(default=50, ge=1, le=1000),
    market: Optional[str] = None,
    date: Optional[str] = None,
) -> List[TradeRecordDto]:
    """매매 내역 조회. market=kr/us(미지정시 전체), date=YYYY-MM-DD(미지정시 전체)."""
    try:
        return OrderService.get_trade_history(limit, market=market, date=date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell", response_model=Dict[str, Any])
async def execute_sell(
    ticker: str = Body(..., embed=True),
    quantity: int = Body(0, embed=True),
) -> Dict[str, Any]:
    """매도 실행 (수량 0 입력 시 전략에 따라 전량 또는 분할 매도)."""
    try:
        return TradingStrategyService.execute_sell(ticker, quantity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings", response_model=List[Dict[str, Any]])
async def get_settings() -> List[Dict[str, Any]]:
    """설정 조회."""
    try:
        return SettingsService.get_all_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings", response_model=SettingUpdateResponse)
async def update_setting(
    key: str = Body(..., embed=True),
    value: str = Body(..., embed=True),
) -> SettingUpdateResponse:
    """설정 변경."""
    try:
        result = SettingsService.set_setting(key, value)
        if result:
            return SettingUpdateResponse(status="success", key=result.key, value=result.value)
        raise HTTPException(status_code=400, detail="Failed to update setting")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/start", response_model=StatusMessageResponse)
async def start_trading() -> StatusMessageResponse:
    """자동 매매 시작 (전략 활성화)."""
    try:
        TradingStrategyService.set_enabled(True)
        return StatusMessageResponse(status="success", message="Trading Strategy Started")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stop", response_model=StatusMessageResponse)
async def stop_trading() -> StatusMessageResponse:
    """자동 매매 중지 (전략 비활성화)."""
    try:
        TradingStrategyService.set_enabled(False)
        return StatusMessageResponse(status="success", message="Trading Strategy Stopped")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tick-settings", response_model=TickSettingsResponse)
async def get_tick_settings() -> TickSettingsResponse:
    """틱매매 설정 조회."""
    try:
        return TickSettingsResponse(**SettingsService.get_tick_settings())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/tick-settings", response_model=TickSettingsUpdateResponse)
async def update_tick_settings(payload: TickTradingSettingsRequest) -> TickSettingsUpdateResponse:
    """틱매매 설정 변경."""
    try:
        updates = _build_tick_updates_from_payload(payload)
        SettingsService.update_tick_settings(updates)
        return TickSettingsUpdateResponse(status="success", updated=updates)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell-all-and-rebuy", response_model=SellAllRebuResponse)
async def sell_all_and_rebuy() -> SellAllRebuResponse:
    """보유 종목 전량 매도 후 전략대로 재매수."""
    try:
        result = TradingStrategyService.sell_all_and_rebuy()
        return SellAllRebuResponse(**result)
    except Exception as e:
        logger.error(f"❌ sell_all_and_rebuy 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
