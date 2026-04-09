import time
from repositories.settings_repo import SettingsRepo
from config import Config
from utils.logger import get_logger

logger = get_logger("settings_service")

class SettingsService:
    """
    System settings management service
    """
    _cache: dict = {}       # {key: (value, expire_time)}
    _CACHE_TTL: int = 30    # 30s TTL — reflects changes within 30 seconds

    # Default settings (loaded from Config)
    DEFAULT_SETTINGS = {
        "STRATEGY_TARGET_CASH_RATIO": (str(Config.STRATEGY_TARGET_CASH_RATIO), "Target cash ratio (0.0 ~ 1.0)"),
        "STRATEGY_PER_TRADE_RATIO": (str(Config.STRATEGY_PER_TRADE_RATIO), "Per-trade ratio (relative to total assets)"),
        "STRATEGY_BASE_SCORE": (str(Config.STRATEGY_BASE_SCORE), "Base score"),
        "STRATEGY_BUY_THRESHOLD_MIN": (str(Config.STRATEGY_BUY_THRESHOLD_MIN), "Buy score lower bound (default 30)"),
        "STRATEGY_BUY_THRESHOLD": (str(Config.STRATEGY_BUY_THRESHOLD), "Buy score threshold"),
        "STRATEGY_SELL_THRESHOLD": (str(Config.STRATEGY_SELL_THRESHOLD), "Sell score threshold"),
        "STRATEGY_SELL_THRESHOLD_MAX": (str(Config.STRATEGY_SELL_THRESHOLD_MAX), "Sell score upper bound (default 100)"),
        "STRATEGY_REQUIRE_FULL_ANALYSIS": (str(Config.STRATEGY_REQUIRE_FULL_ANALYSIS), "Block trading before full analysis ready (1=block)"),
        "STRATEGY_MIN_READY_RATIO": (str(Config.STRATEGY_MIN_READY_RATIO), "Minimum readiness ratio to allow trading (0.0~1.0)"),
        "STRATEGY_SPLIT_COUNT": (str(Config.STRATEGY_SPLIT_COUNT), "Split trade count"),
        "STRATEGY_STOP_LOSS_PCT": (str(Config.STRATEGY_STOP_LOSS_PCT), "Stop-loss return threshold (%)"),
        "STRATEGY_TAKE_PROFIT_PCT": (str(Config.STRATEGY_TAKE_PROFIT_PCT), "Take-profit return threshold (%)"),
        "STRATEGY_DIP_BUY_PCT": (str(Config.STRATEGY_DIP_BUY_PCT), "Dip buy threshold (%)"),
        "STRATEGY_OVERSOLD_RSI": (str(Config.STRATEGY_OVERSOLD_RSI), "Oversold RSI threshold"),
        "STRATEGY_OVERBOUGHT_RSI": (str(Config.STRATEGY_OVERBOUGHT_RSI), "Overbought RSI threshold"),
        "STRATEGY_ALLOW_EXTENDED_HOURS": ("1", "Allow US pre/after-market orders (1=allow, 0=disallow)"),
        "STRATEGY_TICK_ENABLED": ("0", "Tick trading strategy enabled (1=on, 0=off)"),
        "STRATEGY_TICK_TICKER": ("005930", "Tick trading target ticker (1 stock per day)"),
        "STRATEGY_TICK_CASH_RATIO": ("0.20", "Cash ratio for tick trading (relative to total assets)"),
        "STRATEGY_TICK_ENTRY_PCT": ("-1.0", "Tick trading 1st entry change rate (%)"),
        "STRATEGY_TICK_ADD_PCT": ("-3.0", "Tick trading 2nd add-buy change rate (%)"),
        "STRATEGY_TICK_TAKE_PROFIT_PCT": ("1.0", "Tick trading take-profit return (%)"),
        "STRATEGY_TICK_STOP_LOSS_PCT": ("-5.0", "Tick trading stop-loss return (%)"),
        "STRATEGY_TICK_CLOSE_MINUTES": ("5", "Minutes before market close to liquidate"),
        "PORTFOLIO_INITIAL_PRINCIPAL": ("10000000", "Initial principal (KRW). Baseline for P&L calculation"),
        "PORTFOLIO_USD_CASH_BALANCE": ("0", "US foreign cash balance (USD). For allocation calc and report adjustment"),
        "STRATEGY_TARGET_CASH_RATIO_KR_BEAR": ("0.20", "KR market BEAR regime target cash ratio"),
        "STRATEGY_TARGET_CASH_RATIO_KR_NEUTRAL": ("0.40", "KR market NEUTRAL regime target cash ratio"),
        "STRATEGY_TARGET_CASH_RATIO_KR_BULL": ("0.50", "KR market BULL regime target cash ratio"),
        "STRATEGY_TARGET_CASH_RATIO_US_BEAR": ("0.20", "US market BEAR regime target cash ratio"),
        "STRATEGY_TARGET_CASH_RATIO_US_NEUTRAL": ("0.40", "US market NEUTRAL regime target cash ratio"),
        "STRATEGY_TARGET_CASH_RATIO_US_BULL": ("0.50", "US market BULL regime target cash ratio"),
        "DCF_EQUITY_RISK_PREMIUM": (str(Config.DCF_EQUITY_RISK_PREMIUM), "DCF equity risk premium (e.g. 5.5%=0.055)"),
        "DCF_DISCOUNT_RATE_FLOOR": (str(Config.DCF_DISCOUNT_RATE_FLOOR), "DCF discount rate floor (e.g. 6%=0.06)"),
        "DCF_DISCOUNT_RATE_CEIL": (str(Config.DCF_DISCOUNT_RATE_CEIL), "DCF discount rate ceiling (e.g. 15%=0.15)"),
        "DCF_DEFAULT_DISCOUNT_RATE": (str(Config.DCF_DEFAULT_DISCOUNT_RATE), "DCF default discount rate when beta unavailable (e.g. 10%=0.10)"),
        "DCF_STAGE1_YEARS": (str(Config.DCF_STAGE1_YEARS), "DCF stage-1 high-growth period (years)"),
        "DCF_TERMINAL_GROWTH": (str(Config.DCF_TERMINAL_GROWTH), "DCF terminal growth rate (e.g. 3%=0.03, GDP growth linked)")
    }

    @classmethod
    def init_defaults(cls):
        """Initialize default settings if not present in DB."""
        # 1. Insert only missing keys
        SettingsRepo.upsert_many(cls.DEFAULT_SETTINGS)
        # 2. Correct specific key values (old defaults -> new defaults)
        _corrections = {
            "STRATEGY_TAKE_PROFIT_PCT": ("5.0", "5", ""),
            "STRATEGY_STOP_LOSS_PCT": ("-10.0", "-10", ""),
        }
        for key, old_values in _corrections.items():
            current = SettingsRepo.get(key)
            if current in old_values:
                SettingsRepo.set(key, cls.DEFAULT_SETTINGS[key][0])
        # 3. Tick trading is always disabled on restart
        SettingsRepo.set("STRATEGY_TICK_ENABLED", "0")

    @classmethod
    def get_setting(cls, key: str, default=None):
        """Get setting value (30s TTL in-memory cache)."""
        now = time.time()
        cached = cls._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        value = SettingsRepo.get(key)
        if value is None:
            value = cls.DEFAULT_SETTINGS.get(key, (default,))[0]

        cls._cache[key] = (value, now + cls._CACHE_TTL)
        return value

    @classmethod
    def get_float(cls, key: str, default: float = 0.0) -> float:
        try:
            val = cls.get_setting(key)
            float_val = float(val) if val is not None else default
            if key == "STRATEGY_STOP_LOSS_PCT" and float_val >= 0:
                logger.warning(f"STRATEGY_STOP_LOSS_PCT value {float_val} is invalid (>=0). Overriding strictly to default {default}.")
                return default
            return float_val
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        try:
            val = cls.get_setting(key)
            return int(float(val)) if val is not None else default
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_bool(cls, key: str, default: bool = False) -> bool:
        try:
            val = str(cls.get_setting(key)).lower()
            if val in ("true", "1", "yes", "on"):
                return True
            if val in ("false", "0", "no", "off"):
                return False
            return default
        except (ValueError, TypeError):
            return default

    @classmethod
    def set_setting(cls, key: str, value: str):
        """Update setting value."""
        desc = cls.DEFAULT_SETTINGS.get(key, ("", ""))[1]
        result = SettingsRepo.set(key, str(value), desc)
        cls._cache.pop(key, None)  # Invalidate cache immediately on change
        if result:
            logger.info(f"⚙️ Setting updated: {key} = {value}")
        return result

    @classmethod
    def get_all_settings(cls) -> list:
        """Get all settings -> [{key, value, description}, ...] list."""
        cls.init_defaults()
        raw = SettingsRepo.get_all()
        return [{"key": k, **v} for k, v in raw.items()]

    @classmethod
    def get_tick_settings(cls) -> dict:
        """Get tick trading settings."""
        return {
            "enabled": cls.get_int("STRATEGY_TICK_ENABLED", 0) == 1,
            "ticker": cls.get_setting("STRATEGY_TICK_TICKER", "005930"),
            "cash_ratio": cls.get_float("STRATEGY_TICK_CASH_RATIO", 0.20),
            "entry_pct": cls.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0),
            "add_pct": cls.get_float("STRATEGY_TICK_ADD_PCT", -3.0),
            "take_profit_pct": cls.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0),
            "stop_loss_pct": cls.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0),
            "close_minutes": cls.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5),
        }

    @classmethod
    def update_tick_settings(cls, updates: dict) -> None:
        """Batch update tick trading settings."""
        for key, value in updates.items():
            cls.set_setting(key, value)
