import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

class Config:
    # 한국투자증권 (KIS) 설정
    KIS_APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
    KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443").strip() # 기본값: 모의투자
    KIS_WS_URL = os.getenv("KIS_WS_URL", "ws://ops.koreainvestment.com:21000").strip() # 웹소켓 주소
    KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "").strip() # 계좌번호 (앞 8자리)
    
    # FRED API Key
    FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
    
    # 슬랙 설정
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    
    # 공통 설정
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
