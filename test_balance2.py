import sys, os, pprint, asyncio
from routers.trading import get_balance
from config import Config
Config.init_config()

async def main():
    res = await get_balance()
    print("total_eval:", res.get("total_eval"))
    print("cash_kr:", res.get("cash_kr"))
    print("cash_us:", res.get("cash_us"))
    print("profit_loss:", res.get("profit_loss"))

asyncio.run(main())
