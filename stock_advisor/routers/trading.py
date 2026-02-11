from fastapi import APIRouter, HTTPException
from stock_advisor.services.kis_service import KisService
from pydantic import BaseModel

router = APIRouter(prefix="/trading", tags=["trading"])

class OrderRequest(BaseModel):
    ticker: str
    quantity: int
    price: int = 0
    order_type: str = "buy"

@router.post("/order")
async def place_order(order: OrderRequest):
    """
    주식 매수/매도 주문
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
