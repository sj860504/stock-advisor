import asyncio
from services.trading.portfolio_service import PortfolioService
from services.base.scheduler_service import SchedulerService
from config import Config

async def main():
    Config.init()
    prices = SchedulerService.get_all_cached_prices()
    try:
        res = PortfolioService.analyze_portfolio("sean", prices)
        print("SUCCESS")
    except Exception as e:
        print("ERROR:", e)

asyncio.run(main())
