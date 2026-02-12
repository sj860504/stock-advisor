import asyncio
import time
from datetime import datetime
import os
import sys

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.market.market_data_service import MarketDataService
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger

logger = get_logger("verify_batch")

async def verify_batch_registration():
    print("🚀 Batch Registration Performance Test Starting...")
    
    # 1. 테스트용 티커 리스트 (DB에 데이터가 있는 것 10개 + 없는 것 2개)
    # 실제 환경의 티커들 (DB에 이미 데이터가 있다고 가정)
    db_tickers = ["005930", "000660", "035420", "035720", "005380", "005490", "051910", "105560", "028260", "012330"]
    new_tickers = ["999995", "999996"] # 존재하지 않는 티커 (API 호출 시도하게 됨)
    all_tickers = db_tickers + new_tickers
    
    # 캐시 초기화 (테스트 반복을 위해)
    MarketDataService._states = {}
    
    print(f"📊 Registering {len(all_tickers)} tickers in one batch.")
    print(f"   - {len(db_tickers)} tickers expected to have DB data (should be < 1s total)")
    print(f"   - {len(new_tickers)} tickers expected to need API (should take ~2s total in background)")

    start_time = time.time()
    
    # 일괄 등록 실행
    MarketDataService.register_batch(all_tickers)
    
    registration_time = time.time() - start_time
    print(f"⏱️ Batch registration call took: {registration_time:.4f} seconds")
    
    # 즉시 준비된 종목 확인
    ready_count = 0
    for ticker in db_tickers:
        state = MarketDataService.get_state(ticker)
        if state and state.is_ready:
            ready_count += 1
            
    print(f"✅ Instantly ready from DB: {ready_count}/{len(db_tickers)}")
    
    if registration_time < 0.5 and ready_count > 0:
        print("🎉 SUCCESS: Batch DB loading is working as intended!")
    else:
        print("⚠️ WARNING: Batch loading might be slower than expected.")

    print("\n⏳ Waiting 5 seconds to observe background warm-up for new tickers...")
    await asyncio.sleep(5)
    
    for ticker in new_tickers:
        state = MarketDataService.get_state(ticker)
        ready = "READY" if state and state.is_ready else "NOT READY"
        print(f"   - Ticker {ticker}: {ready}")

if __name__ == "__main__":
    asyncio.run(verify_batch_registration())
