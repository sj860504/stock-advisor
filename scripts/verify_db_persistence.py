import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.market.stock_meta_service import StockMetaService
from services.analysis.financial_service import FinancialService
from services.market.data_service import DataService

def verify_db_integration():
    print("=== Database Integration & Persistence Verification Start ===")
    
    # 1. DB 초기화 확인
    print("\n1. DB initialization...")
    StockMetaService.init_db()
    db_path = StockMetaService.DB_PATH
    if os.path.exists(db_path):
        print(f"✅ DB file creation confirmed: {db_path}")
    else:
        print("❌ DB file creation failed")

    # 2. DataService를 통한 메타 정보 자동 저장 확인
    print("\n2. DataService meta info save test (KR)...")
    kr_tickers = DataService.get_top_krx_tickers(limit=5)
    if kr_tickers:
        sample_ticker = kr_tickers[0]
        meta = StockMetaService.get_stock_meta(sample_ticker)
        if meta and meta.name_ko:
            print(f"✅ KR ticker meta save success: {sample_ticker} ({meta.name_ko})")
        else:
            print(f"❌ KR ticker meta save failed: {sample_ticker}")

    # 3. FinancialService를 통한 재무 지표 캐싱 및 DB 저장 확인
    print("\n3. FinancialService financial metrics DB caching test (AAPL)...")
    
    # 첫 번째 호출: API에서 가져와서 DB에 저장해야 함
    print("   - First call (API fetching)...")
    metrics1 = FinancialService.get_metrics("AAPL")
    
    # DB에 저장되었는지 확인
    stored = StockMetaService.get_latest_financials("AAPL")
    if stored and stored.eps:
        print(f"✅ AAPL financial metrics DB save confirmed (EPS: {stored.eps})")
    else:
        print("❌ AAPL financial metrics DB save failed")

    # 두 번째 호출: DB에서 가져와야 함 (모의투자 API 호출 없이)
    print("   - Second call (Checking DB cache)...")
    metrics2 = FinancialService.get_metrics("AAPL")
    
    print(f"      Metrics1 (API): {metrics1}")
    print(f"      Metrics2 (DB): {metrics2}")
    
    if metrics1 and metrics2 and metrics1.eps == metrics2.eps:
        print(f"✅ DB cache data return confirmed")
    else:
        print("❌ DB cache data mismatch or return failed")

    print("\n=== All verifications complete ===")

if __name__ == "__main__":
    verify_db_integration()
