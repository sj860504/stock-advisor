"""Uptrend DCA / 매매 규칙 순수 함수 모음 (I/O 없음, 순환 import 없음).

signal_service / position_service / execution_service_v2 / crash_guard_service 가 공유한다.
모든 파라미터는 SettingsService 키로 조정 가능하며 기본값은 settings_service.DEFAULT_SETTINGS 와 동일.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from services.config.settings_service import SettingsService


# ── 점수 스케일링 (D1) ──────────────────────────────────────────────────────────

def tanh_scaled(value: float, cap: float, scale: float) -> int:
    """선형 클램프 대신 cap × tanh(value/scale). scale 근처에서 ~76% 포화, 2×scale 에서 ~96%.
    value 부호 유지. scale<=0 이면 선형 클램프로 폴백."""
    if cap <= 0:
        return 0
    if scale is None or scale <= 0:
        return int(max(-cap, min(cap, value)))
    return int(round(cap * math.tanh(value / scale)))


def dcf_confidence_for_source(source: Optional[str]) -> float:
    """DCF 데이터 소스별 신뢰도 계수 (D1-4)."""
    table = {
        "override": 1.0,
        "five_year_cashflow": 0.9,
        "yfinance": 0.8,
        "analyst_target": 0.6,
        "eps_per_fallback": 0.5,
        "eps_per_fallback_api": 0.5,
        "kis": 0.4,
    }
    if not source:
        return 1.0
    return table.get(str(source), 0.7)


# ── 안전망 손절 (S4) ────────────────────────────────────────────────────────────

def uptrend_stop_loss_pct(atr_pct: Optional[float]) -> float:
    """Uptrend 안전망 손절 임계 (%). ATR 이 있으면 -mult×ATR 을 [floor, cap] 로 클램프,
    없으면 고정값 STRATEGY_UPTREND_STOP_LOSS_PCT."""
    fixed = SettingsService.get_float("STRATEGY_UPTREND_STOP_LOSS_PCT", -25.0)
    if not atr_pct or atr_pct <= 0:
        return fixed
    if SettingsService.get_int("STRATEGY_UPTREND_STOP_ATR_ENABLED", 1) != 1:
        return fixed
    mult = SettingsService.get_float("STRATEGY_UPTREND_STOP_ATR_MULT", 6.0)
    floor = SettingsService.get_float("STRATEGY_UPTREND_STOP_FLOOR_PCT", -35.0)   # 가장 깊은 값
    cap = SettingsService.get_float("STRATEGY_UPTREND_STOP_CAP_PCT", -15.0)       # 가장 얕은 값
    raw = -abs(mult) * float(atr_pct)
    return max(floor, min(cap, raw))


# ── 스케일아웃 / 잔여 trailing (S1, S2) ─────────────────────────────────────────

def parse_scale_out(spec: Optional[str] = None) -> List[Tuple[float, float]]:
    """'10:0.5,20:0.5,35:0.5' → [(10.0, 0.5), (20.0, 0.5), (35.0, 0.5)] (수익률 오름차순).
    파싱 실패/빈 값이면 레거시 단일 단계 [(PARTIAL_TAKE_PCT, PARTIAL_TAKE_RATIO)]."""
    if spec is None:
        spec = SettingsService.get_setting("STRATEGY_UPTREND_SCALE_OUT", "") or ""
    stages: List[Tuple[float, float]] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        a, b = part.split(":", 1)
        try:
            pct, ratio = float(a), float(b)
        except ValueError:
            continue
        if pct > 0 and 0 < ratio <= 1:
            stages.append((pct, ratio))
    if not stages:
        stages = [(
            SettingsService.get_float("STRATEGY_UPTREND_PARTIAL_TAKE_PCT", 10.0),
            SettingsService.get_float("STRATEGY_UPTREND_PARTIAL_TAKE_RATIO", 0.5),
        )]
    return sorted(stages)


def stages_done_count(value) -> int:
    """partial_take_done[ticker] 값 → 완료 단계 수. 레거시 bool True 는 1단계."""
    if value is None or value is False:
        return 0
    if value is True:
        return 1
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def trailing_pct_by_profit(max_profit_pct: float, default_pct: Optional[float] = None,
                           spec: Optional[str] = None) -> float:
    """잔여분 trailing 폭. 고점 수익률이 클수록 좁게. spec '20:-7,35:-5' (수익률:trailing%)."""
    if default_pct is None:
        default_pct = SettingsService.get_float("STRATEGY_UPTREND_TRAILING_REMAINING_PCT", -10.0)
    if spec is None:
        spec = SettingsService.get_setting("STRATEGY_UPTREND_TRAILING_BY_PROFIT", "") or ""
    best = default_pct
    best_thr = -1.0
    for part in str(spec).split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        a, b = part.split(":", 1)
        try:
            thr, pct = float(a), float(b)
        except ValueError:
            continue
        if max_profit_pct >= thr and thr > best_thr and pct < 0:
            best, best_thr = pct, thr
    return best


def trailing_floor_price(buy_price: float) -> float:
    """잔여 trailing 매도 하한가 = 매입가 × (1 + FLOOR%). 이 아래에서는 trailing 매도 안 함."""
    floor_pct = SettingsService.get_float("STRATEGY_UPTREND_TRAILING_FLOOR_PCT", 1.0)
    return float(buy_price) * (1 + floor_pct / 100.0)


# ── DCA (B4) ────────────────────────────────────────────────────────────────────

def dca_stages() -> List[Tuple[float, float, int]]:
    """[(threshold_pct, capital_ratio, key)] 깊은 단계 우선."""
    return [
        (SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE3_PCT", -15.0),
         SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE3_RATIO", 0.05), -15),
        (SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE2_PCT", -8.0),
         SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE2_RATIO", 0.04), -8),
        (SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE1_PCT", -3.0),
         SettingsService.get_float("STRATEGY_UPTREND_DCA_STAGE1_RATIO", 0.03), -3),
    ]


def dca_reference_mode() -> str:
    """'avg'(평균단가, 기본·시뮬 검증) | 'entry'(최초 진입가, 공격적)."""
    v = (SettingsService.get_setting("STRATEGY_UPTREND_DCA_REF", "avg") or "avg").strip().lower()
    return "entry" if v == "entry" else "avg"


# ── 매수 사이징 (B2) ────────────────────────────────────────────────────────────

def buy_confidence_multiplier(score: int) -> float:
    """BUY 신뢰도 승수: 점수가 낮을수록(강한 BUY) 크게. 기존 score≥80/90 승수는 매수 경로에서
    발동 불가(BUY 는 score≤30)여서 교체."""
    t1 = SettingsService.get_int("STRATEGY_BUY_CONFIDENCE_T1_SCORE", 10)
    t2 = SettingsService.get_int("STRATEGY_BUY_CONFIDENCE_T2_SCORE", 20)
    if score <= t1:
        return SettingsService.get_float("STRATEGY_BUY_CONFIDENCE_MULT_T1", 1.5)
    if score <= t2:
        return SettingsService.get_float("STRATEGY_BUY_CONFIDENCE_MULT_T2", 1.25)
    return 1.0


def volatility_multiplier(atr_pct: Optional[float]) -> float:
    """변동성 사이징: target_atr / atr, [min, max] 클램프. ATR 없으면 1.0."""
    if not atr_pct or atr_pct <= 0:
        return 1.0
    if SettingsService.get_int("STRATEGY_ATR_SIZING_ENABLED", 1) != 1:
        return 1.0
    target = SettingsService.get_float("STRATEGY_ATR_TARGET_PCT", 2.0)
    lo = SettingsService.get_float("STRATEGY_ATR_SIZING_MIN_MULT", 0.5)
    hi = SettingsService.get_float("STRATEGY_ATR_SIZING_MAX_MULT", 1.5)
    return max(lo, min(hi, target / float(atr_pct)))


# ── 급락 매수 확인 (D2) ──────────────────────────────────────────────────────────

def drop_confirmation_factor(change_rate: float, current_price: float, low_price: float,
                             volume: float, avg_volume_20d: float) -> float:
    """당일 급락(change_rate<0) 가산에 곱하는 계수.
    (a) 당일 저가 대비 회복률 ≥ RECOVERY% 또는 (b) 거래량 ≥ AVG×VOL_MULT → 1.0, 아니면 DAMPEN(0.5).
    저가/거래량 정보가 전혀 없으면 1.0 (판단 불가 → 기존 동작)."""
    if change_rate >= 0:
        return 1.0
    if SettingsService.get_int("STRATEGY_CHANGE_CONFIRM_ENABLED", 1) != 1:
        return 1.0
    have_low = bool(low_price and low_price > 0 and current_price and current_price > 0)
    have_vol = bool(volume and volume > 0 and avg_volume_20d and avg_volume_20d > 0)
    if not have_low and not have_vol:
        return 1.0
    recovery_ok = False
    if have_low:
        # 저가→현재가 회복폭을 (당일 낙폭) 대비 비율로: (cur-low)/low ÷ |change|
        rebound_pct = (current_price - low_price) / low_price * 100.0
        need = abs(change_rate) * SettingsService.get_float("STRATEGY_CHANGE_CONFIRM_RECOVERY_RATIO", 0.3)
        recovery_ok = rebound_pct >= need
    volume_ok = False
    if have_vol:
        volume_ok = volume >= avg_volume_20d * SettingsService.get_float("STRATEGY_CHANGE_CONFIRM_VOL_MULT", 1.5)
    if recovery_ok or volume_ok:
        return 1.0
    return SettingsService.get_float("STRATEGY_CHANGE_CONFIRM_DAMPEN", 0.5)
