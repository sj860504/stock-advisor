import sys
import os

# Add workspace to path
sys.path.append(os.getcwd())

try:
    from stock_advisor.services.portfolio_service import PortfolioService
    from stock_advisor.services.ticker_service import TickerService
    from stock_advisor.services.data_service import DataService
    print("Imports successful")
except Exception as e:
    print(f"Import failed: {e}")

# Test Ticker Resolution
print(f"Resolving 'samsung': {TickerService.resolve_ticker('삼성전자')}") # 'samsung' in json was name 'samsung', likely '삼성전자'
print(f"Resolving 'ACE 미국빅테크TOP7 Plus': {TickerService.resolve_ticker('ACE 미국빅테크TOP7 Plus')}") 
# Note: The JSON had 'ACE빅테크', I need to guess the full name or check if 'ACE빅테크' is in KRX listing.
