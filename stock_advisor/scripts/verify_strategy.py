import os
import sys

# 프로젝트 루트를 경로에 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import asyncio
from stock_advisor.services.trading_strategy_service import TradingStrategyService
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.macro_service import MacroService

def test_strategy():
    print("🧪 Testing Trading Strategy Service...")
    
    # 1. 포트폴리오 로드 테스트
    user_id = 'sean'
    portfolio = PortfolioService.load_portfolio(user_id)
    print(f"📊 Loaded {len(portfolio)} holdings for {user_id}")
    
    # 2. 마켓 데이터/매크로 데이터 조회 테스트
    macro = MacroService.get_macro_data()
    print(f"🌐 Macro Status: {macro.get('market_regime', {}).get('status')}")
    print(f"📈 S&P500 Price: {macro.get('indices', {}).get('S&P500', {}).get('price')}")
    
    # 3. 전략 실행 시뮬레이션
    print("\n🔍 Running Strategy Logic...")
    TradingStrategyService.run_strategy(user_id)
    print("\n✅ Strategy execution finished. Check logs for signals.")

if __name__ == "__main__":
    # 환경변수 로드 확인
    if not os.getenv("KIS_APP_KEY"):
        from dotenv import load_dotenv
        load_dotenv()
        
    test_strategy()
