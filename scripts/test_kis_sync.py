import os
import sys
import pandas as pd
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market.data_service import DataService
from services.market.stock_meta_service import StockMetaService
from services.kis.kis_service import KisService
from utils.logger import get_logger

logger = get_logger("test_sync")

def test_single_sync(ticker, market):
    print(f"\n🧪 Testing Sync for {ticker} ({market})...")
    try:
        # 1. 메타 정보 초기화 (없을 경우 대비)
        StockMetaService.initialize_default_meta(ticker)
        
        # 2. 개별 종목 동기화 로직 수행 (sync_daily_market_data의 내부 로직 일부 추출 테스트)
        # 실제 sync_daily_market_data를 부르면 시총 100위 조회를 하므로, 특정 종목만 테스트하기 위해 함수를 모방함
        
        # A. 시세 조회 테스트
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = KisService.get_access_token()
        
        if market == "KR":
            res_price = KisFetcher.fetch_domestic_price(token, ticker)
        else:
            res_price = KisFetcher.fetch_overseas_price(token, ticker)
        
        if not res_price:
            print(f"❌ Failed to fetch price for {ticker}")
            return False
            
        print(f"✅ Price fetched: {res_price.get('price')} (Name: {res_price.get('name')})")
        print(f"📊 Indicators (from API): PER={res_price.get('per')}, PBR={res_price.get('pbr')}")

        # B. 히스토리 및 기술적 지표 테스트
        hist = DataService.get_price_history(ticker, days=365)
        if hist.empty:
            print(f"❌ Failed to fetch history for {ticker}")
            return False
        
        print(f"✅ History fetched: {len(hist)} rows")
        
        from services.analysis.indicator_service import IndicatorService
        indicators = IndicatorService.get_latest_indicators(hist['Close'])
        print(f"📈 Calculated Indicators: RSI={indicators.get('rsi')}, EMA200={indicators.get('ema', {}).get(200)}")

        # C. DB 저장 테스트
        metrics = {
            "current_price": res_price.get("price"),
            "market_cap": res_price.get("market_cap"),
            "per": res_price.get("per"),
            "pbr": res_price.get("pbr"),
            "eps": res_price.get("eps"),
            "bps": res_price.get("bps"),
            "rsi": indicators.get("rsi"),
            "ema": indicators.get("ema"),
            "dcf_value": 0.0 # 임시
        }
        
        saved = StockMetaService.save_financials(ticker, metrics)
        if saved:
            print(f"💾 DB Save Success: ID={saved.id}, Date={saved.base_date}")
            # DB 값 검증
            latest = StockMetaService.get_latest_financials(ticker)
            print(f"🔍 DB Verification: RSI={latest.rsi}, EMA200={latest.ema200}, Price={latest.current_price}")
            return True
        else:
            print(f"❌ DB Save Failed for {ticker}")
            return False
            
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_ranking_fetch():
    print("\n🏆 Testing Ranking Fetch...")
    kr_top = DataService.get_top_krx_tickers(limit=5)
    us_top = DataService.get_top_us_tickers(limit=5)
    print(f"🇰🇷 Top 5 KR: {kr_top}")
    print(f"🇺🇸 Top 5 US: {us_top}")
    if kr_top and us_top:
        return True
    return False

if __name__ == "__main__":
    print("🚀 Starting KIS Data Integration Test")
    
    # 1. 순위 조회 테스트
    rank_ok = test_ranking_fetch()
    
    # 2. 개별 종목 통합 테스트 (국내: 삼성전자 005930, 해외: 애플 AAPL)
    kr_ok = test_single_sync("005930", "KR")
    us_ok = test_single_sync("AAPL", "US")
    
    if rank_ok and kr_ok and us_ok:
        print("\n✨ ALL TESTS PASSED! KIS Integration is working perfectly.")
        sys.exit(0)
    else:
        print("\n⚠️ SOME TESTS FAILED. Please check logs.")
        sys.exit(1)
