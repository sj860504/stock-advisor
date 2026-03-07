import sys
import os

# Add workspace to path
sys.path.append(os.getcwd())

try:
    from services.trading.portfolio_service import PortfolioService
    from services.market.ticker_service import TickerService
    from services.market.data_service import DataService
    print("Imports successful")
except Exception as e:
    print(f"Import failed: {e}")

# Test Ticker Resolution
print(f"Resolving 'samsung': {TickerService.resolve_ticker('005930')}")
print(f"Resolving 'ACE BigTech': {TickerService.resolve_ticker('ACE BigTech')}")
