import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.stock_meta_service import StockMetaService

def check_now():
    print(f"🔍 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Analyzing current buy-candidate tickers...")
    
    opps = TradingStrategyService.get_opportunities(user_id="sean")
    
    if not opps:
        print("✅ No buy-candidate tickers found by the algorithm. (RSI, macro indicators did not meet conditions)")
        return

    print(f"\n🚀 [Buy Opportunities Detected: {len(opps)} tickers]")
    print("-" * 60)
    for o in opps:
        # 종목명 조회
        meta = StockMetaService.get_stock_meta(o['ticker'])
        name = meta.name_ko if meta else o['ticker']
        
        print(f"⭐ {name} ({o['ticker']})")
        print(f"  - Total score: {o['score']} pts")
        print(f"  - Current price: {o['current_price']:,} KRW (RSI: {o['rsi']:.1f})")
        print(f"  - Buy reason: {', '.join(o['reasons'])}")
        print("-" * 60)

if __name__ == "__main__":
    check_now()
