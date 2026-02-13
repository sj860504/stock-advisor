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
    KIS_IS_VTS = os.getenv("KIS_IS_VTS", "true").lower() == "true"
    # 실전투자 환경에서만 사후장(시간외) 주문 메서드 활성화
    KIS_ENABLE_AFTER_HOURS_ORDER = os.getenv("KIS_ENABLE_AFTER_HOURS_ORDER", "false").lower() == "true"
    # 사후장 주문 구분값(기본: 장후 시간외)
    KIS_AFTER_HOURS_ORD_DVSN = os.getenv("KIS_AFTER_HOURS_ORD_DVSN", "81")
    
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

    # 전략 매매 설정
    STRATEGY_TARGET_CASH_RATIO = float(os.getenv("STRATEGY_TARGET_CASH_RATIO", "0.40"))
    STRATEGY_PER_TRADE_RATIO = float(os.getenv("STRATEGY_PER_TRADE_RATIO", "0.05"))
    STRATEGY_BASE_SCORE = int(os.getenv("STRATEGY_BASE_SCORE", "50"))
    STRATEGY_BUY_THRESHOLD = int(os.getenv("STRATEGY_BUY_THRESHOLD", "70"))
    STRATEGY_SELL_THRESHOLD = int(os.getenv("STRATEGY_SELL_THRESHOLD", "25"))
    STRATEGY_SPLIT_COUNT = int(os.getenv("STRATEGY_SPLIT_COUNT", "3"))
    
    STRATEGY_STOP_LOSS_PCT = float(os.getenv("STRATEGY_STOP_LOSS_PCT", "-10.0"))
    STRATEGY_TAKE_PROFIT_PCT = float(os.getenv("STRATEGY_TAKE_PROFIT_PCT", "5.0"))
    STRATEGY_DIP_BUY_PCT = float(os.getenv("STRATEGY_DIP_BUY_PCT", "-5.0"))
    STRATEGY_OVERSOLD_RSI = float(os.getenv("STRATEGY_OVERSOLD_RSI", "30.0"))
    STRATEGY_OVERBOUGHT_RSI = float(os.getenv("STRATEGY_OVERBOUGHT_RSI", "70.0"))
