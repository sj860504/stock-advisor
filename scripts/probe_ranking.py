import requests
import json
import os
import sys

# 프로젝트 루트 경로 추가 (config 및 서비스 로드를 위해)
sys.path.append('/Users/a10941/workspace/007_private/003_quant')

from config import Config
from services.kis.kis_service import KisService

def test_ranking_params():
    token = KisService.get_access_token()
    url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/market-cap"
    tr_id = "FHPST01700000"
    
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": Config.KIS_APP_KEY,
        "appsecret": Config.KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }

    test_combinations = [
        {"div": "J", "iscd": "0001"}, # 코스피
        {"div": "J", "iscd": "1001"}, # 코스닥
    ]

    for combo in test_combinations:
        import time
        time.sleep(2.0) # 충분한 딜레이
        
        params = {
            "fid_cond_mrkt_div_code": combo["div"],
            "fid_cond_scr_div_code": "20170",
            "fid_div_cls_code": "0",
            "fid_rank_sort_cls_code": "0",
            "fid_input_cnt_1": "0",
            "fid_prc_cls_code": "0",
            "fid_input_iscd_1": combo["iscd"]
        }
        
        print(f"\n--- Testing: div={combo['div']}, iscd={combo['iscd']} ---")
        res = requests.get(url, headers=headers, params=params)
        print(f"Status: {res.status_code}")
        data = res.json()
        print(f"Msg1: {data.get('msg1')}")
        output = data.get('output') or data.get('output2')
        print(f"Output count: {len(output) if output else 0}")
        if output:
            print(f"First 1: {output[0].get('hts_kor_isnm')} ({output[0].get('mksc_shrn_iscd')})")

if __name__ == "__main__":
    test_ranking_params()
