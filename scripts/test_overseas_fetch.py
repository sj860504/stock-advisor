import sys
import os
import json
import time
from datetime import datetime, timedelta

# 프로젝트 루트를 path에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.market.stock_meta_service import StockMetaService
from models.stock_meta import StockMeta

def run_overseas_test():
    print("🚀 Starting Overseas API Verification Test...")
    
    # 1. DB 초기화 및 메타 데이터 확인
    print("\n--- 1. Database Initialization ---")
    StockMetaService.init_db()
    # 기존 메타 삭제 후 재설정
    from models.stock_meta import Base
    from sqlalchemy import text
    session = StockMetaService.get_session()
    session.execute(text('DROP TABLE IF EXISTS api_tr_meta'))
    session.execute(text('DROP TABLE IF EXISTS stock_meta'))
    session.commit()
    Base.metadata.create_all(StockMetaService.engine)
    
    count = StockMetaService.init_api_tr_meta()
    print(f"✅ {count} TR metadata records initialized.")
    
    # 데이터 확인
    tr_id, path = StockMetaService.get_api_info("해외주식_기간별시세")
    print(f"📊 해외주식_기간별시세 (VTS): TR={tr_id}, Path={path}")
    
    # 2. 토큰 획득
    print("\n--- 2. Getting Access Token ---")
    token = KisService.get_access_token()
    if not token:
        print("❌ Failed to get access token. Check .env")
        return
    print(f"✅ Token: {token[:10]}...")

    # 3. 해외주식 랭킹 조회 (시총 상위 종목 조회용)
    print("\n--- 3. Overseas Ranking (Market Cap Top Items) ---")
    print("💡 이 API는 해당 시장의 시가총액 순위(보통 상위 100개)를 가져옵니다.")
    for excd in ["NAS", "NYS"]:
        res = KisFetcher.fetch_overseas_ranking(token, excd=excd)
        if res and res.get('output'):
            print(f"✅ {excd} Ranking Success: Got {len(res['output'])} items.")
        else:
            print(f"❌ {excd} Ranking Failed. Body: {res.get('raw') if res else 'None'}")

    # 4. 해외주식 상세 시세 조회 (VTS 전용 경로/TR 테스트)
    print("\n--- 4. Overseas Price Detail (VTS Price Route) ---")
    time.sleep(1.0)
    ticker = "AAPL"
    res = KisFetcher.fetch_overseas_price(token, ticker)
    if res and res.get('price'):
        print(f"✅ {ticker} Price Success: ${res['price']} (Name: {res['name']})")
    else:
        print(f"❌ {ticker} Price Failed.")

    # 5. 해외주식 일자별 시세 (HHDFS76240000 테스트)
    print("\n--- 5. Overseas Daily Price (HHDFS76240000) ---")
    time.sleep(1.0)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    
    # AAPL (NASDAQ)
    res_aapl = KisFetcher.fetch_overseas_daily_price(token, "AAPL", start_date, end_date)
    if res_aapl and res_aapl.get('output'):
         print(f"✅ AAPL (NAS) Daily Success: {len(res_aapl['output'])} days returned.")
    else:
         print(f"❌ AAPL (NAS) Daily Failed.")
         
    # JPM (NYSE)
    time.sleep(1.0)
    # 먼저 메타에 JPM 등록
    StockMetaService.upsert_stock_meta(ticker="JPM", api_market_code="NYS", market_type="US")
    res_jpm = KisFetcher.fetch_overseas_daily_price(token, "JPM", start_date, end_date)
    if res_jpm and res_jpm.get('output'):
         print(f"✅ JPM (NYS) Daily Success: {len(res_jpm['output'])} days returned.")
    else:
         print(f"❌ JPM (NYS) Daily Failed.")

    print("\n🚀 Verification Test Finished.")

if __name__ == "__main__":
    run_overseas_test()
