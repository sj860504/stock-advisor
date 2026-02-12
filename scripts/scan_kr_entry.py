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
    print(f"🔍 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 한국 시장 핵심 우량주 스캔 시작 (VTS 대응 모드)...")
    
    # 1. 기초 데이터 확보
    macro_data = MacroService.get_macro_data()
    # 랭킹 API가 불안정할 수 있으므로 상위 5개 우량주 직접 지정
    tickers = ["005930", "000660", "373220", "207940", "005380"]
    print(f"✅ {len(tickers)}개의 핵심 종목 분석을 시작합니다. (호출 제한 준수를 위해 지연 발생)")

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
                print(f"⚠️ 데이터 부족: {ticker}")
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
            print(f"📊 {result['name']} 분석 완료 (점수: {result['score']})")
            
        except Exception as e:
            print(f"❌ {ticker} 분석 중 오류: {e}")
            continue
            
    print("\n" + "="*50)
    print("📊 [한국 시장 매매 알고리즘 분석 최종 보고]")
    print("="*50)
    
    opportunities.sort(key=lambda x: x['score'], reverse=True)
    
    for o in opportunities:
        recommend = "🟢 매수 권장" if o['score'] >= 75 else "⚪ 관망"
        if o['score'] <= 25: recommend = "🔴 매도/주의"
        
        print(f"[{recommend}] {o['name']} ({o['ticker']})")
        print(f"  - 종합 점수: {o['score']}점 / 100점")
        print(f"  - 현재가: {o['current_price']:,}원 (RSI: {o['rsi']:.1f})")
        print(f"  - 매매 사유: {', '.join(o['reasons']) if o['reasons'] else '특이사항 없음'}")
        print("-" * 30)

if __name__ == "__main__":
    scan_kr_market()
