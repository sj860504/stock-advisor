"""2026-09-09 알고리즘 개선 계획(감지·매수·매도 사이클) 회귀 테스트.
DB 없이 SettingsService 를 DEFAULT_SETTINGS + overrides 로 패치.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schemas import (
    BuyCooldownEntry, HoldingSchema, MacroDataSnapshot, MarketRegimeSchema, SignalSchema,
    SplitOrderState, TradeResult, UserState, ExecutionConfig,
)
from models.ticker_state import TickerState
from services.analysis.indicator_service import IndicatorService
from services.config.settings_service import SettingsService
from services.market.macro_service import MacroService
from services.market.market_data_service import MarketDataService
from services.strategy import uptrend_rules as R
from services.strategy.crash_guard_service import CrashGuardService
from services.strategy.execution_service_v2 import TradeExecutorService
from services.strategy.position_service import PositionService
from services.strategy.signal_service import SignalService


class _SettingsStub:
    def __init__(self, **overrides):
        self.overrides = {k: str(v) for k, v in overrides.items()}

    def __call__(self, key, default=None):
        if key in self.overrides:
            return self.overrides[key]
        return SettingsService.DEFAULT_SETTINGS.get(key, (default,))[0]


def _macro(vix=20.0, fng=50, regime="Neutral", regime_score=50, **kw):
    return MacroDataSnapshot(vix=vix, fear_greed=fng,
                             market_regime=MarketRegimeSchema(status=regime, regime_score=regime_score), **kw)


def _state(ticker="005930", price=60000.0, rsi=50.0, dcf=0.0, change=0.0, low=None, volume=0, avg_vol=0,
           atr=0.0, conf=1.0, ema200=None, last_updated=None):
    st = TickerState(ticker=ticker, current_price=price, rsi=rsi, dcf_value=dcf, change_rate=change)
    st.low_price = low if low is not None else price
    st.volume = volume
    st.avg_volume_20d = avg_vol
    st.atr_pct = atr
    st.dcf_confidence = conf
    st.ema = {200: ema200 or price}
    st.last_updated = last_updated
    return st


class Base(unittest.TestCase):
    def setUp(self):
        self.stub = _SettingsStub()
        self._p = patch.object(SettingsService, "get_setting", side_effect=self.stub)
        self._p.start()
        CrashGuardService._status_cache = None
        # DB 의존 제거
        self._p2 = patch.object(PositionService, "_get_mode_for_market", return_value="top100"); self._p2.start()

    def tearDown(self):
        self._p.stop(); self._p2.stop()


# ── D1 점수 스케일/분리 ─────────────────────────────────────────────────────────
class TestScoreScaling(Base):
    def test_tanh_reduces_saturation_but_keeps_sign_and_cap(self):
        self.assertEqual(R.tanh_scaled(0, 25, 20), 0)
        self.assertLess(R.tanh_scaled(-25, 25, 20), 0)
        self.assertGreater(R.tanh_scaled(-25, 25, 20), -25)          # 25% 저평가는 더 이상 캡 포화가 아님
        self.assertLess(R.tanh_scaled(-40, 25, 20), R.tanh_scaled(-25, 25, 20))  # 40% > 25% 구별
        self.assertGreaterEqual(R.tanh_scaled(-500, 25, 20), -25)   # 캡 유지
        self.assertEqual(R.tanh_scaled(-30, 25, 0), -25)            # scale=0 → 선형 클램프

    def test_dcf_confidence_scales_delta(self):
        d_full, _ = SignalService._score_dcf(130.0, 100.0, confidence=1.0)   # +30% 저평가
        d_low, r = SignalService._score_dcf(130.0, 100.0, confidence=0.4)
        self.assertLess(d_full, 0)
        self.assertGreater(d_low, d_full)  # 덜 음수
        self.assertIn("conf0.4", r[0])
        self.assertAlmostEqual(R.dcf_confidence_for_source("kis"), 0.4)
        self.assertAlmostEqual(R.dcf_confidence_for_source(None), 1.0)

    def test_score_equals_stock_plus_market_adj(self):
        macro = _macro(vix=30.0, fng=25)  # 공포 → 음의 market_adj
        st = _state(rsi=40, dcf=70000.0)
        score, reasons, bd = SignalService.calculate_score("005930", st, None, macro, UserState(), 1_000_000, market_total_krw=10_000_000)
        self.assertIn("stock_score", bd); self.assertIn("market_adj", bd)
        self.assertLess(bd["market_adj"], 0)
        self.assertEqual(score, max(1, min(100, bd["stock_score"] + bd["market_adj"])))

    def test_market_adj_capped(self):
        self.stub.overrides["STRATEGY_MARKET_ADJ_CAP"] = "5"
        macro = _macro(vix=40.0, fng=5, regime="Bear", regime_score=20)
        _, reasons, bd = SignalService.calculate_score("005930", _state(), None, macro, UserState(), 1_000_000, market_total_krw=10_000_000)
        self.assertEqual(bd["market_adj"], -5)
        self.assertTrue(any(r.startswith("market_adj_capped") for r in reasons))

    def test_analyze_ticker_exposes_effective_thresholds(self):
        macro = _macro(vix=30.0)
        res = SignalService.analyze_ticker("005930", _state(), None, macro, UserState(), 1_000_000, 1350.0, market_total_krw=10_000_000)
        self.assertEqual(res["effective_buy_threshold"], res["buy_threshold"] - res["market_adj"])
        self.assertEqual(res["buy_threshold"], 30)


# ── D2 급락 확인 ────────────────────────────────────────────────────────────────
class TestDropConfirmation(Base):
    def test_unconfirmed_drop_is_dampened(self):
        # -6% 갭하락, 저가 근처(회복 X), 거래량 평균 이하
        f = R.drop_confirmation_factor(-6.0, 940.0, 938.0, 1000, 5000)
        self.assertAlmostEqual(f, 0.5)

    def test_rebound_or_volume_confirms(self):
        self.assertEqual(R.drop_confirmation_factor(-6.0, 960.0, 930.0, 1000, 5000), 1.0)   # 저가 대비 +3.2% ≥ 6%×0.3
        self.assertEqual(R.drop_confirmation_factor(-6.0, 940.0, 938.0, 9000, 5000), 1.0)   # 거래량 1.8×
        self.assertEqual(R.drop_confirmation_factor(-6.0, 940.0, 0, 0, 0), 1.0)             # 정보 없음 → 기존 동작

    def test_score_technical_marks_unconfirmed(self):
        st = _state(change=-6.0, price=940.0, low=938.0, volume=1000, avg_vol=5000)
        d, reasons = SignalService._score_technical(st, 940.0, 30, 70, -5)
        self.assertTrue(any(r.startswith("drop_unconfirmed") for r in reasons))
        self.assertTrue(any(r.startswith("change_deviation(-6.0%,-8)") or r.startswith("change_deviation(-6.0%,-7") for r in reasons))


# ── D3 Crash breadth / VIX spike ───────────────────────────────────────────────
class TestCrashDetection(Base):
    def test_breadth_triggers_crash(self):
        m = _macro(vix=18.0, kr_breadth_median=-3.5, kr_breadth_down_ratio=0.85, kr_breadth_count=60)
        is_crash, _, reasons = CrashGuardService.get_status(m)
        self.assertTrue(is_crash); self.assertTrue(any(r.startswith("kr_breadth") for r in reasons))

    def test_breadth_needs_min_count(self):
        m = _macro(vix=18.0, kr_breadth_median=-3.5, kr_breadth_down_ratio=0.85, kr_breadth_count=5)
        self.assertFalse(CrashGuardService.get_status(m)[0])

    def test_vix_spike_triggers_crash(self):
        m = _macro(vix=28.0, vix_change_1d=25.0)
        is_crash, _, reasons = CrashGuardService.get_status(m)
        self.assertTrue(is_crash); self.assertTrue(any(r.startswith("vix_spike") for r in reasons))

    def test_compute_breadth_from_states(self):
        saved = dict(MarketDataService._states)
        try:
            MarketDataService._states.clear()
            for i, chg in enumerate([-4.0, -3.0, -2.5, 1.0]):
                st = _state(ticker=f"00000{i}", change=chg); st.last_updated = datetime.now()
                MarketDataService._states[f"00000{i}"] = st
            med, down, n = MarketDataService.compute_breadth("KR")
            self.assertEqual(n, 4); self.assertAlmostEqual(down, 0.75); self.assertAlmostEqual(med, -2.75)
            self.assertEqual(MarketDataService.compute_breadth("US"), (None, None, 0))
        finally:
            MarketDataService._states.clear(); MarketDataService._states.update(saved)


# ── D4 신선도 게이트 ────────────────────────────────────────────────────────────
class TestStaleGate(Base):
    def test_stale_price_detected(self):
        self.assertTrue(SignalService._is_price_stale(_state(last_updated=datetime.now() - timedelta(seconds=1000))))
        self.assertFalse(SignalService._is_price_stale(_state(last_updated=datetime.now() - timedelta(seconds=30))))
        self.assertFalse(SignalService._is_price_stale(_state(last_updated=None)))
        self.stub.overrides["STRATEGY_PRICE_STALE_SEC"] = "0"
        self.assertFalse(SignalService._is_price_stale(_state(last_updated=datetime.now() - timedelta(days=1))))


# ── D5 레짐 ─────────────────────────────────────────────────────────────────────
class TestRegime(Base):
    def test_weighted_score_uses_component_weights(self):
        # 모두 20 → 100, 모두 10 → 50
        self.assertEqual(MacroService._compute_weighted_score(20, 20, 20, 20, 20, 0), 100)
        self.assertEqual(MacroService._compute_weighted_score(10, 10, 10, 10, 10, 0), 50)
        # technical 만 20, 나머지 10 → 50 + 10×25/20 = 62.5 → 62/63 (반올림)
        self.assertIn(MacroService._compute_weighted_score(20, 10, 10, 10, 10, 0), (62, 63))
        self.assertEqual(sum(MacroService.COMPONENT_WEIGHTS.values()), 100)

    def test_bull_threshold_dynamic(self):
        with patch("services.market.stock_meta_service.StockMetaService.get_market_regime_history",
                   return_value=[{"status": "Bear"}, {"status": "Bear"}]):
            self.assertEqual(MacroService._get_bull_threshold(), 70)
        with patch("services.market.stock_meta_service.StockMetaService.get_market_regime_history",
                   return_value=[{"status": "Bear"}, {"status": "Neutral"}]):
            self.assertEqual(MacroService._get_bull_threshold(), 67)
        with patch("services.market.stock_meta_service.StockMetaService.get_market_regime_history", return_value=[]):
            self.assertEqual(MacroService._get_bull_threshold(), 65)


# ── B2 사이징 ───────────────────────────────────────────────────────────────────
class TestSizing(Base):
    def test_confidence_multiplier_buy_direction(self):
        self.assertEqual(R.buy_confidence_multiplier(5), 1.5)
        self.assertEqual(R.buy_confidence_multiplier(15), 1.25)
        self.assertEqual(R.buy_confidence_multiplier(28), 1.0)
        self.assertEqual(R.buy_confidence_multiplier(95), 1.0)  # 옛 승수(≥90→2×) 제거

    def test_volatility_multiplier(self):
        self.assertEqual(R.volatility_multiplier(0), 1.0)
        self.assertAlmostEqual(R.volatility_multiplier(4.0), 0.5)
        self.assertAlmostEqual(R.volatility_multiplier(1.0), 1.5)
        self.assertAlmostEqual(R.volatility_multiplier(2.5), 0.8)

    def test_calculate_buy_quantity_applies_multipliers(self):
        # 100M × 5% = 5M ; score 5 → ×1.5 ; atr 4% → ×0.5 → 3.75M / 60,000 = 62주
        qty, cost, px = TradeExecutorService._calculate_buy_quantity(5, 50_000_000, 60_000, 1350.0, True, market_total_krw=100_000_000, atr_pct=4.0)
        self.assertEqual(qty, 62)

    def test_atr_pct_from_ohlc(self):
        n = 40
        idx = pd.date_range("2026-01-01", periods=n)
        close = pd.Series([100.0 + i for i in range(n)], index=idx)
        df = pd.DataFrame({"Close": close, "High": close + 1.0, "Low": close - 1.0, "Volume": [1000] * n})
        atr = IndicatorService.compute_atr_pct(df)
        self.assertIsNotNone(atr); self.assertGreater(atr, 0); self.assertLess(atr, 5)
        self.assertEqual(IndicatorService.compute_avg_volume(df), 1000.0)
        self.assertIsNone(IndicatorService.compute_atr_pct(df.head(5)))


# ── B5 그룹 한도 ────────────────────────────────────────────────────────────────
class TestGroupCap(Base):
    def test_group_cap_reduces_or_blocks(self):
        holdings = [HoldingSchema(ticker="009540", quantity=10, buy_price=100, current_price=100),
                    HoldingSchema(ticker="010140", quantity=10, buy_price=100, current_price=100)]
        gmap = {"009540": "조선", "010140": "조선", "042660": "조선", "005930": "반도체"}
        with patch.object(TradeExecutorService, "_group_key_map", return_value=gmap):
            # 그룹 현재 2,000 / 총 10,000 = 20% ; 한도 30% → 1,000 여유 → 100 가격 → 10주까지
            q = TradeExecutorService._group_exposure_cap_qty("042660", holdings, 100.0, 10_000.0, 1350.0, 50)
            self.assertEqual(q, 10)
            # 다른 그룹(보유 0)은 그룹 예산 30% = 3,000 → 30주 상한
            self.assertEqual(TradeExecutorService._group_exposure_cap_qty("005930", holdings, 100.0, 10_000.0, 1350.0, 50), 30)
            self.assertEqual(TradeExecutorService._group_exposure_cap_qty("005930", holdings, 100.0, 10_000.0, 1350.0, 20), 20)
            # 이미 초과 → 0
            self.assertEqual(TradeExecutorService._group_exposure_cap_qty("042660", holdings, 100.0, 5_000.0, 1350.0, 50), 0)


# ── B4 예비현금 동적화 / DCA ────────────────────────────────────────────────────
class TestDcaRules(Base):
    def _dca(self, holding, price, user_state, macro=None, cash=50_000_000):
        calls = []
        def fake(*a, **k):
            calls.append(k); return TradeResult(executed=True, spent_krw=1.0)
        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake):
            r = PositionService._handle_uptrend_dca_signal(
                holding.ticker, holding, (price - holding.buy_price) / holding.buy_price * 100, price,
                100_000_000, cash, 1350.0, [holding], "sean", macro or _macro(), 0.0, 0.0, user_state)
        return r, calls

    def test_reserve_zero_in_deep_crash_tier(self):
        self.assertAlmostEqual(TradeExecutorService._uptrend_target_cash_ratio(_macro(kospi_change_5d=-1.0), "005930"), 0.10)
        self.assertAlmostEqual(TradeExecutorService._uptrend_target_cash_ratio(_macro(kospi_change_5d=-13.0), "005930"), 0.0)
        self.assertAlmostEqual(TradeExecutorService._uptrend_target_cash_ratio(_macro(kospi_change_5d=-13.0, spx_change_5d=-1.0), "AAPL"), 0.10)

    def test_one_stage_per_day(self):
        us = UserState()
        h = HoldingSchema(ticker="005930", quantity=10, buy_price=100_000, current_price=83_000)  # -17%
        r1, c1 = self._dca(h, 83_000, us)
        self.assertTrue(r1.executed); self.assertEqual(c1[0]["trigger_reason"], "dca_stage_-15")
        r2, c2 = self._dca(h, 83_000, us)   # 같은 날 2단계 → 보류
        self.assertFalse(r2.executed); self.assertEqual(c2, [])
        self.stub.overrides["STRATEGY_UPTREND_DCA_MAX_STAGES_PER_DAY"] = "0"
        r3, c3 = self._dca(h, 83_000, us)
        self.assertTrue(r3.executed); self.assertEqual(c3[0]["trigger_reason"], "dca_stage_-8")

    def test_entry_reference_mode(self):
        self.stub.overrides["STRATEGY_UPTREND_DCA_REF"] = "entry"
        us = UserState()
        h = HoldingSchema(ticker="005930", quantity=10, buy_price=100_000, current_price=96_500)
        r, c = self._dca(h, 96_500, us)
        self.assertTrue(r.executed); self.assertEqual(us.dca_done["005930"]["ref_price"], 100_000)
        # 추매 후 평균단가가 내려가도(97,000) 기준가는 100,000 유지 → -8% 는 92,000 에서
        h2 = HoldingSchema(ticker="005930", quantity=20, buy_price=97_000, current_price=92_000)
        self.stub.overrides["STRATEGY_UPTREND_DCA_MAX_STAGES_PER_DAY"] = "0"
        r2, c2 = self._dca(h2, 92_000, us)
        self.assertTrue(r2.executed); self.assertEqual(c2[0]["trigger_reason"], "dca_stage_-8")


# ── B3 트랜치 페이싱 ────────────────────────────────────────────────────────────
class TestTranchePacing(Base):
    def test_second_tranche_waits_same_day_unless_drop(self):
        split_orders = {"005930": SplitOrderState(total_qty=9, remaining_qty=6, splits_done=1, split_count=3,
                                                   start_date="2026-09-09", entry_price=60_000,
                                                   last_tranche_price=60_000, last_tranche_date="2026-09-09")}
        with patch.object(PositionService, "_execute_split_tranche", return_value=TradeResult(executed=True)) as ex:
            r = PositionService._handle_buy_split("005930", None, 20, "r", 0.0, 30, {}, "2026-09-09", _state(price=59_500),
                                                  100_000_000, 50_000_000, 1350.0, [], "sean", _macro(), 0.1, 0.1, split_orders)
            self.assertFalse(r.executed); ex.assert_not_called()
            r = PositionService._handle_buy_split("005930", None, 20, "r", 0.0, 30, {}, "2026-09-09", _state(price=58_500),
                                                  100_000_000, 50_000_000, 1350.0, [], "sean", _macro(), 0.1, 0.1, split_orders)
            self.assertTrue(r.executed)  # -2.5% 하락 → 진행
            r = PositionService._handle_buy_split("005930", None, 20, "r", 0.0, 30, {}, "2026-09-10", _state(price=61_000),
                                                  100_000_000, 50_000_000, 1350.0, [], "sean", _macro(), 0.1, 0.1, split_orders)
            self.assertTrue(r.executed)  # 다음 거래일 → 진행


# ── B6 재진입 ───────────────────────────────────────────────────────────────────
class TestReentry(Base):
    def test_sell_block_expires_by_days_or_drop(self):
        cd = {"005930": BuyCooldownEntry(date="2026-09-01", price=100_000, kind="sell", expire_days=5)}
        self.assertTrue(PositionService._is_buy_cooldown_active("005930", "2026-09-03", 99_000, cd))
        self.assertFalse(PositionService._is_buy_cooldown_active("005930", "2026-09-06", 99_000, cd))   # 5일 경과
        self.assertFalse(PositionService._is_buy_cooldown_active("005930", "2026-09-03", 94_000, cd))   # -6% 하락
        PositionService._cleanup_expired_cooldowns({}, cd, "2026-09-03"); self.assertIn("005930", cd)
        PositionService._cleanup_expired_cooldowns({}, cd, "2026-09-06"); self.assertNotIn("005930", cd)

    def test_legacy_buy_entry_unchanged(self):
        cd = {"005930": BuyCooldownEntry(date="2026-09-01", price=100_000)}
        self.assertTrue(PositionService._is_buy_cooldown_active("005930", "2026-09-01", 100_000, cd))
        self.assertFalse(PositionService._is_buy_cooldown_active("005930", "2026-09-02", 100_000, cd))

    def test_panic_lock_requires_recovery(self):
        us = UserState()
        PositionService._set_panic_lock("005930", us, price=50_000)
        self.assertEqual(us.panic_locks["005930"]["price"], 50_000)
        macro = _macro()
        s1, r1, _ = SignalService.calculate_score("005930", _state(price=50_500, rsi=25), None, macro, us, 1e6, market_total_krw=1e7)
        self.assertEqual(s1, 50); self.assertTrue(r1[0].startswith("panic_lock_no_recovery"))
        s2, r2, _ = SignalService.calculate_score("005930", _state(price=51_600, rsi=25), None, macro, us, 1e6, market_total_krw=1e7)
        self.assertEqual(s2, 20)
        PositionService._clear_expired_panic_locks(us, expire_days=0)
        self.assertNotIn("005930", us.panic_locks)
        us.panic_locks["legacy"] = "2020-01-01"; PositionService._clear_expired_panic_locks(us); self.assertNotIn("legacy", us.panic_locks)


# ── S1/S2 스케일아웃·trailing ───────────────────────────────────────────────────
class TestScaleOut(Base):
    def test_parse_and_bands(self):
        self.assertEqual(R.parse_scale_out("10:0.5,20:0.5,35:0.5"), [(10.0, 0.5), (20.0, 0.5), (35.0, 0.5)])
        self.assertEqual(R.parse_scale_out("bad"), [(10.0, 0.5)])
        self.assertEqual(R.trailing_pct_by_profit(12.0), -10.0)
        self.assertEqual(R.trailing_pct_by_profit(25.0), -7.0)
        self.assertEqual(R.trailing_pct_by_profit(40.0), -5.0)
        self.assertEqual(R.stages_done_count(True), 1); self.assertEqual(R.stages_done_count(2), 2); self.assertEqual(R.stages_done_count(None), 0)

    def _pt(self, us, profit, qty=8, price=None):
        h = HoldingSchema(ticker="005930", quantity=qty, buy_price=100.0, current_price=price or 100 * (1 + profit / 100))
        calls = []
        def fake(*a, **k):
            calls.append(k); return TradeResult(executed=True)
        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake):
            ok = PositionService._handle_uptrend_partial_take("005930", h, profit, _state(price=h.current_price), 40,
                                                              1e7, 1e6, 1350.0, [h], "sean", _macro(), 0.1, 0.1, us)
        return ok, calls

    def test_three_stage_scale_out(self):
        us = UserState()
        ok, c = self._pt(us, 11.0); self.assertTrue(ok); self.assertEqual(c[0]["forced_qty"], 4); self.assertEqual(us.partial_take_done["005930"], 1)
        ok, c = self._pt(us, 15.0); self.assertFalse(ok)                          # 2단계(20%) 미달
        ok, c = self._pt(us, 21.0, qty=4); self.assertTrue(ok); self.assertEqual(c[0]["forced_qty"], 2); self.assertEqual(us.partial_take_done["005930"], 2)
        ok, c = self._pt(us, 36.0, qty=2); self.assertTrue(ok); self.assertEqual(c[0]["forced_qty"], 1); self.assertEqual(us.partial_take_done["005930"], 3)
        ok, c = self._pt(us, 50.0, qty=1); self.assertFalse(ok)                   # 단계 소진
        self.assertEqual(us.add_buy_cooldown["005930"].kind, "sell")              # 재진입 차단 설정

    def test_remaining_trailing_floor_and_band(self):
        us = UserState(); us.partial_take_done["005930"] = 1; us.remaining_high["005930"] = 112.0
        h = HoldingSchema(ticker="005930", quantity=4, buy_price=100.0, current_price=100.5)
        with patch.object(PositionService, "_handle_forced_sell", return_value=TradeResult(executed=True)) as fs:
            # 고점 112 → -10% = 100.8 ; 현재 100.5 는 trailing 조건이지만 손익분기 하한(101) 아래 → 보류
            ok = PositionService._handle_uptrend_remaining_trailing("005930", h, 100.5, 1e7, 1e6, 1350.0, [h], "sean", _macro(), 0.1, 0.1, us)
            self.assertFalse(ok); fs.assert_not_called()
            # 고점 130(+30%) → band -7% → 120.9 ; 현재 120 → 매도
            us.remaining_high["005930"] = 130.0
            ok = PositionService._handle_uptrend_remaining_trailing("005930", h, 120.0, 1e7, 1e6, 1350.0, [h], "sean", _macro(), 0.1, 0.1, us)
            self.assertTrue(ok); fs.assert_called_once()
            self.assertIn("trail -7%", fs.call_args.kwargs["reason"])


# ── S3/S4/S5 ─────────────────────────────────────────────────────────────────────
class TestSellRules(Base):
    def test_atr_stop_loss(self):
        self.assertAlmostEqual(R.uptrend_stop_loss_pct(None), -25.0)
        self.assertAlmostEqual(R.uptrend_stop_loss_pct(3.0), -18.0)   # -6×3
        self.assertAlmostEqual(R.uptrend_stop_loss_pct(1.0), -15.0)   # cap
        self.assertAlmostEqual(R.uptrend_stop_loss_pct(9.0), -35.0)   # floor
        h = HoldingSchema(ticker="005930", quantity=1, buy_price=100.0, current_price=80.0)
        _, r, forced = SignalService._score_portfolio(h, -20.0, 7.0, -7.0, atr_pct=3.0)
        self.assertTrue(forced)
        _, r, forced = SignalService._score_portfolio(h, -20.0, 7.0, -7.0, atr_pct=None)
        self.assertFalse(forced)

    def test_score_sell_requires_min_profit_in_uptrend(self):
        h = HoldingSchema(ticker="005930", quantity=10, buy_price=100.0, current_price=101.0)
        sig = SignalSchema(ticker="005930", state=_state(price=101.0), holding=h, score=80, reasons=["x"])
        cfg = ExecutionConfig(buy_max=30, sell_min=70, take_profit_pct=7, stop_loss_pct=-7, add_rsi_limit=60, add_score_limit=55, exchange_rate=1350.0, today="2026-09-09")
        us = UserState()
        with patch.object(PositionService, "_handle_score_trade") as hst, \
             patch("repositories.trade_history_repo.TradeHistoryRepo.get_first_buy_date", return_value=None):
            ex, *_ = PositionService._process_single_signal(sig, cfg, {}, {}, [h], "sean", 1e7, 0, 1e6, _macro(), 0.1, 0.1,
                                                            split_orders={}, sell_split_orders={}, trailing_high={}, user_state=us)
            self.assertFalse(ex); hst.assert_not_called()
            h2 = HoldingSchema(ticker="005930", quantity=10, buy_price=100.0, current_price=105.0)
            sig2 = SignalSchema(ticker="005930", state=_state(price=105.0), holding=h2, score=80, reasons=["x"])
            hst.return_value = TradeResult(executed=True)
            ex, *_ = PositionService._process_single_signal(sig2, cfg, {}, {}, [h2], "sean", 1e7, 0, 1e6, _macro(), 0.1, 0.1,
                                                            split_orders={}, sell_split_orders={}, trailing_high={}, user_state=us)
            self.assertTrue(ex); hst.assert_called_once()
            self.assertEqual(us.add_buy_cooldown["005930"].kind, "sell")

    def test_relative_weakness(self):
        h = HoldingSchema(ticker="005930", quantity=10, buy_price=100.0, current_price=78.0)
        macro = _macro(kospi_change_90d=5.0, kospi_change_20d=2.0)
        us = UserState()
        calls = []
        def fake(*a, **k):
            calls.append(k); return TradeResult(executed=True)
        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake), \
             patch("repositories.trade_history_repo.TradeHistoryRepo.get_first_buy_date", return_value=datetime.now() - timedelta(days=120)):
            r = PositionService._handle_relative_weakness("005930", h, -22.0, 78.0, 1e7, 1e6, 1350.0, [h], "sean", macro, 0.1, 0.1, us, "2026-09-09")
            self.assertTrue(r.executed); self.assertEqual(calls[0]["forced_qty"], 5); self.assertEqual(calls[0]["trigger_reason"], "relative_weakness")
            r = PositionService._handle_relative_weakness("005930", h, -22.0, 78.0, 1e7, 1e6, 1350.0, [h], "sean", macro, 0.1, 0.1, us, "2026-09-09")
            self.assertFalse(r.executed)  # 1회만
        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake), \
             patch("repositories.trade_history_repo.TradeHistoryRepo.get_first_buy_date", return_value=datetime.now() - timedelta(days=30)):
            r = PositionService._handle_relative_weakness("005930", h, -22.0, 78.0, 1e7, 1e6, 1350.0, [h], "sean", macro, 0.1, 0.1, UserState(), "2026-09-09")
            self.assertFalse(r.executed)  # 보유기간 미달
        idx_down = _macro(kospi_change_90d=-25.0, kospi_change_20d=2.0)
        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake), \
             patch("repositories.trade_history_repo.TradeHistoryRepo.get_first_buy_date", return_value=datetime.now() - timedelta(days=120)):
            r = PositionService._handle_relative_weakness("005930", h, -22.0, 78.0, 1e7, 1e6, 1350.0, [h], "sean", idx_down, 0.1, 0.1, UserState(), "2026-09-09")
            self.assertFalse(r.executed)  # 지수도 -25% → 상대약세 아님


# ── B1 루프당 신규 매수 상한 ───────────────────────────────────────────────────
class TestNewBuyCap(Base):
    def test_only_top_n_new_candidates_processed(self):
        sigs = [SignalSchema(ticker=f"00000{i}", state=_state(ticker=f"00000{i}"), holding=None, score=s, reasons=[])
                for i, s in enumerate([25, 10, 30, 5])]
        processed = []
        def fake_process(sig, *a, **k):
            processed.append(sig.ticker); return True, sig.ticker, 0.0, 0.0
        cfg = ExecutionConfig(buy_max=30, sell_min=70, take_profit_pct=7, stop_loss_pct=-7, add_rsi_limit=60, add_score_limit=55, exchange_rate=1350.0, today="2026-09-09")
        with patch.object(PositionService, "_process_single_signal", side_effect=fake_process), \
             patch.object(PositionService, "_load_execution_config", return_value=cfg), \
             patch.object(PositionService, "_check_unmonitored_holdings", return_value=(False, set())):
            PositionService._execute_collected_signals("sean", sigs, [], 1e7, 0, 1e6, 0.1, 0.1, _macro(), UserState())
        self.assertEqual(processed, ["000003", "000001"])  # 점수 5, 10 만 (상한 2, 점수순)


# ── 인프라: 섀도우 / 마감 병합 ─────────────────────────────────────────────────
class TestInfra(Base):
    def test_shadow_mode_records_without_order(self):
        self.stub.overrides["STRATEGY_SHADOW"] = "1"
        with patch.object(TradeExecutorService, "_has_pending_order", return_value=False), \
             patch("services.trading.order_service.OrderService.record_trade") as rec, \
             patch("services.kis.kis_service.KisService.send_order") as send:
            ok = TradeExecutorService._place_and_record("005930", "buy", 3, 60_000, "r", "sean", trigger_reason="score_buy")
            self.assertTrue(ok); send.assert_not_called()
            self.assertEqual(rec.call_args.kwargs["status"], "shadow")

    def test_sell_split_merges_near_close(self):
        with patch("services.market.market_hour_service.MarketHourService.minutes_to_close", return_value=5):
            self.assertEqual(PositionService._get_sell_split_qty("005930", 10, {}, "2026-09-09"), 10)
        with patch("services.market.market_hour_service.MarketHourService.minutes_to_close", return_value=120):
            self.assertEqual(PositionService._get_sell_split_qty("005930", 10, {}, "2026-09-09"), 2)

    def test_minutes_to_close_outside_session(self):
        from services.market.market_hour_service import MarketHourService
        v = MarketHourService.minutes_to_close("KR")
        self.assertTrue(v == 1e9 or 0 <= v <= 390)


if __name__ == "__main__":
    unittest.main()
