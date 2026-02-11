import os
import sys
from datetime import datetime

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from services.stock_meta_service import StockMetaService
from services.financial_service import FinancialService
from services.data_service import DataService

def verify_db_integration():
    print("=== 데이터베이스 연동 및 영속화 검증 시작 ===")
    
    # 1. DB 초기화 확인
    print("\n1. DB 초기화 가동...")
    StockMetaService.init_db()
    db_path = StockMetaService.DB_PATH
    if os.path.exists(db_path):
        print(f"✅ DB 파일 생성 확인: {db_path}")
    else:
        print("❌ DB 파일 생성 실패")

    # 2. DataService를 통한 메타 정보 자동 저장 확인
    print("\n2. DataService 메타 정보 저장 테스트 (KR)...")
    kr_tickers = DataService.get_top_krx_tickers(limit=5)
    if kr_tickers:
        sample_ticker = kr_tickers[0]
        meta = StockMetaService.get_stock_meta(sample_ticker)
        if meta and meta.name_ko:
            print(f"✅ KR 종목 메타 저장 성공: {sample_ticker} ({meta.name_ko})")
        else:
            print(f"❌ KR 종목 메타 저장 실패: {sample_ticker}")

    # 3. FinancialService를 통한 재무 지표 캐싱 및 DB 저장 확인
    print("\n3. FinancialService 재무 지표 DB 캐싱 테스트 (AAPL)...")
    
    # 첫 번째 호출: API에서 가져와서 DB에 저장해야 함
    print("   - 첫 번째 호출 (API fetching)...")
    metrics1 = FinancialService.get_metrics("AAPL")
    
    # DB에 저장되었는지 확인
    stored = StockMetaService.get_latest_financials("AAPL")
    if stored and stored.eps:
        print(f"✅ AAPL 재무 지표 DB 저장 확인 (EPS: {stored.eps})")
    else:
        print("❌ AAPL 재무 지표 DB 저장 실패")

    # 두 번째 호출: DB에서 가져와야 함 (모의투자 API 호출 없이)
    print("   - 두 번째 호출 (Checking DB cache)...")
    metrics2 = FinancialService.get_metrics("AAPL")
    
    print(f"      Metrics1 (API): {metrics1}")
    print(f"      Metrics2 (DB): {metrics2}")
    
    if metrics1 and metrics2 and metrics1.get('eps') == metrics2.get('eps'):
        print(f"✅ DB 캐시 데이터 반환 확인")
    else:
        print("❌ DB 캐시 데이터 불일치 또는 반환 실패")

    print("\n=== 모든 검증 완료 ===")

if __name__ == "__main__":
    verify_db_integration()
