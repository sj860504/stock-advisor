"""CrashGuardService — 단기 시장 충격 감지 및 매도/매수 가드.

Uptrend DCA 알고리즘의 안전망:
- Crash: KOSPI/SPX 1d<-5% OR 5d<-10% OR VIX>35 OR VIX 1d +20% 급등
         OR 장중 breadth(유니버스 등락률 중앙값 ≤ -3% AND 하락비율 ≥ 80%) → 매도/손절 일시 보류
- Frozen: VIX>50 → 매수도 일시 보류 (panic 단계 추가 진입 방지)
  · DCA 추매 / score 신규매수 / 자산관리 budget 매수 전부 차단
    (ExecutionServiceV2._execute_buy_order 에서 중앙 게이트)

지수 5d 변화율 기반 per_trade multiplier + score boost 도 제공.
  KR 종목 → KOSPI 5d, US 종목 → SPX 5d (SPX 없으면 KOSPI 폴백)
  tier1 (>= -3%): mult 1.0, score 0
  tier2 (< -3%):  mult 1.5, score -5
  tier3 (< -7%):  mult 2.0, score -10
  tier4 (< -12%): mult 2.5, score -15

상태 캐시는 입력값(지수 변화율, VIX)이 바뀌면 즉시 무효화 — 매크로 갱신 직후
옛 Crash/Frozen 판정이 최대 60초 남는 문제 방지.
"""
from typing import Optional, Tuple

from services.config.settings_service import SettingsService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("crash_guard")


class CrashGuardService:
    """급락 감지 및 보호 가드. macro_data 의 KOSPI/SPX 변화율 + VIX 사용."""

    _status_cache = None   # (inputs_key, is_crash, is_frozen, reasons[])

    @staticmethod
    def _inputs_key(macro_data) -> tuple:
        return (
            macro_data.vix, getattr(macro_data, "vix_change_1d", None),
            macro_data.kospi_change_1d, macro_data.kospi_change_5d,
            macro_data.spx_change_1d, macro_data.spx_change_5d,
            getattr(macro_data, "kr_breadth_median", None), getattr(macro_data, "kr_breadth_down_ratio", None),
            getattr(macro_data, "us_breadth_median", None), getattr(macro_data, "us_breadth_down_ratio", None),
        )

    @staticmethod
    def _breadth_crash(median, down_ratio, count) -> bool:
        """D3: 장중 breadth 기반 Crash — 유니버스 등락률 중앙값 ≤ N% AND 하락 비율 ≥ R (표본 ≥ MIN)."""
        if median is None or down_ratio is None:
            return False
        if (count or 0) < SettingsService.get_int("STRATEGY_CRASH_BREADTH_MIN_COUNT", 20):
            return False
        med_thr = SettingsService.get_float("STRATEGY_CRASH_BREADTH_MEDIAN_PCT", -3.0)
        ratio_thr = SettingsService.get_float("STRATEGY_CRASH_BREADTH_DOWN_RATIO", 0.8)
        return median <= med_thr and down_ratio >= ratio_thr

    @classmethod
    def get_status(cls, macro_data) -> Tuple[bool, bool, list]:
        """현재 crash/frozen 상태 + reasons 반환. (is_crash, is_frozen, reasons[]).
        macro_data 가 None 이면 모두 False."""
        if macro_data is None:
            return False, False, []
        key = cls._inputs_key(macro_data)
        if cls._status_cache and cls._status_cache[0] == key:
            return cls._status_cache[1], cls._status_cache[2], cls._status_cache[3]

        vix = macro_data.vix or 0
        # KOSPI / SPX 중 더 큰 하락을 사용 (양 시장 보호)
        k1d = macro_data.kospi_change_1d
        k5d = macro_data.kospi_change_5d
        s1d = macro_data.spx_change_1d
        s5d = macro_data.spx_change_5d
        worst_1d = min([x for x in (k1d, s1d) if x is not None], default=None)
        worst_5d = min([x for x in (k5d, s5d) if x is not None], default=None)

        idx_1d_thresh = SettingsService.get_float("STRATEGY_CRASH_INDEX_1D_PCT", -5.0)
        idx_5d_thresh = SettingsService.get_float("STRATEGY_CRASH_INDEX_5D_PCT", -10.0)
        vix_thresh    = SettingsService.get_float("STRATEGY_CRASH_VIX_LEVEL", 35.0)
        frozen_vix    = SettingsService.get_float("STRATEGY_CRASH_FROZEN_VIX", 50.0)

        reasons = []
        if worst_1d is not None and worst_1d < idx_1d_thresh:
            reasons.append(f"index_1d({worst_1d:.1f}%<{idx_1d_thresh}%)")
        if worst_5d is not None and worst_5d < idx_5d_thresh:
            reasons.append(f"index_5d({worst_5d:.1f}%<{idx_5d_thresh}%)")
        if vix and vix > vix_thresh:
            reasons.append(f"vix({vix:.1f}>{vix_thresh})")
        # D3-3: VIX 급등 (수준 미달이어도 전일 대비 +N%)
        vix_chg = getattr(macro_data, "vix_change_1d", None)
        vix_chg_thr = SettingsService.get_float("STRATEGY_CRASH_VIX_CHANGE_PCT", 20.0)
        if vix_chg is not None and vix_chg_thr > 0 and vix_chg >= vix_chg_thr:
            reasons.append(f"vix_spike({vix_chg:+.0f}%>={vix_chg_thr:.0f}%)")
        # D3-1: 장중 breadth (일봉 종가 확정 전에 급락 감지)
        if cls._breadth_crash(getattr(macro_data, "kr_breadth_median", None),
                              getattr(macro_data, "kr_breadth_down_ratio", None),
                              getattr(macro_data, "kr_breadth_count", 0)):
            reasons.append(f"kr_breadth(med{macro_data.kr_breadth_median:+.1f}%,down{macro_data.kr_breadth_down_ratio:.0%})")
        if cls._breadth_crash(getattr(macro_data, "us_breadth_median", None),
                              getattr(macro_data, "us_breadth_down_ratio", None),
                              getattr(macro_data, "us_breadth_count", 0)):
            reasons.append(f"us_breadth(med{macro_data.us_breadth_median:+.1f}%,down{macro_data.us_breadth_down_ratio:.0%})")

        is_crash = bool(reasons)
        is_frozen = bool(vix and vix > frozen_vix)
        if is_frozen:
            reasons.append(f"frozen(vix>{frozen_vix})")

        cls._status_cache = (key, is_crash, is_frozen, reasons)
        return is_crash, is_frozen, reasons

    @classmethod
    def is_crash(cls, macro_data) -> bool:
        """매도/손절 보류 여부."""
        return cls.get_status(macro_data)[0]

    @classmethod
    def is_frozen(cls, macro_data) -> bool:
        """매수도 보류 여부 (VIX>50 panic)."""
        return cls.get_status(macro_data)[1]

    # ── 지수 5d tier (KR→KOSPI, US→SPX) ─────────────────────────────────────────

    @staticmethod
    def index_label(ticker: Optional[str]) -> str:
        """tier 산출에 사용하는 지수 이름 (로그/reason 용)."""
        return "KOSPI" if (ticker is None or is_kr(ticker)) else "SPX"

    @classmethod
    def index_5d_change(cls, macro_data, ticker: Optional[str] = None) -> Optional[float]:
        """종목 시장에 맞는 지수 5d 변화율. KR/None → KOSPI, US → SPX(없으면 KOSPI 폴백)."""
        if macro_data is None:
            return None
        if ticker is not None and not is_kr(ticker):
            if macro_data.spx_change_5d is not None:
                return macro_data.spx_change_5d
        return macro_data.kospi_change_5d

    @classmethod
    def index_5d_tier(cls, macro_data, ticker: Optional[str] = None) -> int:
        """지수 5d 변화율 tier (1~4). per_trade mult / score boost 결정.
        tier1=정상, tier4=극심한 폭락."""
        chg_5d = cls.index_5d_change(macro_data, ticker)
        if chg_5d is None:
            return 1
        t2 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER2", -7.0)
        t3 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER3", -12.0)
        t4 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER4", -18.0)
        if chg_5d < t4: return 4
        if chg_5d < t3: return 3
        if chg_5d < t2: return 2
        return 1

    @classmethod
    def kospi_5d_tier(cls, macro_data) -> int:
        """KOSPI 기준 tier (UI 배지 등 하위 호환용)."""
        return cls.index_5d_tier(macro_data, ticker=None)

    @classmethod
    def per_trade_mult(cls, macro_data, ticker: Optional[str] = None) -> float:
        """종목 시장 지수 5d tier 에 따른 per_trade ratio 배율."""
        tier = cls.index_5d_tier(macro_data, ticker)
        key = f"STRATEGY_KOSPI_5D_MULT_VAL{tier}"
        defaults = {1: 1.0, 2: 1.5, 3: 2.0, 4: 2.5}
        return SettingsService.get_float(key, defaults[tier])

    @classmethod
    def score_boost(cls, macro_data, ticker: Optional[str] = None) -> int:
        """종목 시장 지수 5d tier 에 따른 score 가산 (음수 = BUY 방향)."""
        tier = cls.index_5d_tier(macro_data, ticker)
        key = f"STRATEGY_KOSPI_5D_SCORE_BOOST{tier}"
        defaults = {1: 0, 2: -5, 3: -10, 4: -15}
        return SettingsService.get_int(key, defaults[tier])
