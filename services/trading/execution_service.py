import requests
import json
import os
from typing import Optional

from utils.market import is_kr

class ExecutionService:
    """
    한국투자증권(KIS) API를 통한 실시간 주문 실행 서비스
    """
    _base_url = "https://openapivts.koreainvestment.com:29443" # 모의투자 URL
    _access_token: Optional[str] = None
    
    @classmethod
    def _get_token(cls):
        """API 접속을 위한 토큰 발급"""
        # .env???섍꼍蹂?섏뿉??濡쒕뱶 (Sean?섏씠 諛쒓툒諛쏆쑝?쒕㈃ ?ш린???ㅼ젙 ?꾩슂)
        app_key = os.getenv("KIS_APP_KEY")
        app_secret = os.getenv("KIS_APP_SECRET")
        
        if not app_key or not app_secret:
            print("KIS API 키가 설정되지 않았습니다.")
            return None

        url = f"{cls._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            cls._access_token = response.json().get("access_token")
            print("KIS API 토큰 발급 성공")
            return cls._access_token
        except Exception as e:
            print(f"토큰 발급 오류: {e}")
            return None

    @classmethod
    def buy_market_order(cls, ticker: str, quantity: int):
        """시장가 매수 주문"""
        if not cls._access_token:
            cls._get_token()
            
        url = f"{cls._base_url}/uapi/domestic-stock/v1/trading/order-cash" # 국내 주식 기본 주문 URL
        
        # 해외 주식은 URL/헤더가 다름
        if not is_kr(ticker): # 해외 주식(미국 등) 종목
            url = f"{cls._base_url}/uapi/overseas-stock/v1/trading/order"

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {cls._access_token}",
            "appkey": os.getenv("KIS_APP_KEY"),
            "appsecret": os.getenv("KIS_APP_SECRET"),
            "tr_id": "VTTT0001U" if is_kr(ticker) else "VTTT1002U" # 모의투자 매수 TR ID
        }
        
        # 실제 주문 데이터는 계좌 정보가 필요
        # 계좌 정보가 없으므로 현재는 시뮬레이션으로만 동작
        print(f"?? [{ticker}] {quantity}二?시장가 매수 주문 ?꾩넚 ?쒕룄...")
        return {"status": "ready", "message": "API 계좌 정보가 설정되면 실제 주문이 실행됩니다."}

    @classmethod
    def get_balance(cls):
        """계좌 잔고 및 보유 종목 조회"""
        print("📊 계좌 잔고 조회 중...")
        return {"cash": 10000000, "stocks": []} # 테스트용 더미 데이터
