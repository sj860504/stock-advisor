"""Uptrend DCA 알고리즘 회귀 테스트 (DB 없이 SettingsService 를 DEFAULT_SETTINGS 로 패치).

검증 항목:
  1. US 종목 DCA 예산/수량이 KRW 기준으로 일관 계산됨 (통화 혼용 버그)
  2. DCA 추매는 레거시 추매 엔트리(-5%) / 목표현금 게이트를 우회 (stage1 -3% 실행 가능)
  3. Uptrend 안전망 손절: -25% 이하 → forced_sell, 그 위는 추매 가산
  4. 손절 연속일수: Uptrend 모드는 UPTREND_STOP_LOSS_DAYS(7) 적용
  5. Frozen(VIX>50) 이면 모든 매수 경로 차단
  6. 지수 5d tier: KR→KOSPI, US→SPX
  7. 현금 부족은 DCA 단계를 소진 처리하지 않음 (재시도 가능)
  8. Uptrend 목표현금 = max(MIN_CASH, DCA_RESERVE)
  9. CrashGuard 상태 캐시는 입력값 변화 시 즉시 무효화
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.schemas import HoldingSchema, MacroDataSnapshot, MarketRegimeSchema, TradeResult, UserState
from services.config.settings_service import SettingsService
from services.strategy.asset_management_service import AssetManagementService
from services.strategy.crash_guard_service import CrashGuardService
from services.strategy.execution_service_v2 import TradeExecutorService
from services.strategy.position_service import PositionService
from services.strategy.signal_service import SignalService


class _SettingsStub:
    """SettingsService.get_setting 을 DB 없이 DEFAULT_SETTINGS + overrides 로 대체."""

    def __init__(self, **overrides):
        self.overrides = {k: str(v) for k, v in overrides.items()}

    def __call__(self, key, default=None):
        if key in self.overrides:
            return self.overrides[key]
        return SettingsService.DEFAULT_SETTINGS.get(key, (default,))[0]


def _macro(vix=20.0, k5d=None, s5d=None, k1d=None, s1d=None, regime="Neutral", regime_score=50):
    return MacroDataSnapshot(
        vix=vix, fear_greed=50,
        market_regime=MarketRegimeSchema(status=regime, regime_score=regime_score),
        kospi_change_1d=k1d, kospi_change_5d=k5d, spx_change_1d=s1d, spx_change_5d=s5d,
    )


class UptrendDcaBase(unittest.TestCase):
    def setUp(self):
        self.stub = _SettingsStub()
        self._p = patch.object(SettingsService, "get_setting", side_effect=self.stub)
        self._p.start()
        CrashGuardService._status_cache = None

    def tearDown(self):
        self._p.stop()


class TestDcaCurrency(UptrendDcaBase):
    def _run_dca(self, ticker, price, buy_price, market_total, cash_krw, usd_cash, fx=1350.0, macro=None):
        holding = HoldingSchema(ticker=ticker, quantity=10, buy_price=buy_price, current_price=price)
        user_state = UserState()
        calls = []

        def fake_trade(*args, **kwargs):
            calls.append((args, kwargs))
            return TradeResult(executed=True, spent_usd=1.0)

        with patch.object(TradeExecutorService, "_execute_trade_v2", side_effect=fake_trade):
            result = PositionService._handle_uptrend_dca_signal(
                ticker, holding, (price - buy_price) / buy_price * 100, price,
                market_total, cash_krw, fx, [holding], "sean", macro or _macro(),
                0.0, 0.0, user_state, usd_cash=usd_cash,
            )
        return result, calls, user_state

    def test_us_ticker_qty_is_computed_in_krw_terms(self):
        # AAPL -10% → stage2 (-8%, 4%). us_total_krw=100M → budget 4M KRW; price 180$×1350=243,000 → 16주
        result, calls, us = self._run_dca("AAPL", price=180.0, buy_price=200.0,
                                          market_total=100_000_000, cash_krw=50_000_000, usd_cash=20_000)
        self.assertTrue(result.executed)
        self.assertEqual(len(calls), 1)
        kwargs = calls[0][1]
        self.assertEqual(kwargs["forced_qty"], 16)
        self.assertEqual(kwargs["trigger_reason"], "dca_stage_-8")
        self.assertTrue(us.dca_done["AAPL"]["-8"])

    def test_us_ticker_ignores_krw_cash_for_min_cash_guard(self):
        # USD 현금 1,000$ (=1.35M KRW) 만 있으면 예산은 그 한도로 축소 → 5주 (1.35M / 243k)
        result, calls, _ = self._run_dca("AAPL", price=180.0, buy_price=200.0,
                                         market_total=100_000_000, cash_krw=50_000_000, usd_cash=1_000)
        self.assertTrue(result.executed)
        self.assertEqual(calls[0][1]["forced_qty"], 5)

    def test_kr_ticker_uses_krw_cash(self):
        # 005930 -4% → stage1 (-3%, 3%). kr_total 100M → 3M KRW / 60,000 = 50주
        result, calls, _ = self._run_dca("005930", price=60_000, buy_price=62_500,
                                         market_total=100_000_000, cash_krw=50_000_000, usd_cash=0)
        self.assertTrue(result.executed)
        self.assertEqual(calls[0][1]["forced_qty"], 50)
        self.assertEqual(calls[0][1]["trigger_reason"], "dca_stage_-3")

    def test_insufficient_cash_does_not_consume_stage(self):
        result, calls, us = self._run_dca("AAPL", price=180.0, buy_price=200.0,
                                          market_total=100_000_000, cash_krw=50_000_000, usd_cash=0)
        self.assertFalse(result.executed)
        self.assertEqual(calls, [])
        self.assertNotIn("AAPL", us.dca_done)  # 재시도 가능해야 함

    def test_max_position_marks_stage_done(self):
        # 보유 평가액이 총자산 15% 이상 → 단계 소진 처리
        holding_val_krw = 10 * 180 * 1350  # 2.43M
        result, calls, us = self._run_dca("AAPL", price=180.0, buy_price=200.0,
                                          market_total=holding_val_krw / 0.2, cash_krw=0, usd_cash=50_000)
        self.assertFalse(result.executed)
        self.assertTrue(us.dca_done["AAPL"]["-8"])

    def test_us_ticker_uses_spx_multiplier(self):
        # SPX 5d -13% → tier3 → mult 2.0 → 4%×2 = 8% → 8M KRW / 243k = 32주 ; KOSPI 는 정상
        macro = _macro(k5d=-1.0, s5d=-13.0)
        result, calls, _ = self._run_dca("AAPL", price=180.0, buy_price=200.0,
                                         market_total=100_000_000, cash_krw=0, usd_cash=100_000, macro=macro)
        self.assertEqual(calls[0][1]["forced_qty"], 32)


class TestEntryGateBypass(UptrendDcaBase):
    def test_dca_trigger_bypasses_add_buy_entry_and_cash_target(self):
        with patch.object(TradeExecutorService, "_has_absolute_cash", return_value=True), \
             patch.object(TradeExecutorService, "_is_cash_below_target", return_value=True):
            ok = TradeExecutorService._check_buy_cash_and_entry_conditions(
                "005930", 1_000_000, True, -3.2, [], 1350.0, 0.1, 0.1, _macro(),
                trigger_reason="dca_stage_-3",
            )
            self.assertTrue(ok)
            # 동일 조건에서 일반 추매는 -5% 엔트리 게이트에 막힘
            blocked = TradeExecutorService._check_buy_cash_and_entry_conditions(
                "005930", 1_000_000, True, -3.2, [], 1350.0, 0.1, 0.1, _macro(),
                trigger_reason="add_position",
            )
            self.assertFalse(blocked)

    def test_dca_trigger_still_requires_absolute_cash(self):
        with patch.object(TradeExecutorService, "_has_absolute_cash", return_value=False):
            ok = TradeExecutorService._check_buy_cash_and_entry_conditions(
                "005930", 0, True, -3.2, [], 1350.0, 0.1, 0.1, _macro(), trigger_reason="dca_stage_-3",
            )
            self.assertFalse(ok)


class TestUptrendStopLoss(UptrendDcaBase):
    def test_score_portfolio_deep_loss_is_buy_but_extreme_loss_is_forced_sell(self):
        holding = HoldingSchema(ticker="005930", quantity=1, buy_price=100.0, current_price=88.0)
        delta, reasons, forced = SignalService._score_portfolio(holding, -12.0, 7.0, -7.0)
        self.assertFalse(forced)
        self.assertEqual(delta, TradeExecutorService.WEIGHTS["ADD_POSITION_LOSS"])
        self.assertTrue(reasons[0].startswith("uptrend_deep_loss"))

        delta, reasons, forced = SignalService._score_portfolio(holding, -26.0, 7.0, -7.0)
        self.assertTrue(forced)
        self.assertTrue(reasons[0].startswith("uptrend_stop_loss_hit"))

    def test_legacy_mode_still_forces_sell_at_legacy_threshold(self):
        self.stub.overrides["STRATEGY_UPTREND_DCA_ENABLED"] = "0"
        holding = HoldingSchema(ticker="005930", quantity=1, buy_price=100.0, current_price=90.0)
        _, reasons, forced = SignalService._score_portfolio(holding, -10.0, 7.0, -7.0)
        self.assertTrue(forced)
        self.assertEqual(reasons, ["stop_loss_hit"])

    def test_unpack_signal_detects_uptrend_forced_sell(self):
        from models.schemas import SignalSchema
        holding = HoldingSchema(ticker="005930", quantity=1, buy_price=100.0, current_price=70.0)
        sig = SignalSchema(ticker="005930", state=None, holding=holding, score=100,
                           reasons=["uptrend_stop_loss_hit(-30.0%<=-25%)"])
        u = PositionService._unpack_signal(sig, 1_000_000, 0)
        self.assertTrue(u.forced_sell)

    def test_consecutive_days_uses_uptrend_setting(self):
        us = UserState()
        days = [f"2026-09-{d:02d}" for d in range(1, 8)]  # 7 거래일
        fired = [PositionService._should_execute_stop_loss("005930", -26.0, _macro(), us, d) for d in days]
        self.assertEqual(fired, [False] * 6 + [True])
        self.assertNotIn("005930", us.stop_loss_streak)  # 실행 직전 리셋

    def test_same_day_recall_does_not_double_count(self):
        us = UserState()
        PositionService._should_execute_stop_loss("005930", -26.0, _macro(), us, "2026-09-01")
        PositionService._should_execute_stop_loss("005930", -26.0, _macro(), us, "2026-09-01")
        self.assertEqual(us.stop_loss_streak["005930"]["days"], 1)


class TestFrozenGuard(UptrendDcaBase):
    def test_frozen_blocks_every_buy_path(self):
        from services.market.market_hour_service import MarketHourService
        with patch.object(MarketHourService, "is_weekend", return_value=False), \
             patch.object(MarketHourService, "is_trading_active", return_value=True), \
             patch.object(TradeExecutorService, "_check_buy_cash_and_entry_conditions",
                          side_effect=AssertionError("must not reach entry checks when frozen")):
            for trig in ("dca_stage_-3", "score_buy", "budget_buy", None):
                result = TradeExecutorService._execute_buy_order(
                    "005930", 20, 0.0, False, 60_000, 100_000_000, 50_000_000, 1350.0,
                    [], "sean", None, _macro(vix=55.0), 0.1, 0.1, forced_qty=1, trigger_reason=trig,
                )
                self.assertFalse(result.executed)

    def test_not_frozen_proceeds_to_entry_checks(self):
        from services.market.market_hour_service import MarketHourService
        with patch.object(MarketHourService, "is_weekend", return_value=False), \
             patch.object(MarketHourService, "is_trading_active", return_value=True), \
             patch.object(TradeExecutorService, "_check_buy_cash_and_entry_conditions", return_value=False) as chk:
            TradeExecutorService._execute_buy_order(
                "005930", 20, 0.0, False, 60_000, 100_000_000, 50_000_000, 1350.0,
                [], "sean", None, _macro(vix=36.0), 0.1, 0.1, forced_qty=1,
            )
            chk.assert_called_once()


class TestIndexTier(UptrendDcaBase):
    def test_kr_uses_kospi_and_us_uses_spx(self):
        macro = _macro(k5d=-1.0, s5d=-13.0)
        self.assertEqual(CrashGuardService.index_5d_tier(macro, "005930"), 1)
        self.assertEqual(CrashGuardService.index_5d_tier(macro, "AAPL"), 3)
        self.assertEqual(CrashGuardService.kospi_5d_tier(macro), 1)  # UI 배지 하위호환
        self.assertEqual(CrashGuardService.score_boost(macro, "AAPL"), -10)
        self.assertEqual(CrashGuardService.per_trade_mult(macro, "AAPL"), 2.0)
        self.assertEqual(CrashGuardService.per_trade_mult(macro, "005930"), 1.0)

    def test_us_falls_back_to_kospi_when_spx_missing(self):
        macro = _macro(k5d=-8.0, s5d=None)
        self.assertEqual(CrashGuardService.index_5d_tier(macro, "AAPL"), 2)

    def test_market_context_reason_label(self):
        macro = _macro(k5d=-1.0, s5d=-13.0)
        _, reasons_us = SignalService._score_market_context(macro, "NEUTRAL", "AAPL")
        _, reasons_kr = SignalService._score_market_context(macro, "NEUTRAL", "005930")
        self.assertTrue(any(r.startswith("SPX_5d_boost(t3") for r in reasons_us))
        self.assertFalse(any("5d_boost" in r for r in reasons_kr))


class TestReserveCash(UptrendDcaBase):
    def test_target_cash_ratio_is_reserve_in_uptrend(self):
        self.assertAlmostEqual(TradeExecutorService._get_target_cash_ratio("KR", "NEUTRAL"), 0.10)
        self.assertAlmostEqual(TradeExecutorService._get_target_cash_ratio("US", "BULL"), 0.10)
        self.assertAlmostEqual(
            AssetManagementService._get_target_cash_ratio(MarketRegimeSchema(status="Bear"), 50, []), 0.10)

    def test_reserve_zero_restores_full_investment(self):
        self.stub.overrides["STRATEGY_UPTREND_DCA_RESERVE_RATIO"] = "0"
        self.assertEqual(TradeExecutorService._get_target_cash_ratio("KR", "NEUTRAL"), 0.0)

    def test_min_cash_wins_when_higher(self):
        self.stub.overrides["STRATEGY_UPTREND_MIN_CASH_RATIO"] = "0.2"
        self.assertAlmostEqual(TradeExecutorService._get_target_cash_ratio("KR", "NEUTRAL"), 0.2)

    def test_asset_mgmt_does_not_sell_to_refill_reserve_in_uptrend(self):
        # 현금 5% < 예비 10% → 레거시라면 수익 종목 매도. Uptrend 모드에선 매도 스킵.
        holding = HoldingSchema(ticker="005930", quantity=100, buy_price=50_000, current_price=60_000)
        with patch.object(PositionService, "execute_sell_for_cash") as sell, \
             patch.object(PositionService, "execute_buy_budget") as buy, \
             patch.object(SignalService, "get_latest_signals", return_value=[]):
            AssetManagementService._rebalance_market(
                "sean", "KR", cash=300_000, stock_total=6_000_000, target_ratio=0.10,
                holdings=[holding], user_state=UserState(), macro_data=_macro(),
            )
            sell.assert_not_called()
            buy.assert_not_called()

    def test_asset_mgmt_still_buys_with_surplus_and_passes_macro(self):
        holding = HoldingSchema(ticker="005930", quantity=100, buy_price=50_000, current_price=60_000)
        macro = _macro()
        with patch.object(PositionService, "execute_buy_budget") as buy, \
             patch.object(SignalService, "get_latest_signals", return_value=[]):
            AssetManagementService._rebalance_market(
                "sean", "KR", cash=4_000_000, stock_total=6_000_000, target_ratio=0.10,
                holdings=[holding], user_state=UserState(), macro_data=macro,
            )
            buy.assert_called_once()
            self.assertIs(buy.call_args.kwargs["macro_data"], macro)
            self.assertAlmostEqual(buy.call_args.kwargs["budget_krw"], 3_000_000)


class TestCrashGuardCache(UptrendDcaBase):
    def test_status_follows_input_changes_immediately(self):
        self.assertEqual(CrashGuardService.get_status(_macro(vix=20.0)), (False, False, []))
        is_crash, is_frozen, reasons = CrashGuardService.get_status(_macro(vix=55.0))
        self.assertTrue(is_crash and is_frozen)
        self.assertTrue(any(r.startswith("frozen") for r in reasons))
        self.assertEqual(CrashGuardService.get_status(_macro(vix=20.0))[0], False)

    def test_crash_uses_worst_of_kospi_spx(self):
        is_crash, _, reasons = CrashGuardService.get_status(_macro(k1d=-1.0, s1d=-6.0))
        self.assertTrue(is_crash)
        self.assertTrue(reasons[0].startswith("index_1d(-6.0%"))


if __name__ == "__main__":
    unittest.main()
