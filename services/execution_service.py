import requests
import json
import os
from typing import Optional

class ExecutionService:
    """
    한국투자증권(KIS) API를 통한 실시간 매매 실행 서비스
    """
    _base_url = "https://openapivts.koreainvestment.com:29443" # 모의투자용 URL
    _access_token: Optional[str] = None
    
    @classmethod
    def _get_token(cls):
        """API 접근을 위한 토큰 발급"""
        # .env나 환경변수에서 로드 (Sean님이 발급받으시면 여기에 설정 필요)
        app_key = os.getenv("KIS_APP_KEY")
        app_secret = os.getenv("KIS_APP_SECRET")
        
        if not app_key or not app_secret:
            print("❌ KIS API 키가 설정되지 않았습니다.")
            return None

        url = f"{cls._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }
        
        try:
            res = requests.post(url, json=payload)
            cls._access_token = res.json().get("access_token")
            print("✅ KIS API 토큰 발급 성공")
            return cls._access_token
        except Exception as e:
            print(f"❌ 토큰 발급 에러: {e}")
            return None

    @classmethod
    def buy_market_order(cls, ticker: str, quantity: int):
        """시장가 매수 주문"""
        if not cls._access_token:
            cls._get_token()
            
        url = f"{cls._base_url}/uapi/domestic-stock/v1/trading/order-cash" # 국내주식 기준 예시
        
        # 해외주식(미국)일 경우 URL과 헤더가 달라짐
        if not ticker.isdigit(): # 미국 주식인 경우 (알파벳 티커)
            url = f"{cls._base_url}/uapi/overseas-stock/v1/trading/order"
            
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {cls._access_token}",
            "appkey": os.getenv("KIS_APP_KEY"),
            "appsecret": os.getenv("KIS_APP_SECRET"),
            "tr_id": "VTTT0001U" if ticker.isdigit() else "VTTT1002U" # 모의투자 매수 TR ID
        }
        
        # 상세 주문 데이터 (한국투자증권 규격에 맞춤 필요)
        # 이 부분은 발급받으신 계좌번호가 있어야 완성이 가능합니다.
        print(f"🚀 [{ticker}] {quantity}주 시장가 매수 주문 전송 시도...")
        return {"status": "ready", "message": "API 키와 계좌 정보가 설정되면 실제 주문이 나갑니다."}

    @classmethod
    def get_balance(cls):
        """계좌 잔고 및 현금 조회"""
        print("🔍 계좌 잔고 조회 중...")
        return {"cash": 10000000, "stocks": []} # 테스트용 가짜 데이터
