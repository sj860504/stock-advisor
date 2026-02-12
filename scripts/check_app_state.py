import sys
import os

# 프로젝트 루트 경로 추가
sys.path.append('/Users/a10941/workspace/007_private/003_quant')

from services.market.market_data_service import MarketDataService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.market_hour_service import MarketHourService
import pytz
from datetime import datetime

def debug_status():
    print(f"--- System Status Check ---")
    tz = pytz.timezone('Asia/Seoul')
    now = datetime.now(tz)
    print(f"Current Seoul Time: {now}")
    print(f"KR Market Open: {MarketHourService.is_kr_market_open()}")
    print(f"US Market Open: {MarketHourService.is_us_market_open()}")
    
    states = MarketDataService.get_all_states()
    print(f"Registered Tickers: {len(states)}")
    for ticker, state in list(states.items())[:5]:
        print(f" - {ticker}: Price={state.current_price}, RSI={state.rsi}")
        
    print(f"Strategy Enabled: {TradingStrategyService.is_enabled()}")
    
if __name__ == "__main__":
    debug_status()
