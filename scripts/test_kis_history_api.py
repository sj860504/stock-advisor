import os
import sys
import asyncio
from pprint import pprint

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.kis.kis_service import KisService
from config import Config

async def test_kis_history():
    print("Testing KIS History API...")
    
    # 1. Domestic History (TTTC8001R)
    url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
    tr_id = "VTTC8001R" if Config.KIS_IS_VTS else "TTTC8001R"
    headers = KisService.get_headers(tr_id)
    account_prefix, account_suffix = KisService._get_account_parts()
    
    params = {
        "CANO": account_prefix,
        "ACNT_PRDT_CD": account_suffix,
        "INQR_STRT_DT": "20240101", # roughly last few months
        "INQR_END_DT": "20241231",
        "SLL_BUY_DVSN_CD": "00", # All
        "INQR_DVSN": "00", # order order
        "PDNO": "",
        "CCLD_DVSN": "00", # All
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "INQR_DVSN_3": "00",
        "INQR_DVSN_1": "",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }
    
    import requests
    response = requests.get(url, headers=headers, params=params)
    print("Domestic Response Status:", response.status_code)
    try:
        data = response.json()
        print("Success:", data.get('rt_cd'))
        print("Msg:", data.get('msg1'))
        if data.get('output1'):
            print(f"Found {len(data['output1'])} domestic trades")
            pprint(data['output1'][:2])
    except Exception as e:
        print("Failed to parse json:", e)

    import time
    time.sleep(2)
    print("\n2. Overseas History (JTTT3001R)")
    url2 = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-ccnl"
    tr_id2 = "VTTT3001R" if Config.KIS_IS_VTS else "JTTT3001R"
    headers2 = KisService.get_headers(tr_id2)
    
    params2 = {
        "CANO": account_prefix,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "NASD", # NASDAQ exchange
        "PDNO": "%", # All
        "ORD_STRT_DT": "20240101",
        "ORD_END_DT": "20241231",
        "SLL_BUY_DVSN": "00", # All
        "CCLD_NCCS_DVSN": "00", # All
        "ORD_DT": "",
        "ORD_GNO_BRNO": "",
        "ODNO": "",
        "SORT_SQN": "",
        "CTX_AREA_NK200": "",
        "CTX_AREA_FK200": ""
    }
    
    response2 = requests.get(url2, headers=headers2, params=params2)
    print("Overseas Response Status:", response2.status_code)
    try:
        data2 = response2.json()
        print("Success:", data2.get('rt_cd'))
        print("Msg:", data2.get('msg1'))
        if data2.get('output'):
            print(f"Found {len(data2['output'])} overseas trades")
            pprint(data2['output'][:2])
    except Exception as e:
        print("Failed to parse json:", e)

if __name__ == "__main__":
    asyncio.run(test_kis_history())
