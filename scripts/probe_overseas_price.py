import sys
import os
import requests
import json
import time

# 프로젝트 루트를 path에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kis.kis_service import KisService
from config import Config

def probe_overseas_price():
    print("🔍 Probing VTS Overseas Price APIs (New Paths)...")
    token = KisService.get_access_token()
    
    base_vts = "https://openapivts.koreainvestment.com:29443"
    
    # 1. 시세 조합 테스트
    print("\n--- 1. Price Combination Probes ---")
    combinations = [
        ("/uapi/overseas-price/v1/quotations/price", "VTTT1101R"),
        ("/uapi/overseas-price/v1/quotations/price", "HHDFS00000300"),
        ("/uapi/overseas-stock/v1/quotations/price", "VTTT1101R"),
        ("/uapi/overseas-stock/v1/quotations/price", "HHDFS00000300"),
        ("/uapi/overseas-price/v1/quotations/price-detail", "VTTT1101R"),
        ("/uapi/overseas-price/v1/quotations/price-detail", "HHDFS70200200"),
    ]
    
    for path, tr_id in combinations:
        print(f"\n🌐 Testing Path: {path}, TR: {tr_id}")
        url = f"{base_vts}{path}"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P"
        }
        # EXCD 3자/4자 둘 다 시도
        for excd in ["NAS", "NASD"]:
            params = {"AUTH": "", "EXCD": excd, "SYMB": "AAPL"}
            try:
                res = requests.get(url, headers=headers, params=params, timeout=5)
                print(f"[{res.status_code}] EXCD:{excd} -> {res.text[:100]}")
            except Exception as e:
                print(f"[ERROR] EXCD:{excd} -> {e}")
            time.sleep(1.2)

    # 2. 일자별 시세 정밀 테스트
    print("\n--- 2. Daily Price Precision Probe ---")
    url = f"{base_vts}/uapi/overseas-price/v1/quotations/dailyprice"
    headers["tr_id"] = "HHDFS76240000"
    # EXCD 3자 vs 4자
    for excd in ["NAS", "NASD"]:
        params = {"AUTH": "", "EXCD": excd, "SYMB": "AAPL", "GUBN": "0", "BYMD": "", "MODP": "0"}
        res = requests.get(url, headers=headers, params=params)
        print(f"URL: {url}, TR: HHDFS76240000, EXCD: {excd}")
        print(f"Status: {res.status_code}, Resp: {res.text[:200]}")
        time.sleep(1.2)

    # 3. 랭킹 정밀 테스트
    print("\n--- 3. Ranking Precision Probe ---")
    url = f"{base_vts}/uapi/overseas-stock/v1/ranking/market-cap"
    headers["tr_id"] = "HHDFS76350100"
    for excd in ["NAS", "NASD", "NYS", "NYSE"]:
        params = {"AUTH": "", "EXCD": excd, "GUBN": "0"}
        res = requests.get(url, headers=headers, params=params)
        print(f"URL: {url}, TR: HHDFS76350100, EXCD: {excd}")
        print(f"Status: {res.status_code}, Resp: {res.text[:100]}")
        time.sleep(1.2)

if __name__ == "__main__":
    probe_overseas_price()
