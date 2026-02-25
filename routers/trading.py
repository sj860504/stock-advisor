from fastapi import APIRouter, HTTPException, Body
from services.kis.kis_service import KisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.trading.order_service import OrderService
from services.trading.portfolio_service import PortfolioService
from services.config.settings_service import SettingsService
from typing import Dict, List, Any, Optional, Tuple
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


def _sell_single_holding(
    ticker: str, name: str, quantity: int, current_price: float
) -> Tuple[bool, str]:
    """단일 종목 매도를 실행하고 (성공 여부, 오류 메시지)를 반환합니다."""
    is_us = not ticker.isdigit()
    if is_us:
        if current_price <= 0:
            return False, f"{ticker} 현재가 정보 없음"
        res = KisService.send_overseas_order(
            ticker=ticker, quantity=quantity,
            price=round(float(current_price), 2), order_type="sell",
        )
    else:
        res = KisService.send_order(ticker, quantity, 0, "sell")

    if res.get("status") == "success":
        return True, ""
    return False, res.get("msg", "Unknown error")


def _execute_mass_sell(holdings: list) -> Tuple[int, int, List[str]]:
    """보유 종목 전량 매도를 실행하고 (성공수, 실패수, 실패_티커_목록)을 반환합니다."""
    success_count, fail_count, failed_tickers = 0, 0, []
    for holding in holdings:
        ticker = holding["ticker"]
        name = holding.get("name", ticker)
        quantity = holding["quantity"]
        if quantity <= 0:
            continue
        logger.info(f"📤 {ticker} ({name}) {quantity}주 매도 시도...")
        try:
            ok, err = _sell_single_holding(ticker, name, quantity, holding.get("current_price", 0))
            if ok:
                logger.info(f"✅ {ticker} ({name}) {quantity}주 매도 성공")
                success_count += 1
            else:
                logger.error(f"❌ {ticker} 매도 실패: {err}")
                fail_count += 1
                failed_tickers.append(ticker)
        except Exception as e:
            logger.error(f"❌ {ticker} 매도 중 오류: {e}")
            fail_count += 1
            failed_tickers.append(ticker)
    return success_count, fail_count, failed_tickers


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
    limit: int = 50,
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
        return TickSettingsResponse(
            enabled=SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) == 1,
            ticker=SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930"),
            cash_ratio=SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.20),
            entry_pct=SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0),
            add_pct=SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0),
            take_profit_pct=SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0),
            stop_loss_pct=SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0),
            close_minutes=SettingsService.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/tick-settings", response_model=TickSettingsUpdateResponse)
async def update_tick_settings(payload: TickTradingSettingsRequest) -> TickSettingsUpdateResponse:
    """틱매매 설정 변경."""
    try:
        updates = _build_tick_updates_from_payload(payload)
        for key, value in updates.items():
            SettingsService.set_setting(key, value)
        return TickSettingsUpdateResponse(status="success", updated=updates)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run_strategy_after_sell(
    user_id: str, success_count: int, fail_count: int, failed_tickers: List[str]
) -> SellAllRebuResponse:
    """매도 완료 후 전략을 실행하고 결과 응답을 반환합니다."""
    try:
        TradingStrategyService.run_strategy(user_id)
        logger.info("✅ 전략 실행 완료")
        return SellAllRebuResponse(
            status="success",
            message=f"전량 매도 및 전략 재매수 완료 (매도 성공: {success_count}, 실패: {fail_count})",
            sold=success_count, failed=fail_count, failed_tickers=failed_tickers or None,
        )
    except Exception as e:
        logger.error(f"❌ 전략 실행 중 오류: {e}")
        return SellAllRebuResponse(
            status="partial",
            message=f"매도 완료 (성공: {success_count}, 실패: {fail_count}), 전략 실행 실패",
            sold=success_count, failed=fail_count,
            failed_tickers=failed_tickers or None, strategy_error=str(e),
        )


@router.post("/sell-all-and-rebuy", response_model=SellAllRebuResponse)
async def sell_all_and_rebuy() -> SellAllRebuResponse:
    """보유 종목 전량 매도 후 전략대로 재매수."""
    try:
        user_id = "sean"
        logger.info("🔄 보유 종목 전량 매도 후 전략 재매수 시작")
        holdings = PortfolioService.sync_with_kis(user_id)
        if not holdings:
            return SellAllRebuResponse(status="success", message="보유 종목이 없습니다.", sold=0, failed=0)
        logger.info(f"📊 보유 종목 {len(holdings)}개 확인")
        success_count, fail_count, failed_tickers = _execute_mass_sell(holdings)
        PortfolioService.sync_with_kis(user_id)
        return _run_strategy_after_sell(user_id, success_count, fail_count, failed_tickers)
    except Exception as e:
        logger.error(f"❌ sell_all_and_rebuy 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
