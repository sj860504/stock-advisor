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
print(f"Resolving 'samsung': {TickerService.resolve_ticker('?쇱꽦?꾩옄')}") # 'samsung' in json was name 'samsung', likely '?쇱꽦?꾩옄'
print(f"Resolving 'ACE 誘멸뎅鍮낇뀒?촖OP7 Plus': {TickerService.resolve_ticker('ACE 誘멸뎅鍮낇뀒?촖OP7 Plus')}") 
# Note: The JSON had 'ACE鍮낇뀒??, I need to guess the full name or check if 'ACE鍮낇뀒?? is in KRX listing.
