import os
from dotenv import load_dotenv

# .env 로드 (루트 경로 기준)
load_dotenv()

class Config:
    # 한국투자증권 설정
    KIS_APP_KEY = os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
    KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    KIS_WS_URL = os.getenv("KIS_WS_URL", "ws://ops.koreainvestment.com:21000")
    KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")
    
    # Slack 설정
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
    
    # 공공데이터 포털 (선택)
    DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
    
    # FRED API (거시경제)
    FRED_API_KEY = os.getenv("FRED_API_KEY")
    
    # 로깅 레벨
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # 포트폴리오 데이터 경로
    PORTFOLIO_FILE = "data/portfolio_sean.json"
