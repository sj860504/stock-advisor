import os
import sys
import time
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market.data_service import DataService
from services.market.macro_service import MacroService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.trading.portfolio_service import PortfolioService
from services.analysis.indicator_service import IndicatorService
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher

class TickerState:
    def __init__(self, price, rsi, ema, change_rate=0):
        self.current_price = price
        self.rsi = rsi
        self.ema = ema
        self.change_rate = change_rate

def scan_kr_market():
    print(f"🔍 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting KR market blue-chip scan (VTS mode)...")
    
    # 1. 기초 데이터 확보
    macro_data = MacroService.get_macro_data()
    # 랭킹 API가 불안정할 수 있으므로 상위 5개 우량주 직접 지정
    tickers = ["005930", "000660", "373220", "207940", "005380"]
    print(f"✅ Starting analysis of {len(tickers)} core tickers. (Delays due to API rate limits)")

    token = KisService.get_access_token()
    total_assets = 100000000.0
    cash_balance = 100000000.0
    opportunities = []
    
    for ticker in tickers:
        try:
            # A. 현재가 조회
            time.sleep(1.2) # API 제한 준수
            price_info = KisFetcher.fetch_domestic_price(token, ticker)
            if not price_info: continue
            
            curr_price = price_info.get('price', 0)
            change_rate = price_info.get('change_rate', 0)
            
            # B. 기술적 지표 조회
            time.sleep(1.2) # API 제한 준수
            hist = DataService.get_price_history(ticker, days=250)
            if hist.empty:
                print(f"⚠️ Insufficient data: {ticker}")
                continue
            
            indicators = IndicatorService.get_latest_indicators(hist['Close'])
            rsi = indicators.get('rsi', 50)
            ema = indicators.get('ema', {})
            
            # C. 전략 분석
            state = TickerState(curr_price, rsi, ema, change_rate)
            result = TradingStrategyService.analyze_ticker(
                ticker=ticker, state=state, holding=None, macro=macro_data,
                user_state={}, total_assets=total_assets, cash_balance=cash_balance, exchange_rate=1.0
            )
            
            result['name'] = price_info.get('name', ticker)
            opportunities.append(result)
            print(f"📊 {result['name']} analysis complete (score: {result['score']})")
            
        except Exception as e:
            print(f"❌ {ticker} analysis error: {e}")
            continue
            
    print("\n" + "="*50)
    print("📊 [KR Market Trading Algorithm Analysis Final Report]")
    print("="*50)
    
    opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    for o in opportunities:
        recommend = "🟢 Buy recommended" if o['score'] >= 75 else "⚪ Hold/Watch"
        if o['score'] <= 25: recommend = "🔴 Sell/Caution"
        
        print(f"[{recommend}] {o['name']} ({o['ticker']})")
        print(f"  - Total score: {o['score']} pts / 100 pts")
        print(f"  - Current price: {o['current_price']:,} KRW (RSI: {o['rsi']:.1f})")
        print(f"  - Trade reason: {', '.join(o['reasons']) if o['reasons'] else 'Nothing notable'}")
        print("-" * 30)

if __name__ == "__main__":
    scan_kr_market()
