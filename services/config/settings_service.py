import time
from models.settings import Settings
from services.market.stock_meta_service import StockMetaService
from config import Config
from utils.logger import get_logger

logger = get_logger("settings_service")

class SettingsService:
    """
    시스템 설정 관리 서비스
    """
    _cache: dict = {}       # {key: (value, expire_time)}
    _CACHE_TTL: int = 30    # 30초 TTL — 변경 후 최대 30초 내 반영
    
    # 기본 설정값 정의 (Config에서 가져옴)
    DEFAULT_SETTINGS = {
        "STRATEGY_TARGET_CASH_RATIO": (str(Config.STRATEGY_TARGET_CASH_RATIO), "목표 현금 비중 (0.0 ~ 1.0)"),
        "STRATEGY_PER_TRADE_RATIO": (str(Config.STRATEGY_PER_TRADE_RATIO), "1회 매매 비중 (자산 대비)"),
        "STRATEGY_BASE_SCORE": (str(Config.STRATEGY_BASE_SCORE), "기본 점수"),
        "STRATEGY_BUY_THRESHOLD_MIN": (str(Config.STRATEGY_BUY_THRESHOLD_MIN), "매수 점수 하한 (기본 30)"),
        "STRATEGY_BUY_THRESHOLD": (str(Config.STRATEGY_BUY_THRESHOLD), "매수 점수 임계값"),
        "STRATEGY_SELL_THRESHOLD": (str(Config.STRATEGY_SELL_THRESHOLD), "매도 점수 임계값"),
        "STRATEGY_SELL_THRESHOLD_MAX": (str(Config.STRATEGY_SELL_THRESHOLD_MAX), "매도 점수 상한 (기본 100)"),
        "STRATEGY_REQUIRE_FULL_ANALYSIS": (str(Config.STRATEGY_REQUIRE_FULL_ANALYSIS), "전체 분석 준비 전 매매 금지(1=금지)"),
        "STRATEGY_MIN_READY_RATIO": (str(Config.STRATEGY_MIN_READY_RATIO), "매매 허용 최소 준비율(0.0~1.0)"),
        "STRATEGY_SPLIT_COUNT": (str(Config.STRATEGY_SPLIT_COUNT), "분할 매매 횟수"),
        "STRATEGY_STOP_LOSS_PCT": (str(Config.STRATEGY_STOP_LOSS_PCT), "손절 기준 수익률 (%)"),
        "STRATEGY_TAKE_PROFIT_PCT": (str(Config.STRATEGY_TAKE_PROFIT_PCT), "익절 기준 수익률 (%)"),
        "STRATEGY_DIP_BUY_PCT": (str(Config.STRATEGY_DIP_BUY_PCT), "급락 매수 기준 (%)"),
        "STRATEGY_OVERSOLD_RSI": (str(Config.STRATEGY_OVERSOLD_RSI), "과매도 RSI 기준"),
        "STRATEGY_OVERBOUGHT_RSI": (str(Config.STRATEGY_OVERBOUGHT_RSI), "과매수 RSI 기준"),
        "STRATEGY_ALLOW_EXTENDED_HOURS": ("1", "미국 프리/애프터마켓 주문 허용(1=허용,0=미허용)"),
        "STRATEGY_TICK_ENABLED": ("0", "틱매매 전략 사용 여부(1=사용,0=미사용)"),
        "STRATEGY_TICK_TICKER": ("005930", "틱매매 대상 티커 (하루 1종목)"),
        "STRATEGY_TICK_CASH_RATIO": ("0.20", "틱매매에 사용할 여유 현금 비중(총자산 대비)"),
        "STRATEGY_TICK_ENTRY_PCT": ("-1.0", "틱매매 1차 진입 기준 등락률(%)"),
        "STRATEGY_TICK_ADD_PCT": ("-3.0", "틱매매 2차 추가매수 기준 등락률(%)"),
        "STRATEGY_TICK_TAKE_PROFIT_PCT": ("1.0", "틱매매 익절 기준 수익률(%)"),
        "STRATEGY_TICK_STOP_LOSS_PCT": ("-5.0", "틱매매 손절 기준 수익률(%)"),
        "STRATEGY_TICK_CLOSE_MINUTES": ("5", "장마감 전 현금화 시도 분"),
        "PORTFOLIO_INITIAL_PRINCIPAL": ("10000000", "초기 원금(원). 원금 대비 손익 계산 기준값"),
        "PORTFOLIO_USD_CASH_BALANCE": ("0", "미국 외화 현금 잔고(USD). 비중 계산 및 보고 보정용"),
        "STRATEGY_TARGET_CASH_RATIO_KR_BEAR": ("0.20", "한국 시장 BEAR 국면 목표 현금 비중"),
        "STRATEGY_TARGET_CASH_RATIO_KR_NEUTRAL": ("0.40", "한국 시장 NEUTRAL 국면 목표 현금 비중"),
        "STRATEGY_TARGET_CASH_RATIO_KR_BULL": ("0.50", "한국 시장 BULL 국면 목표 현금 비중"),
        "STRATEGY_TARGET_CASH_RATIO_US_BEAR": ("0.20", "미국 시장 BEAR 국면 목표 현금 비중"),
        "STRATEGY_TARGET_CASH_RATIO_US_NEUTRAL": ("0.40", "미국 시장 NEUTRAL 국면 목표 현금 비중"),
        "STRATEGY_TARGET_CASH_RATIO_US_BULL": ("0.50", "미국 시장 BULL 국면 목표 현금 비중"),
        "DCF_EQUITY_RISK_PREMIUM": (str(Config.DCF_EQUITY_RISK_PREMIUM), "DCF 주식 위험 프리미엄(예: 5.5%=0.055)"),
        "DCF_DISCOUNT_RATE_FLOOR": (str(Config.DCF_DISCOUNT_RATE_FLOOR), "DCF 할인율 하한(예: 6%=0.06)"),
        "DCF_DISCOUNT_RATE_CEIL": (str(Config.DCF_DISCOUNT_RATE_CEIL), "DCF 할인율 상한(예: 15%=0.15)"),
        "DCF_DEFAULT_DISCOUNT_RATE": (str(Config.DCF_DEFAULT_DISCOUNT_RATE), "DCF beta 미제공 시 기본 할인율(예: 10%=0.10)"),
        "DCF_STAGE1_YEARS": (str(Config.DCF_STAGE1_YEARS), "DCF 1단계 고성장 기간(년)")
    }

    @classmethod
    def init_defaults(cls):
        """기본 설정값이 DB에 없으면 초기화"""
        session = StockMetaService.get_session()
        try:
            for key, (default_val, desc) in cls.DEFAULT_SETTINGS.items():
                setting = session.query(Settings).filter_by(key=key).first()
                if not setting:
                    setting = Settings(key=key, value=default_val, description=desc)
                    session.add(setting)
                else:
                    # 기본값이 변경된 경우(예: 익절 5%→3%, 손절 -10%→-8%)이고 사용자가 직접 수정하지 않았다면 함께 갱신
                    if key == "STRATEGY_TAKE_PROFIT_PCT" and setting.value in ("5.0", "5", ""):
                        setting.value = default_val
                    if key == "STRATEGY_STOP_LOSS_PCT" and setting.value in ("-10.0", "-10", ""):
                        setting.value = default_val
                    # 틱매매는 재시작 시 항상 비활성화 — 명시적으로 켤 때만 동작
                    if key == "STRATEGY_TICK_ENABLED":
                        setting.value = "0"
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error initializing default settings: {e}")

    @classmethod
    def get_setting(cls, key: str, default=None):
        """설정값 조회 (30초 TTL 인메모리 캐시)"""
        now = time.time()
        cached = cls._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        session = StockMetaService.get_session()
        setting = session.query(Settings).filter_by(key=key).first()
        if setting:
            value = setting.value
        elif key in cls.DEFAULT_SETTINGS:
            value = cls.DEFAULT_SETTINGS[key][0]
        else:
            value = default

        cls._cache[key] = (value, now + cls._CACHE_TTL)
        return value

    @classmethod
    def get_float(cls, key: str, default: float = 0.0) -> float:
        try:
            val = cls.get_setting(key)
            return float(val) if val is not None else default
        except:
            return default

    @classmethod
    def get_int(cls, key: str, default: int = 0) -> int:
        try:
            val = cls.get_setting(key)
            return int(float(val)) if val is not None else default
        except:
            return default

    @classmethod
    def set_setting(cls, key: str, value: str):
        """설정값 변경"""
        session = StockMetaService.get_session()
        try:
            setting = session.query(Settings).filter_by(key=key).first()
            if setting:
                setting.value = str(value)
            else:
                desc = cls.DEFAULT_SETTINGS.get(key, ("", ""))[1]
                setting = Settings(key=key, value=str(value), description=desc)
                session.add(setting)
            session.commit()
            cls._cache.pop(key, None)  # 변경 시 캐시 즉시 무효화
            logger.info(f"⚙️ Setting updated: {key} = {value}")
            return setting
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error setting value for {key}: {e}")
            return None

    @classmethod
    def get_all_settings(cls):
        """전체 설정 조회"""
        # 먼저 초기화 보장
        cls.init_defaults()
        
        session = StockMetaService.get_session()
        settings = session.query(Settings).all()
        return {setting.key: {"value": setting.value, "description": setting.description} for setting in settings}
