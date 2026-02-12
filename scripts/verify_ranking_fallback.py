import sys
import os

# 프로젝트 루트 경로 추가
sys.path.append('/Users/a10941/workspace/007_private/003_quant')

from config import Config
Config.KIS_IS_VTS = True # 모의투자 모드 강제 설정

from services.kis.fetch.kis_fetcher import KisFetcher
from services.kis.kis_service import KisService
import json

def verify_fallback():
    print(f"🔍 Verifying domestic ranking fallback (VTS Mode: {Config.KIS_IS_VTS})")
    token = KisService.get_access_token()
    
    # fetch_domestic_ranking 호출
    ranking_data = KisFetcher.fetch_domestic_ranking(token)
    
    if ranking_data and 'output' in ranking_data:
        output = ranking_data['output']
        print(f"✅ Received {len(output)} stocks from local master fallback.")
        if len(output) > 0:
            print(f"Top Stock: {output[0]['hts_kor_isnm']} ({output[0]['mksc_shrn_iscd']})")
    else:
        print("❌ Failed to receive ranking data.")

if __name__ == "__main__":
    verify_fallback()
