import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.market.trading_economics_service import TradingEconomicsService

latest, prev = TradingEconomicsService.get_ism_services_pmi()
print(f"Latest: {latest}, Previous: {prev}")
