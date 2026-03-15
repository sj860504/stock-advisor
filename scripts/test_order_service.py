import os
import sys
import asyncio
from pprint import pprint

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.trading.order_service import OrderService

async def test_order_service():
    print("Testing OrderService...")
    history = OrderService.get_trade_history(limit=5)
    print(f"Got {len(history)} items")
    if history:
        pprint(history[0].model_dump())

if __name__ == "__main__":
    asyncio.run(test_order_service())
