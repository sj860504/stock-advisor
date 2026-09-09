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
        "STRATEGY_BUY_THRESHOLD": (str(Config.STRATEGY_BUY_THRESHOLD), "Buy score threshold (score ≤ N → BUY signal)"),
        "STRATEGY_SELL_THRESHOLD": (str(Config.STRATEGY_SELL_THRESHOLD), "Sell score threshold (score ≥ N → SELL signal)"),
        "STRATEGY_REQUIRE_FULL_ANALYSIS": (str(Config.STRATEGY_REQUIRE_FULL_ANALYSIS), "Block trading before full analysis ready (1=block)"),
        "STRATEGY_MIN_READY_RATIO": (str(Config.STRATEGY_MIN_READY_RATIO), "Minimum readiness ratio to allow trading (0.0~1.0)"),
        "STRATEGY_SPLIT_COUNT": (str(Config.STRATEGY_SPLIT_COUNT), "Split trade count"),
        "STRATEGY_STOP_LOSS_PCT": (str(Config.STRATEGY_STOP_LOSS_PCT), "Stop-loss return threshold (%)"),
        "STRATEGY_TAKE_PROFIT_PCT": (str(Config.STRATEGY_TAKE_PROFIT_PCT), "Take-profit return threshold (%)"),
        "STRATEGY_DIP_BUY_PCT": (str(Config.STRATEGY_DIP_BUY_PCT), "Dip buy threshold (%)"),
        "STRATEGY_OVERSOLD_RSI": (str(Config.STRATEGY_OVERSOLD_RSI), "Oversold RSI threshold"),
        "STRATEGY_OVERBOUGHT_RSI": (str(Config.STRATEGY_OVERBOUGHT_RSI), "Overbought RSI threshold"),
        "STRATEGY_ALLOW_EXTENDED_HOURS": ("1", "Allow US pre/after-market orders (1=allow, 0=disallow)"),
        "STRATEGY_POST_CLOSE_BUFFER_MIN": ("30", "정규장 마감 후 매매/전략 활성 유지 시간 (분)"),
        "STRATEGY_DCF_DEVIATION_CAP": ("25", "DCF 편차 점수 캡 (±)"),
        "STRATEGY_RSI_DEVIATION_CAP": ("15", "RSI 편차 점수 캡 (±)"),
        "STRATEGY_EMA200_DEVIATION_CAP": ("15", "EMA200 편차 점수 캡 (±)"),
        "STRATEGY_CHANGE_DEVIATION_CAP": ("15", "당일 등락률 편차 점수 캡 (±)"),
        "STRATEGY_VIX_DEVIATION_CAP": ("10", "VIX 편차 점수 캡 (±)"),
        "STRATEGY_FNG_DEVIATION_CAP": ("10", "Fear & Greed 편차 점수 캡 (±)"),
        "STRATEGY_REGIME_DEVIATION_CAP": ("10", "Regime score 편차 점수 캡 (±)"),
        "STRATEGY_GAP_RELAX_STEP": ("5", "현금갭 5%pa당 BUY threshold +N (cash-gap-aware)"),
        "STRATEGY_GAP_RELAX_MAX": ("10", "현금갭 기반 BUY threshold 최대 완화 (B1: 20→10, 중립 종목 매수 방지)"),
        "STRATEGY_COOLDOWN_HIGH_GAP_PCT": ("30", "쿨다운 단축 임계 (현금갭 %pa)"),
        "STRATEGY_BUY_COOLDOWN_HOURS": ("24", "기본 매수 쿨다운 (시간)"),
        "STRATEGY_BUY_COOLDOWN_HOURS_HIGH_GAP": ("1", "갭 큰 경우 매수 쿨다운 (시간)"),
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
        "DCF_TERMINAL_GROWTH": (str(Config.DCF_TERMINAL_GROWTH), "DCF terminal growth rate (e.g. 3%=0.03, GDP growth linked)"),
        # ────────────────────────────────────────────────────────────────────
        # Mode × Market × Regime 파라미터 (top100 / watchlist × KR / US × 4 regimes)
        # 키 컨벤션: STRATEGY_{MODE}_{MARKET}_{PARAM}_{REGIME}
        # Fallback 체인: 모드+시장+레짐 → 시장+레짐 → 레짐 → default
        # ────────────────────────────────────────────────────────────────────
        # ─── TOP100 × KR ───
        "STRATEGY_TOP100_KR_TAKE_PROFIT_PCT_BULL":      ("12.0", "[Top100·KR·Bull] 익절 기준 (%)"),
        "STRATEGY_TOP100_KR_TAKE_PROFIT_PCT_NEUTRAL":   ("7.0",  "[Top100·KR·Neutral] 익절 기준 (%)"),
        "STRATEGY_TOP100_KR_TAKE_PROFIT_PCT_WEAK_BEAR": ("5.0",  "[Top100·KR·Weak Bear] 익절 기준 (%)"),
        "STRATEGY_TOP100_KR_TAKE_PROFIT_PCT_BEAR":      ("3.0",  "[Top100·KR·Bear] 익절 기준 (%)"),
        "STRATEGY_TOP100_KR_STOP_LOSS_PCT_BULL":        ("-7.0", "[Top100·KR·Bull] 손절 기준 (%)"),
        "STRATEGY_TOP100_KR_STOP_LOSS_PCT_NEUTRAL":     ("-7.0", "[Top100·KR·Neutral] 손절 기준 (%) — 5월 검증값"),
        "STRATEGY_TOP100_KR_STOP_LOSS_PCT_WEAK_BEAR":   ("-3.0", "[Top100·KR·Weak Bear] 손절 기준 (%)"),
        "STRATEGY_TOP100_KR_STOP_LOSS_PCT_BEAR":        ("-5.0", "[Top100·KR·Bear] 손절 기준 (%)"),
        "STRATEGY_TOP100_KR_TRAILING_STOP_PCT_BULL":    ("-7.0", "[Top100·KR·Bull] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_KR_TRAILING_STOP_PCT_NEUTRAL": ("-5.0", "[Top100·KR·Neutral] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_KR_TRAILING_STOP_PCT_WEAK_BEAR":("-4.0","[Top100·KR·Weak Bear] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_KR_TRAILING_STOP_PCT_BEAR":    ("-5.0", "[Top100·KR·Bear] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_KR_TIGHT_STOP_TRIGGER_PCT":    ("3.0",  "[Top100·KR] Tight Stop 발동 임계 수익률 (%) — 5월 검증값"),
        "STRATEGY_TOP100_KR_TIGHT_STOP_PCT":            ("-3.0", "[Top100·KR] Tight Stop drawdown 임계 (%) — 5월 검증값"),
        "STRATEGY_TOP100_KR_STOP_LOSS_CONSECUTIVE_DAYS":("3",    "[Top100·KR] 연속 N거래일 손절 임계 초과시만 매도 (0=즉시)"),
        # ─── TOP100 × US ───
        "STRATEGY_TOP100_US_TAKE_PROFIT_PCT_BULL":      ("12.0", "[Top100·US·Bull] 익절 기준 (%)"),
        "STRATEGY_TOP100_US_TAKE_PROFIT_PCT_NEUTRAL":   ("7.0",  "[Top100·US·Neutral] 익절 기준 (%)"),
        "STRATEGY_TOP100_US_TAKE_PROFIT_PCT_WEAK_BEAR": ("5.0",  "[Top100·US·Weak Bear] 익절 기준 (%)"),
        "STRATEGY_TOP100_US_TAKE_PROFIT_PCT_BEAR":      ("3.0",  "[Top100·US·Bear] 익절 기준 (%)"),
        "STRATEGY_TOP100_US_STOP_LOSS_PCT_BULL":        ("-7.0", "[Top100·US·Bull] 손절 기준 (%)"),
        "STRATEGY_TOP100_US_STOP_LOSS_PCT_NEUTRAL":     ("-5.0", "[Top100·US·Neutral] 손절 기준 (%) — 시뮬 검증값"),
        "STRATEGY_TOP100_US_STOP_LOSS_PCT_WEAK_BEAR":   ("-3.0", "[Top100·US·Weak Bear] 손절 기준 (%)"),
        "STRATEGY_TOP100_US_STOP_LOSS_PCT_BEAR":        ("-5.0", "[Top100·US·Bear] 손절 기준 (%)"),
        "STRATEGY_TOP100_US_TRAILING_STOP_PCT_BULL":    ("-7.0", "[Top100·US·Bull] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_US_TRAILING_STOP_PCT_NEUTRAL": ("-5.0", "[Top100·US·Neutral] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_US_TRAILING_STOP_PCT_WEAK_BEAR":("-4.0","[Top100·US·Weak Bear] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_US_TRAILING_STOP_PCT_BEAR":    ("-5.0", "[Top100·US·Bear] 트레일링 스탑 (%)"),
        "STRATEGY_TOP100_US_TIGHT_STOP_TRIGGER_PCT":    ("3.0",  "[Top100·US] Tight Stop 발동 임계 수익률 (%)"),
        "STRATEGY_TOP100_US_TIGHT_STOP_PCT":            ("-3.0", "[Top100·US] Tight Stop drawdown 임계 (%)"),
        "STRATEGY_TOP100_US_STOP_LOSS_CONSECUTIVE_DAYS":("0",    "[Top100·US] 연속 N거래일 손절 (0=즉시 — US는 시뮬에서 즉시 손절이 유리)"),
        # ─── WATCHLIST × KR ───
        "STRATEGY_WATCHLIST_KR_TAKE_PROFIT_PCT_BULL":      ("15.0", "[Watchlist·KR·Bull] 익절 (사용자 종목은 더 욕심)"),
        "STRATEGY_WATCHLIST_KR_TAKE_PROFIT_PCT_NEUTRAL":   ("10.0", "[Watchlist·KR·Neutral] 익절"),
        "STRATEGY_WATCHLIST_KR_TAKE_PROFIT_PCT_WEAK_BEAR": ("7.0",  "[Watchlist·KR·Weak Bear] 익절"),
        "STRATEGY_WATCHLIST_KR_TAKE_PROFIT_PCT_BEAR":      ("5.0",  "[Watchlist·KR·Bear] 익절"),
        "STRATEGY_WATCHLIST_KR_STOP_LOSS_PCT_BULL":        ("-10.0","[Watchlist·KR·Bull] 손절"),
        "STRATEGY_WATCHLIST_KR_STOP_LOSS_PCT_NEUTRAL":     ("-8.0", "[Watchlist·KR·Neutral] 손절 (더 관대)"),
        "STRATEGY_WATCHLIST_KR_STOP_LOSS_PCT_WEAK_BEAR":   ("-5.0", "[Watchlist·KR·Weak Bear] 손절"),
        "STRATEGY_WATCHLIST_KR_STOP_LOSS_PCT_BEAR":        ("-7.0", "[Watchlist·KR·Bear] 손절"),
        "STRATEGY_WATCHLIST_KR_TRAILING_STOP_PCT_BULL":    ("-10.0","[Watchlist·KR·Bull] 트레일링"),
        "STRATEGY_WATCHLIST_KR_TRAILING_STOP_PCT_NEUTRAL": ("-7.0", "[Watchlist·KR·Neutral] 트레일링"),
        "STRATEGY_WATCHLIST_KR_TRAILING_STOP_PCT_WEAK_BEAR":("-5.0","[Watchlist·KR·Weak Bear] 트레일링"),
        "STRATEGY_WATCHLIST_KR_TRAILING_STOP_PCT_BEAR":    ("-7.0", "[Watchlist·KR·Bear] 트레일링"),
        "STRATEGY_WATCHLIST_KR_TIGHT_STOP_TRIGGER_PCT":    ("5.0",  "[Watchlist·KR] Tight Stop 발동 임계 (%)"),
        "STRATEGY_WATCHLIST_KR_TIGHT_STOP_PCT":            ("-3.0", "[Watchlist·KR] Tight Stop drawdown (%)"),
        "STRATEGY_WATCHLIST_KR_STOP_LOSS_CONSECUTIVE_DAYS":("3",    "[Watchlist·KR] 연속 N거래일 손절 (0=즉시)"),
        # ─── WATCHLIST × US ───
        "STRATEGY_WATCHLIST_US_TAKE_PROFIT_PCT_BULL":      ("15.0", "[Watchlist·US·Bull] 익절"),
        "STRATEGY_WATCHLIST_US_TAKE_PROFIT_PCT_NEUTRAL":   ("10.0", "[Watchlist·US·Neutral] 익절"),
        "STRATEGY_WATCHLIST_US_TAKE_PROFIT_PCT_WEAK_BEAR": ("7.0",  "[Watchlist·US·Weak Bear] 익절"),
        "STRATEGY_WATCHLIST_US_TAKE_PROFIT_PCT_BEAR":      ("5.0",  "[Watchlist·US·Bear] 익절"),
        "STRATEGY_WATCHLIST_US_STOP_LOSS_PCT_BULL":        ("-10.0","[Watchlist·US·Bull] 손절"),
        "STRATEGY_WATCHLIST_US_STOP_LOSS_PCT_NEUTRAL":     ("-8.0", "[Watchlist·US·Neutral] 손절"),
        "STRATEGY_WATCHLIST_US_STOP_LOSS_PCT_WEAK_BEAR":   ("-5.0", "[Watchlist·US·Weak Bear] 손절"),
        "STRATEGY_WATCHLIST_US_STOP_LOSS_PCT_BEAR":        ("-7.0", "[Watchlist·US·Bear] 손절"),
        "STRATEGY_WATCHLIST_US_TRAILING_STOP_PCT_BULL":    ("-10.0","[Watchlist·US·Bull] 트레일링"),
        "STRATEGY_WATCHLIST_US_TRAILING_STOP_PCT_NEUTRAL": ("-7.0", "[Watchlist·US·Neutral] 트레일링"),
        "STRATEGY_WATCHLIST_US_TRAILING_STOP_PCT_WEAK_BEAR":("-5.0","[Watchlist·US·Weak Bear] 트레일링"),
        "STRATEGY_WATCHLIST_US_TRAILING_STOP_PCT_BEAR":    ("-7.0", "[Watchlist·US·Bear] 트레일링"),
        "STRATEGY_WATCHLIST_US_TIGHT_STOP_TRIGGER_PCT":    ("5.0",  "[Watchlist·US] Tight Stop 발동 임계 (%)"),
        "STRATEGY_WATCHLIST_US_TIGHT_STOP_PCT":            ("-3.0", "[Watchlist·US] Tight Stop drawdown (%)"),
        "STRATEGY_WATCHLIST_US_STOP_LOSS_CONSECUTIVE_DAYS":("0",    "[Watchlist·US] 연속 N거래일 손절 (0=즉시)"),
        # ────────────────────────────────────────────────────────────────────
        # Uptrend DCA Algorithm — 우상향 가정 저점 매수 전략
        # ────────────────────────────────────────────────────────────────────
        # Master switches
        "STRATEGY_UPTREND_DCA_ENABLED":          ("1",     "[Uptrend] 알고리즘 활성화 (0=레거시 사용)"),
        # 부분 익절
        "STRATEGY_UPTREND_PARTIAL_TAKE_PCT":     ("10.0",  "[Uptrend] 부분익절 발동 수익률 (%)"),
        "STRATEGY_UPTREND_PARTIAL_TAKE_RATIO":   ("0.5",   "[Uptrend] 부분익절 비율 (0.5=50%)"),
        "STRATEGY_UPTREND_TRAILING_REMAINING_PCT": ("-10.0", "[Uptrend] 잔여분 trailing stop (%)"),
        # 손절
        "STRATEGY_UPTREND_STOP_LOSS_PCT":        ("-25.0", "[Uptrend] 손절 임계 (%) — 깊게"),
        "STRATEGY_UPTREND_STOP_LOSS_DAYS":       ("7",     "[Uptrend] 손절 연속 거래일 — 길게"),
        # DCA (3단계)
        "STRATEGY_UPTREND_DCA_STAGE1_PCT":       ("-3.0",  "[Uptrend] DCA 1단계 손실 임계 (%)"),
        "STRATEGY_UPTREND_DCA_STAGE1_RATIO":     ("0.03",  "[Uptrend] DCA 1단계 자본 비중 (3%)"),
        "STRATEGY_UPTREND_DCA_STAGE2_PCT":       ("-8.0",  "[Uptrend] DCA 2단계 손실 임계 (%)"),
        "STRATEGY_UPTREND_DCA_STAGE2_RATIO":     ("0.04",  "[Uptrend] DCA 2단계 자본 비중 (4%)"),
        "STRATEGY_UPTREND_DCA_STAGE3_PCT":       ("-15.0", "[Uptrend] DCA 3단계 손실 임계 (%)"),
        "STRATEGY_UPTREND_DCA_STAGE3_RATIO":     ("0.05",  "[Uptrend] DCA 3단계 자본 비중 (5%)"),
        # Crash 감지 (매도/손절 보류)
        "STRATEGY_CRASH_INDEX_1D_PCT":           ("-5.0",  "[Crash] 지수 1일 변화율 임계 (%)"),
        "STRATEGY_CRASH_INDEX_5D_PCT":           ("-10.0", "[Crash] 지수 5일 변화율 임계 (%)"),
        "STRATEGY_CRASH_VIX_LEVEL":              ("35.0",  "[Crash] VIX 절대값 임계"),
        "STRATEGY_CRASH_FROZEN_VIX":             ("50.0",  "[Crash] Frozen(매수도 보류) VIX 임계"),
        # KOSPI 5d 보너스 (per_trade multiplier + score boost)
        "STRATEGY_KOSPI_5D_MULT_TIER1":          ("-3.0",  "[KOSPI 5d] 1단계 임계 (%)"),
        "STRATEGY_KOSPI_5D_MULT_TIER2":          ("-7.0",  "[KOSPI 5d] 2단계 임계 (%)"),
        "STRATEGY_KOSPI_5D_MULT_TIER3":          ("-12.0", "[KOSPI 5d] 3단계 임계 (%)"),
        "STRATEGY_KOSPI_5D_MULT_TIER4":          ("-18.0", "[KOSPI 5d] 4단계 임계 (%)"),
        "STRATEGY_KOSPI_5D_MULT_VAL1":           ("1.0",   "[KOSPI 5d] tier1 per_trade 배율"),
        "STRATEGY_KOSPI_5D_MULT_VAL2":           ("1.5",   "[KOSPI 5d] tier2 per_trade 배율"),
        "STRATEGY_KOSPI_5D_MULT_VAL3":           ("2.0",   "[KOSPI 5d] tier3 per_trade 배율"),
        "STRATEGY_KOSPI_5D_MULT_VAL4":           ("2.5",   "[KOSPI 5d] tier4 per_trade 배율"),
        "STRATEGY_KOSPI_5D_SCORE_BOOST1":        ("0",     "[KOSPI 5d] tier1 score 가산 (BUY 방향, 음수)"),
        "STRATEGY_KOSPI_5D_SCORE_BOOST2":        ("-5",    "[KOSPI 5d] tier2 score 가산"),
        "STRATEGY_KOSPI_5D_SCORE_BOOST3":        ("-10",   "[KOSPI 5d] tier3 score 가산"),
        "STRATEGY_KOSPI_5D_SCORE_BOOST4":        ("-15",   "[KOSPI 5d] tier4 score 가산"),
        # Safety
        "STRATEGY_UPTREND_MAX_POSITION_PCT":     ("0.15",  "[Uptrend] 종목당 최대 자본 비중 (15%)"),
        "STRATEGY_UPTREND_MIN_CASH_RATIO":       ("0.0",   "[Uptrend] 최저 현금 비율 (0=풀투자 허용) — DCA 추매가 사용 가능한 하한"),
        "STRATEGY_UPTREND_DCA_RESERVE_RATIO":    ("0.10",  "[Uptrend] DCA 예비현금 비율 — score/budget 일반 매수는 이 비율 이상 현금 유지, DCA 추매만 MIN_CASH 까지 사용 (0=예비 없음)"),
        "STRATEGY_UPTREND_RESERVE_ZERO_TIER":    ("3",     "[Uptrend] 지수 5d tier 가 이 값 이상(폭락)이면 예비현금 0% 로 전환 (0=비활성)"),
        "STRATEGY_UPTREND_DCA_REF":              ("avg",   "[Uptrend] DCA 단계 기준가: avg(평균단가, 시뮬 검증) | entry(최초 진입가, 공격적)"),
        "STRATEGY_UPTREND_DCA_MAX_STAGES_PER_DAY": ("1",   "[Uptrend] 종목당 하루 최대 DCA 단계 수"),
        "STRATEGY_UPTREND_TRAILING_FLOOR_PCT":   ("1.0",   "[Uptrend] 잔여 trailing 매도 하한 = 매입가 × (1+N%) — 그 아래에선 trailing 매도 안 함 (손실 확정 차단)"),
        "STRATEGY_UPTREND_SCALE_OUT":            ("10:0.5,20:0.5,35:0.5", "[Uptrend] 다단계 스케일아웃 '수익률:비율,…' (각 단계는 현재 보유의 비율 매도)"),
        "STRATEGY_UPTREND_TRAILING_BY_PROFIT":   ("20:-7,35:-5", "[Uptrend] 고점 수익률 구간별 잔여 trailing 폭 '수익률:trailing%,…' (기본 TRAILING_REMAINING_PCT)"),
        "STRATEGY_UPTREND_SCORE_SELL_MIN_PROFIT": ("3.0",  "[Uptrend] score 매도(≥SELL_THRESHOLD) 허용 최소 수익률 (%) — 손실 구간 score 매도 금지"),
        "STRATEGY_UPTREND_STOP_ATR_ENABLED":     ("1",     "[Uptrend] 안전망 손절을 ATR 기반으로 (1) / 고정 STOP_LOSS_PCT (0)"),
        "STRATEGY_UPTREND_STOP_ATR_MULT":        ("6.0",   "[Uptrend] 안전망 손절 = -MULT × ATR%"),
        "STRATEGY_UPTREND_STOP_FLOOR_PCT":       ("-35.0", "[Uptrend] ATR 손절 가장 깊은 값"),
        "STRATEGY_UPTREND_STOP_CAP_PCT":         ("-15.0", "[Uptrend] ATR 손절 가장 얕은 값"),
        "STRATEGY_RELATIVE_WEAKNESS_ENABLED":    ("1",     "[Sell] 상대 약세 정리: 장기 보유 + 지수 대비 큰 부진 + 지수 회복 중 → 일부 정리"),
        "STRATEGY_RELATIVE_WEAKNESS_MIN_DAYS":   ("90",    "[Sell] 상대 약세 판정 최소 보유 캘린더일"),
        "STRATEGY_RELATIVE_WEAKNESS_UNDERPERF_PCT": ("-20.0", "[Sell] (종목 수익률 − 지수 90d 수익률) 임계 (%p)"),
        "STRATEGY_RELATIVE_WEAKNESS_INDEX_20D_MIN": ("0.0", "[Sell] 지수 20d 변화율이 이 값 이상(회복 중)일 때만"),
        "STRATEGY_RELATIVE_WEAKNESS_SELL_RATIO": ("0.5",   "[Sell] 상대 약세 정리 비율"),
        "STRATEGY_MAX_NEW_BUYS_PER_LOOP":        ("2",     "[Buy] 루프당 신규 종목 매수 최대 건수 (점수 낮은 순)"),
        "STRATEGY_BUY_CONFIDENCE_T1_SCORE":      ("10",    "[Buy] 신뢰도 승수 1단계 점수 (score ≤ N)"),
        "STRATEGY_BUY_CONFIDENCE_MULT_T1":       ("1.5",   "[Buy] 신뢰도 승수 1단계 배율"),
        "STRATEGY_BUY_CONFIDENCE_T2_SCORE":      ("20",    "[Buy] 신뢰도 승수 2단계 점수"),
        "STRATEGY_BUY_CONFIDENCE_MULT_T2":       ("1.25",  "[Buy] 신뢰도 승수 2단계 배율"),
        "STRATEGY_ATR_SIZING_ENABLED":           ("1",     "[Buy] 변동성(ATR) 사이징 활성"),
        "STRATEGY_ATR_TARGET_PCT":               ("2.0",   "[Buy] 목표 ATR% — per_trade × (TARGET/ATR)"),
        "STRATEGY_ATR_SIZING_MIN_MULT":          ("0.5",   "[Buy] ATR 사이징 최소 배율"),
        "STRATEGY_ATR_SIZING_MAX_MULT":          ("1.5",   "[Buy] ATR 사이징 최대 배율"),
        "STRATEGY_SPLIT_TRANCHE_DROP_PCT":       ("-2.0",  "[Buy] 분할매수 다음 트랜치 조건: 전 트랜치가 대비 N% 하락 OR 다음 거래일"),
        "STRATEGY_MAX_GROUP_RATIO":              ("0.30",  "[Buy] 동일 업종(industry) 그룹 최대 자본 비중 — 초과분만큼 매수 수량 축소"),
        "STRATEGY_REENTRY_RECOVERY_PCT":         ("3.0",   "[Buy] 손절(panic_lock) 후 재진입: 손절가 대비 +N% 회복 확인"),
        "STRATEGY_REENTRY_DROP_PCT":             ("-5.0",  "[Buy] 매도 후 재매수 허용 하락률 (매도가 대비)"),
        "STRATEGY_REENTRY_DAYS":                 ("5",     "[Buy] 매도 후 재매수 차단 일수"),
        "STRATEGY_PRICE_STALE_SEC":              ("600",   "[Detect] 가격 last_updated 경과 N초 초과 시 신호 생성 스킵 (0=비활성)"),
        "STRATEGY_CHANGE_CONFIRM_ENABLED":       ("1",     "[Detect] 급락 매수 가산은 반등/거래량 확인 시에만 전액"),
        "STRATEGY_CHANGE_CONFIRM_RECOVERY_RATIO": ("0.3",  "[Detect] 저가 대비 회복폭 ≥ 당일 낙폭 × N"),
        "STRATEGY_CHANGE_CONFIRM_VOL_MULT":      ("1.5",   "[Detect] 거래량 ≥ 20일 평균 × N"),
        "STRATEGY_CHANGE_CONFIRM_DAMPEN":        ("0.5",   "[Detect] 확인 실패 시 급락 가산 계수"),
        "STRATEGY_DCF_TANH_SCALE":               ("20.0",  "[Detect] DCF 점수 tanh 스케일 (%; 0=선형 클램프)"),
        "STRATEGY_RSI_TANH_SCALE":               ("15.0",  "[Detect] RSI 점수 tanh 스케일 (0=선형 클램프)"),
        "STRATEGY_DCF_CONFIDENCE_ENABLED":       ("1",     "[Detect] DCF 소스 신뢰도 계수 적용"),
        "STRATEGY_MARKET_ADJ_CAP":               ("20",    "[Detect] 시장 컴포넌트(VIX/F&G/Regime/지수) 합산 캡 ±N"),
        "STRATEGY_CRASH_BREADTH_MEDIAN_PCT":     ("-3.0",  "[Crash] 장중 breadth: 유니버스 등락률 중앙값 ≤ N%"),
        "STRATEGY_CRASH_BREADTH_DOWN_RATIO":     ("0.8",   "[Crash] 장중 breadth: 하락 종목 비율 ≥ N"),
        "STRATEGY_CRASH_BREADTH_MIN_COUNT":      ("20",    "[Crash] 장중 breadth 판정 최소 종목 수"),
        "STRATEGY_CRASH_VIX_CHANGE_PCT":         ("20.0",  "[Crash] VIX 전일 대비 +N% 급등이면 Crash"),
        "STRATEGY_REGIME_BULL_THRESHOLD":        ("65",    "[Regime] Bull 기본 임계 (직전 Bear 1개월 → +2, 2개월 → +5)"),
        "STRATEGY_SHADOW":                       ("0",     "[Exec] 1=주문 대신 trade_history(status=shadow) 기록만 (섀도우 모드)"),
        "STRATEGY_SELL_MERGE_BEFORE_CLOSE_MIN":  ("10",    "[Sell] 장 마감 N분 전이면 분할매도 잔여 트랜치를 1회로 합쳐 처리 (0=비활성)"),
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
            # Uptrend 부분익절 임계 15 → 10 (옛 값 강제 갱신)
            "STRATEGY_UPTREND_PARTIAL_TAKE_PCT": ("15.0", "15", ""),
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
