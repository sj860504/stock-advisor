import os
import sys
import asyncio

# 프로젝트 루트 경로를 프로그래밍 방식으로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

from config import Config
from services.kis.kis_service import KisService
from services.analysis.financial_service import FinancialService
from services.analysis.dcf_service import DcfService
from services.analysis.analyzer.financial_analyzer import FinancialAnalyzer

# --- 검증용 하드코딩 토큰 (필요 시 여기에 직접 입력하여 403 방지 가능) ---
HARDCODED_TOKEN = "" 

def verify_kis_integration():
    print("=== KIS Service Integration Verification Start ===")
    
    # 환경 변수 로드 확인
    print(f"Base URL: {Config.KIS_BASE_URL}")
    print(f"App Key Loaded: {'Yes' if Config.KIS_APP_KEY else 'No'}")
    
    try:
        # 1. 엑세스 토큰 확인 (재사용 테스트)
        print("\n1. KIS access token check...")
        token1 = KisService.get_access_token()
        token2 = KisService.get_access_token()
        
        cache_path = os.path.join(project_root, 'data', 'kis_token.json')
        file_exists = os.path.exists(cache_path)
        
        if token1 == token2:
            print(f"✅ Token reuse success (memory/file cache working)")
            print(f"   - Token prefix: {token1[:10]}...")
            print(f"   - Cache file exists: {file_exists}")
        else:
            print("❌ Token reuse failed (duplicate issuance)")

        # 2. 국내 주식 시세/지표 데이터 (삼성전자)
        print("\n2. Domestic stock (삼성전자 005930) data collection & analysis...")
        raw_kr = KisService.get_financials("005930")
        if raw_kr and raw_kr.get('rt_cd') == '0':
            metrics_kr = FinancialAnalyzer.analyze_domestic_metrics(raw_kr)
            print(f"✅ Domestic data analysis complete: {metrics_kr}")
        else:
            print(f"❌ Domestic data collection failed: {raw_kr.get('msg1') if raw_kr else 'No Response'}")

        # 3. 해외 주식 상세 시세/지표 (TSLA)
        print("\n3. Overseas stock (TSLA) data collection & analysis...")
        # NASD, NYSE, AMEX 등 시장 코드 확인 필요 (기본 NASD)
        raw_us = KisService.get_overseas_financials("TSLA", market="NASD")
        if raw_us and raw_us.get('rt_cd') == '0':
            metrics_us = FinancialAnalyzer.analyze_overseas_metrics(raw_us)
            print(f"✅ Overseas data analysis complete: {metrics_us}")
        else:
            print(f"❌ Overseas data collection failed: {raw_us.get('msg1') if raw_us else 'No Response'}")

        # 4. DCF 계산 파이프라인 (AAPL)
        print("\n4. DCF calculation pipeline test (AAPL)...")
        dcf_val = DcfService.calculate_dcf("AAPL")
        if dcf_val > 0:
            print(f"✅ DCF fair value calculation success: ${dcf_val:.2f}")
        else:
            print("⚠️ No DCF result (insufficient base metrics)")

    except Exception as e:
        print(f"💥 Critical error during verification: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_kis_integration()
