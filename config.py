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
    # 점수 기반 전략 기본값: 30~40 매수, 70~100 매도
    STRATEGY_BUY_THRESHOLD_MIN = int(os.getenv("STRATEGY_BUY_THRESHOLD_MIN", "30"))
    STRATEGY_BUY_THRESHOLD = int(os.getenv("STRATEGY_BUY_THRESHOLD", "40"))
    STRATEGY_SELL_THRESHOLD = int(os.getenv("STRATEGY_SELL_THRESHOLD", "70"))
    STRATEGY_SELL_THRESHOLD_MAX = int(os.getenv("STRATEGY_SELL_THRESHOLD_MAX", "100"))
    # 전체 분석 준비 완료 전에는 매매하지 않음
    STRATEGY_REQUIRE_FULL_ANALYSIS = int(os.getenv("STRATEGY_REQUIRE_FULL_ANALYSIS", "1"))
    STRATEGY_MIN_READY_RATIO = float(os.getenv("STRATEGY_MIN_READY_RATIO", "1.0"))
    STRATEGY_SPLIT_COUNT = int(os.getenv("STRATEGY_SPLIT_COUNT", "3"))
    
    STRATEGY_STOP_LOSS_PCT = float(os.getenv("STRATEGY_STOP_LOSS_PCT", "-8.0"))
    STRATEGY_TAKE_PROFIT_PCT = float(os.getenv("STRATEGY_TAKE_PROFIT_PCT", "3.0"))
    STRATEGY_DIP_BUY_PCT = float(os.getenv("STRATEGY_DIP_BUY_PCT", "-5.0"))
    STRATEGY_OVERSOLD_RSI = float(os.getenv("STRATEGY_OVERSOLD_RSI", "30.0"))
    STRATEGY_OVERBOUGHT_RSI = float(os.getenv("STRATEGY_OVERBOUGHT_RSI", "70.0"))

    # 인증 (JWT)
    JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRE_HOURS = 24
    AUTH_USERNAME = os.getenv("AUTH_USERNAME", "sean")
    AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "")

    # DCF(현금흐름할인법) 설정
    DCF_EQUITY_RISK_PREMIUM = float(os.getenv("DCF_EQUITY_RISK_PREMIUM", "0.055"))
    DCF_DISCOUNT_RATE_FLOOR = float(os.getenv("DCF_DISCOUNT_RATE_FLOOR", "0.06"))
    DCF_DISCOUNT_RATE_CEIL = float(os.getenv("DCF_DISCOUNT_RATE_CEIL", "0.15"))
    DCF_DEFAULT_DISCOUNT_RATE = float(os.getenv("DCF_DEFAULT_DISCOUNT_RATE", "0.10"))
    DCF_STAGE1_YEARS = int(os.getenv("DCF_STAGE1_YEARS", "10"))
