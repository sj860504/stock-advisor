import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market.market_data_service import MarketDataService

# 전체 상태 확인
all_states = MarketDataService.get_all_states()
ready_count = sum(1 for state in all_states.values() if state.is_ready)

print(f"📊 Total stocks: {len(all_states)}")
print(f"✅ Ready stocks: {ready_count}")
print(f"⏳ Not ready: {len(all_states) - ready_count}")

# 샘플 5개 출력
print("\n=== Sample Ready Stocks ===")
count = 0
for ticker, state in all_states.items():
    if state.is_ready and count < 5:
        print(f"  {ticker}: Price={state.current_price}, RSI={state.rsi}, EMA60={state.ema.get(60)}, EMA200={state.ema.get(200)}")
        count += 1
