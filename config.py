import os
from dotenv import load_dotenv

# Load .env (relative to project root)
load_dotenv()

class Config:
    # Korea Investment & Securities — VTS (paper trading, order execution)
    KIS_APP_KEY = os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
    KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    KIS_WS_URL = os.getenv("KIS_WS_URL", "ws://ops.koreainvestment.com:21000")
    KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")
    KIS_IS_VTS = os.getenv("KIS_IS_VTS", "true").lower() == "true"

    # Korea Investment & Securities — Live account (orders + quotes + WebSocket)
    # When KIS_IS_VTS=false: orders/balance/history use KIS_REAL_* credentials and KIS_REAL_ACCOUNT_NO
    KIS_REAL_APP_KEY = os.getenv("KIS_REAL_APP_KEY", "")
    KIS_REAL_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET", "")
    KIS_REAL_BASE_URL = os.getenv("KIS_REAL_BASE_URL", "https://openapi.koreainvestment.com:9443")
    KIS_REAL_WS_URL = os.getenv("KIS_REAL_WS_URL", "ws://ops.koreainvestment.com:21000")
    KIS_REAL_ACCOUNT_NO = os.getenv("KIS_REAL_ACCOUNT_NO", "")

    @classmethod
    def has_real_credentials(cls) -> bool:
        """Return True if live account credentials are fully configured."""
        return bool(cls.KIS_REAL_APP_KEY and cls.KIS_REAL_APP_SECRET and cls.KIS_REAL_ACCOUNT_NO)
    # Enable after-hours order method only in live trading environment
    KIS_ENABLE_AFTER_HOURS_ORDER = os.getenv("KIS_ENABLE_AFTER_HOURS_ORDER", "false").lower() == "true"
    # After-hours order type code (default: post-market extended hours)
    KIS_AFTER_HOURS_ORD_DVSN = os.getenv("KIS_AFTER_HOURS_ORD_DVSN", "81")
    
    # Slack configuration
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
    
    # Public data portal (optional)
    DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
    
    # FRED API (macroeconomics)
    FRED_API_KEY = os.getenv("FRED_API_KEY")
    
    # Financial Modeling Prep API (for ISM Services PMI calendar)
    FMP_API_KEY = os.getenv("FMP_API_KEY")
    
    # Dev mode (true: suppress Slack buy/sell notifications)
    DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"

    # Logging level
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # Portfolio data path
    PORTFOLIO_FILE = "data/portfolio_sean.json"

    # Strategy trading settings
    STRATEGY_TARGET_CASH_RATIO = float(os.getenv("STRATEGY_TARGET_CASH_RATIO", "0.40"))
    STRATEGY_PER_TRADE_RATIO = float(os.getenv("STRATEGY_PER_TRADE_RATIO", "0.05"))
    STRATEGY_BASE_SCORE = int(os.getenv("STRATEGY_BASE_SCORE", "50"))
    # Score-based strategy defaults: 30~40 buy, 70~100 sell
    STRATEGY_BUY_THRESHOLD_MIN = int(os.getenv("STRATEGY_BUY_THRESHOLD_MIN", "30"))
    STRATEGY_BUY_THRESHOLD = int(os.getenv("STRATEGY_BUY_THRESHOLD", "30"))  # 문서·자산관리와 통일 (B1)
    STRATEGY_SELL_THRESHOLD = int(os.getenv("STRATEGY_SELL_THRESHOLD", "70"))
    STRATEGY_SELL_THRESHOLD_MAX = int(os.getenv("STRATEGY_SELL_THRESHOLD_MAX", "100"))
    # Do not trade until full analysis is ready
    STRATEGY_REQUIRE_FULL_ANALYSIS = int(os.getenv("STRATEGY_REQUIRE_FULL_ANALYSIS", "1"))
    STRATEGY_MIN_READY_RATIO = float(os.getenv("STRATEGY_MIN_READY_RATIO", "1.0"))
    STRATEGY_SPLIT_COUNT = int(os.getenv("STRATEGY_SPLIT_COUNT", "3"))
    
    STRATEGY_STOP_LOSS_PCT = float(os.getenv("STRATEGY_STOP_LOSS_PCT", "-8.0"))
    STRATEGY_TAKE_PROFIT_PCT = float(os.getenv("STRATEGY_TAKE_PROFIT_PCT", "3.0"))
    STRATEGY_DIP_BUY_PCT = float(os.getenv("STRATEGY_DIP_BUY_PCT", "-5.0"))
    STRATEGY_OVERSOLD_RSI = float(os.getenv("STRATEGY_OVERSOLD_RSI", "30.0"))
    STRATEGY_OVERBOUGHT_RSI = float(os.getenv("STRATEGY_OVERBOUGHT_RSI", "70.0"))

    # USD/KRW exchange rate (default value until real-time integration)
    # Shared by macro_service.get_exchange_rate() and portfolio_service
    EXCHANGE_RATE_KRW_USD: float = float(os.getenv("EXCHANGE_RATE_KRW_USD", "1400"))

    # Authentication (JWT)
    JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
    JWT_ALGORITHM = "HS256"
    JWT_EXPIRE_HOURS = 30 * 24  # 720 (30일)
    AUTH_USERNAME = os.getenv("AUTH_USERNAME", "sean")
    AUTH_PASSWORD_HASH = os.getenv("AUTH_PASSWORD_HASH", "")

    # DCF (Discounted Cash Flow) settings
    DCF_EQUITY_RISK_PREMIUM = float(os.getenv("DCF_EQUITY_RISK_PREMIUM", "0.055"))
    DCF_DISCOUNT_RATE_FLOOR = float(os.getenv("DCF_DISCOUNT_RATE_FLOOR", "0.06"))
    DCF_DISCOUNT_RATE_CEIL = float(os.getenv("DCF_DISCOUNT_RATE_CEIL", "0.15"))
    DCF_DEFAULT_DISCOUNT_RATE = float(os.getenv("DCF_DEFAULT_DISCOUNT_RATE", "0.10"))
    DCF_STAGE1_YEARS = int(os.getenv("DCF_STAGE1_YEARS", "10"))
    DCF_TERMINAL_GROWTH = float(os.getenv("DCF_TERMINAL_GROWTH", "0.03"))
