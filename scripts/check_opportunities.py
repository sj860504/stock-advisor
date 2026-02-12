import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy.trading_strategy_service import TradingStrategyService
from services.market.stock_meta_service import StockMetaService

def check_now():
    print(f"🔍 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 현재 매수 대기 종목 분석 중...")
    
    opps = TradingStrategyService.get_opportunities(user_id="sean")
    
    if not opps:
        print("✅ 현재 알고리즘 기준 매수 대기 종목이 없습니다. (RSI, 거시 지표가 조건을 충족하지 않음)")
        return

    print(f"\n🚀 [매수 기회 감지: {len(opps)}개 종목]")
    print("-" * 60)
    for o in opps:
        # 종목명 조회
        meta = StockMetaService.get_stock_meta(o['ticker'])
        name = meta.name_ko if meta else o['ticker']
        
        print(f"⭐ {name} ({o['ticker']})")
        print(f"  - 종합 점수: {o['score']}점")
        print(f"  - 현재 가격: {o['current_price']:,}원 (RSI: {o['rsi']:.1f})")
        print(f"  - 매수 사유: {', '.join(o['reasons'])}")
        print("-" * 60)

if __name__ == "__main__":
    check_now()
