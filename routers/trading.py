from fastapi import APIRouter, HTTPException, Body
from services.kis.kis_service import KisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.trading.order_service import OrderService
from services.config.settings_service import SettingsService
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/trading", tags=["trading"])

class OrderRequest(BaseModel):
    ticker: str
    quantity: int
    price: int = 0
    order_type: str = "buy"

@router.post("/order")
async def place_order(order: OrderRequest):
    """
    주식 매수/매도 주문 (기존 endpoints)
    """
    try:
        result = KisService.send_order(order.ticker, order.quantity, order.price, order.order_type)
        if result['status'] == 'success':
            return result
        else:
            raise HTTPException(status_code=400, detail=result.get('msg', 'Order failed'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/balance")
async def get_balance():
    """
    주식 잔고 조회
    """
    try:
        balance = KisService.get_balance()
        if balance:
            return balance
        else:
            raise HTTPException(status_code=400, detail="Failed to fetch balance")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/waiting-list")
async def get_waiting_list():
    """
    매매 대기 목록 조회 (BUY/SELL 시그널)
    """
    try:
        return TradingStrategyService.get_waiting_list()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_trade_history(limit: int = 50):
    """
    매매 내역 조회
    """
    try:
        return OrderService.get_trade_history(limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sell")
async def execute_sell(ticker: str = Body(..., embed=True), quantity: int = Body(0, embed=True)):
    """
    매도 실행 (수량 0 입력 시 전략에 따라 전량 또는 분할 매도)
    """
    try:
        return TradingStrategyService.execute_sell(ticker, quantity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings")
async def get_settings():
    """
    설정 조회
    """
    try:
        return SettingsService.get_all_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/settings")
async def update_setting(key: str = Body(..., embed=True), value: str = Body(..., embed=True)):
    """
    설정 변경
    """
    try:
        result = SettingsService.set_setting(key, value)
        if result:
            return {"status": "success", "key": result.key, "value": result.value}
        else:
            raise HTTPException(status_code=400, detail="Failed to update setting")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/start")
async def start_trading():
    """
    자동 매매 시작 (전략 활성화)
    """
    try:
        TradingStrategyService.set_enabled(True)
        return {"status": "success", "message": "Trading Strategy Started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stop")
async def stop_trading():
    """
    자동 매매 중지 (전략 비활성화)
    """
    try:
        TradingStrategyService.set_enabled(False)
        return {"status": "success", "message": "Trading Strategy Stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
