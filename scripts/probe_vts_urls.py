import sys
import os
import requests

# 프로젝트 루트를 path에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.kis.kis_service import KisService
from config import Config

def probe_urls():
    print("🔍 Probing VTS Overseas API Endpoints...")
    token = KisService.get_access_token()
    
    # 시도해볼 경로 및 포트 조합
    targets = [
        ("https://openapivts.koreainvestment.com:29443", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100", {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"}),
        ("https://openapivts.koreainvestment.com:29443", "/uapi/overseas-stock/v1/quotations/price", "VTTT1101R", {"AUTH": "", "EXCD": "NASD", "SYMB": "AAPL"}),
        ("https://openapivts.koreainvestment.com:29443", "/uapi/overseas-stock/v1/quotations/price-detail", "VTTT1101R", {"AUTH": "", "EXCD": "NASD", "SYMB": "AAPL"}),
        ("https://openapivts.koreainvestment.com", "/uapi/overseas-stock/v1/quotations/price", "VTTT1101R", {"AUTH": "", "EXCD": "NASD", "SYMB": "AAPL"}),
        ("https://openapi.koreainvestment.com:9443", "/uapi/overseas-stock/v1/quotations/price", "HHDFS00000300", {"AUTH": "", "EXCD": "NASD", "SYMB": "AAPL"}),
    ]
    
    for base, path, tr_id, params in targets:
        print(f"\n🌐 Testing: {base}{path} (TR: {tr_id})")
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P"
        }
        url = f"{base}{path}"
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            print(f"[{res.status_code}] Response -> {res.text[:150]}")
        except Exception as e:
            print(f"[ERROR] {e}")

if __name__ == "__main__":
    probe_urls()
