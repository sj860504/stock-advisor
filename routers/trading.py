from fastapi import APIRouter, HTTPException
from services.kis_service import KisService
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
    二쇱떇 留ㅼ닔/留ㅻ룄 二쇰Ц
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
    二쇱떇 ?붽퀬 議고쉶
    """
    try:
        balance = KisService.get_balance()
        if balance:
            return balance
        else:
            raise HTTPException(status_code=400, detail="Failed to fetch balance")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
