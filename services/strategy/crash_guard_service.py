"""CrashGuardService — 단기 시장 충격 감지 및 매도/매수 가드.

Uptrend DCA 알고리즘의 안전망:
- Crash: KOSPI 1d<-5% OR 5d<-10% OR VIX>35 → 매도/손절 일시 보류
- Frozen: VIX>50 → 매수도 일시 보류 (panic 단계 추가 진입 방지)

KOSPI 5d 변화율 기반 per_trade multiplier + score boost 도 제공:
  tier1 (>= -3%): mult 1.0, score 0
  tier2 (< -3%):  mult 1.5, score -5
  tier3 (< -7%):  mult 2.0, score -10
  tier4 (< -12%): mult 2.5, score -15

5분 in-memory 캐시 (signal_cache 와 동일 주기).
"""
import time
from typing import Tuple, Optional

from services.config.settings_service import SettingsService
from utils.logger import get_logger

logger = get_logger("crash_guard")


class CrashGuardService:
    """급락 감지 및 보호 가드. macro_data 의 KOSPI/SPX 변화율 + VIX 사용."""

    _status_cache = None   # (is_crash, is_frozen, reasons[], cached_at_epoch)
    _CACHE_TTL_SEC = 60    # 1분 (매크로 갱신 주기에 맞춤)

    @classmethod
    def get_status(cls, macro_data) -> Tuple[bool, bool, list]:
        """현재 crash/frozen 상태 + reasons 반환. (is_crash, is_frozen, reasons[]).
        macro_data 가 None 이면 모두 False."""
        if macro_data is None:
            return False, False, []
        now = time.time()
        if cls._status_cache and (now - cls._status_cache[3]) < cls._CACHE_TTL_SEC:
            return cls._status_cache[0], cls._status_cache[1], cls._status_cache[2]

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

        is_crash = bool(reasons)
        is_frozen = bool(vix and vix > frozen_vix)
        if is_frozen and "frozen" not in str(reasons):
            reasons.append(f"frozen(vix>{frozen_vix})")

        cls._status_cache = (is_crash, is_frozen, reasons, now)
        return is_crash, is_frozen, reasons

    @classmethod
    def is_crash(cls, macro_data) -> bool:
        """매도/손절 보류 여부."""
        return cls.get_status(macro_data)[0]

    @classmethod
    def is_frozen(cls, macro_data) -> bool:
        """매수도 보류 여부 (VIX>50 panic)."""
        return cls.get_status(macro_data)[1]

    @classmethod
    def kospi_5d_tier(cls, macro_data) -> int:
        """KOSPI 5d 변화율 tier (1~4). per_trade mult / score boost 결정.
        tier1=정상, tier4=극심한 폭락."""
        if macro_data is None:
            return 1
        k5d = macro_data.kospi_change_5d
        if k5d is None:
            return 1
        t1 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER1", -3.0)
        t2 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER2", -7.0)
        t3 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER3", -12.0)
        t4 = SettingsService.get_float("STRATEGY_KOSPI_5D_MULT_TIER4", -18.0)
        if k5d < t4: return 4
        if k5d < t3: return 3
        if k5d < t2: return 2
        return 1

    @classmethod
    def per_trade_mult(cls, macro_data) -> float:
        """현재 KOSPI 5d tier 에 따른 per_trade ratio 배율."""
        tier = cls.kospi_5d_tier(macro_data)
        key = f"STRATEGY_KOSPI_5D_MULT_VAL{tier}"
        defaults = {1: 1.0, 2: 1.5, 3: 2.0, 4: 2.5}
        return SettingsService.get_float(key, defaults[tier])

    @classmethod
    def score_boost(cls, macro_data) -> int:
        """현재 KOSPI 5d tier 에 따른 score 가산 (음수 = BUY 방향)."""
        tier = cls.kospi_5d_tier(macro_data)
        key = f"STRATEGY_KOSPI_5D_SCORE_BOOST{tier}"
        defaults = {1: 0, 2: -5, 3: -10, 4: -15}
        return SettingsService.get_int(key, defaults[tier])
