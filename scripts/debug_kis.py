import os
import sys
import json
from datetime import datetime

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from config import Config

def debug_full_flow():
    print("🔍 [1] Token Check")
    try:
        token = KisService.get_access_token()
        print(f"✅ Token: {token[:10]}...")
    except Exception as e:
        print(f"❌ Token Failed: {e}")
        return

    print("\n🔍 [2] Domestic Price (삼성전자 005930)")
    res_kr = KisFetcher.fetch_domestic_price(token, "005930")
    print(json.dumps(res_kr, indent=2, ensure_ascii=False))

    print("\n🔍 [3] Overseas Price (Apple AAPL)")
    res_us = KisFetcher.fetch_overseas_price(token, "AAPL", meta={"api_market_code": "NASD"})
    print(json.dumps(res_us, indent=2, ensure_ascii=False))

    print("\n🔍 [4] Domestic Ranking (KOSPI)")
    res_kr_rank = KisFetcher.fetch_domestic_ranking(token, mrkt_div="0001")
    if res_kr_rank.get('output'):
        print(f"✅ Got {len(res_kr_rank['output'])} domestic items. First: {res_kr_rank['output'][0].get('hts_kor_isnm')}")
    else:
        print(f"❌ Domestic Ranking Failed: {res_kr_rank.get('msg1', 'No output')}")

    print("\n🔍 [5] Overseas Ranking (NASDAQ)")
    res_us_rank = KisFetcher.fetch_overseas_ranking(token, excd="NAS")
    if res_us_rank.get('output'):
        print(f"✅ Got {len(res_us_rank['output'])} overseas items. First: {res_us_rank['output'][0].get('hname')}")
    else:
        print(f"❌ Overseas Ranking Failed: {res_us_rank.get('msg1', 'No output')}")

if __name__ == "__main__":
    debug_full_flow()
