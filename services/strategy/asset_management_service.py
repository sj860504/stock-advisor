from typing import List, Optional, Tuple
from models.schemas import HoldingSchema, MacroDataSnapshot, MarketRegimeSchema, UserState
from services.config.settings_service import SettingsService
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us

logger = get_logger("asset_management_service")


class AssetManagementService:
    """
    Asset allocation manager.
    Calculates budget/cash targets based on regime and delegates
    buy/sell execution to PositionService.
    """

    # ── Entry Point ────────────────────────────────────────────────────────────

    @classmethod
    def run(
        cls,
        user_id: str,
        holdings: List[HoldingSchema],
        kr_cash: float,
        usd_cash: float,
        macro_data: MacroDataSnapshot,
        is_kr_open: bool = True,
        is_us_open: bool = True,
        user_state: Optional[UserState] = None,
    ) -> None:
        """Asset allocation entry point.
        Calculates cash gap per open market and delegates buy/sell to PositionService."""
        regime: MarketRegimeSchema = macro_data.market_regime
        fear_greed: Optional[float] = macro_data.fear_greed

        target_ratio = cls._get_target_cash_ratio(regime, fear_greed, holdings)
        kr_stock_total, us_stock_total_usd = cls._calc_totals(holdings)

        logger.info(
            f"[AssetMgmt] regime={regime.status} fear_greed={fear_greed} "
            f"target_cash={target_ratio:.0%} "
            f"kr_stock={kr_stock_total:,.0f} us_stock=${us_stock_total_usd:,.2f}"
        )

        # 시장별 전략 활성화 여부 확인
        is_kr_strategy_enabled = SettingsService.get_bool("STRATEGY_ENABLED_KR", True)
        is_us_strategy_enabled = SettingsService.get_bool("STRATEGY_ENABLED_US", True)

        if is_kr_open and is_kr_strategy_enabled:
            cls._rebalance_market(user_id, "KR", kr_cash, kr_stock_total, target_ratio, holdings, user_state, macro_data)
        elif is_kr_open:
            logger.info("[AssetMgmt] KR Strategy is disabled. Skipping KR rebalance.")

        if is_us_open and is_us_strategy_enabled:
            cls._rebalance_market(user_id, "US", usd_cash, us_stock_total_usd, target_ratio, holdings, user_state, macro_data)
        elif is_us_open:
            logger.info("[AssetMgmt] US Strategy is disabled. Skipping US rebalance.")

    @classmethod
    def _rebalance_market(
        cls, user_id: str, market: str, cash: float, stock_total: float,
        target_ratio: float, holdings: List[HoldingSchema],
        user_state: Optional[UserState] = None,
        macro_data: Optional[MacroDataSnapshot] = None,
    ) -> None:
        """단일 시장(KR/US) 현금갭 계산 후 매수 또는 매도 위임.
        gap 크기 비례 BUY threshold 동적 완화 (cash-gap-aware)."""
        from services.strategy.signal_service import SignalService
        from services.strategy.position_service import PositionService

        gap = cls._calc_cash_gap(cash, stock_total, target_ratio)
        gap_str = f"{gap:,.0f}원" if market == "KR" else f"${gap:,.2f}"

        # gap percentage-points (현재 비중 - 목표) — gap-aware 임계 완화에 사용
        total = stock_total + max(0.0, cash)
        cash_ratio = (cash / total) if total > 0 else 0.0
        gap_pct = max(0.0, (cash_ratio - target_ratio) * 100)

        logger.info(f"[AssetMgmt] {market} gap={gap_str} ({gap_pct:.1f}%pa)")

        if gap > 0:
            signals = SignalService.get_latest_signals()
            # 시장별 시그널 필터
            from utils.market import is_kr as _is_kr
            mkt_signals = [s for s in signals if (_is_kr(s.ticker) if market == "KR" else not _is_kr(s.ticker))]

            # cash-gap-aware: gap 5%pa당 threshold +N, 최대 +M
            base_thr = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 40)
            relax_step = SettingsService.get_int("STRATEGY_GAP_RELAX_STEP", 5)
            relax_max = SettingsService.get_int("STRATEGY_GAP_RELAX_MAX", 20)
            relaxed_thr = base_thr + min(relax_max, int(gap_pct / 5) * relax_step)
            eligible = [s for s in mkt_signals if s.score <= relaxed_thr]
            logger.info(
                f"[AssetMgmt] {market} threshold {base_thr}→{relaxed_thr} "
                f"(eligible={len(eligible)}/{len(mkt_signals)})"
            )

            budget_krw = gap if market == "KR" else 0.0
            budget_usd = 0.0 if market == "KR" else gap
            PositionService.execute_buy_budget(
                user_id, budget_krw=budget_krw, budget_usd=budget_usd,
                signals=eligible, user_state=user_state, gap_pct=gap_pct,
                macro_data=macro_data,
            )
        elif gap < 0:
            if SettingsService.get_int("STRATEGY_UPTREND_DCA_ENABLED", 1) == 1:
                # Uptrend: 예비현금(RESERVE)은 DCA 가 소진하는 게 정상. 수익 종목을 팔아
                # 예비현금을 다시 채우는 것은 '매도 보수적' 원칙과 충돌 → 매도 스킵.
                logger.info(f"[AssetMgmt] {market} 현금 {cash_ratio:.1%} < 예비 {target_ratio:.1%} — Uptrend 모드: 현금확보 매도 스킵")
                return
            signals = SignalService.get_latest_signals()
            market_holdings = [h for h in holdings if (is_kr(h.ticker) if market == "KR" else not is_kr(h.ticker))]
            candidates = cls._select_sell_candidates(market_holdings, signals)
            if candidates:
                need_krw = abs(gap) if market == "KR" else 0.0
                need_usd = 0.0 if market == "KR" else abs(gap)
                PositionService.execute_sell_for_cash(user_id, need_krw=need_krw, need_usd=need_usd, candidates=candidates, user_state=user_state)
            else:
                logger.info(f"[AssetMgmt] {market} 현금 부족 but 수익 종목 없음 → 매도 스킵")

    # ── Target Cash Ratio ──────────────────────────────────────────────────────

    @classmethod
    def _get_target_cash_ratio(cls, regime: MarketRegimeSchema, fear_greed: Optional[float], holdings: List[HoldingSchema]) -> float:
        """Return target cash ratio based on regime + individual holding profit rates.

        Uptrend DCA 활성 시 max(MIN_CASH_RATIO, DCA_RESERVE_RATIO) 사용 — 일반 매수는 DCA 예비현금을 남기고, DCA 추매만 MIN_CASH 까지 사용.

        Legacy rules:
          - fear_greed < 10 → 0.0  (extreme fear, full investment)
          - BEAR    base 0.20, +10%p if ≥30% of holdings exceed 3% profit
          - NEUTRAL base 0.40, +10%p if ≥30% of holdings exceed 5% profit
          - BULL    base 0.40, +10%p if ≥30% of holdings exceed 7% profit
          - Upper bound: 0.60
        """
        from services.config.settings_service import SettingsService as _SS
        if _SS.get_int("STRATEGY_UPTREND_DCA_ENABLED", 1) == 1:
            # 일반 budget 매수는 DCA 예비현금(RESERVE) 이상 유지 — execution_service_v2 와 동일 규칙
            from services.strategy.execution_service_v2 import TradeExecutorService
            return TradeExecutorService._uptrend_target_cash_ratio()
        if fear_greed is not None and fear_greed < 10:
            return 0.0

        status = regime.status

        if status == "Bear":
            base, threshold = 0.20, 3.0
        elif status == "Bull":
            base, threshold = 0.40, 7.0
        else:
            base, threshold = 0.40, 5.0

        ratio = cls._calc_profit_exceeding_ratio(holdings, threshold)
        if ratio >= 0.30:
            return min(base + 0.10, 0.60)

        return base

    # ── Portfolio Totals ───────────────────────────────────────────────────────

    @staticmethod
    def _calc_totals(holdings: List[HoldingSchema]) -> Tuple[float, float]:
        """Sum stock evaluation values by market.
        Returns (kr_total_krw, us_total_usd). Pure function, no I/O."""
        kr_total = sum(
            (h.current_price or 0.0) * h.quantity
            for h in holdings if is_kr(h.ticker)
        )
        us_total_usd = sum(
            (h.current_price or 0.0) * h.quantity
            for h in holdings if not is_kr(h.ticker)
        )
        return kr_total, us_total_usd

    @staticmethod
    def _calc_cash_gap(cash: float, stock_total: float, target_ratio: float) -> float:
        """Calculate cash surplus/deficit vs target ratio for a single market.
          Positive = surplus (budget available for buying)
          Negative = deficit (need to sell for cash)
        Pure function, no I/O."""
        total = stock_total + max(0.0, cash)
        if total <= 0:
            return 0.0
        current_ratio = cash / total
        return (current_ratio - target_ratio) * total

    # ── Sell Candidates ────────────────────────────────────────────────────────

    @classmethod
    def _select_sell_candidates(cls, holdings: List[HoldingSchema], signals=None) -> List[HoldingSchema]:
        """Return profit-positive holdings sorted by score descending (then profit rate).
        Returns empty list if no profitable holdings → caller skips sell.
        Pure function, no I/O."""
        min_profit = 1.0  # 수수료 고려 최소 수익률 1%
        profitable = [h for h in holdings if cls._calc_holding_profit_pct(h) >= min_profit]
        score_map = {s.ticker: s.score for s in signals} if signals else {}
        return sorted(
            profitable,
            key=lambda h: (score_map.get(h.ticker, 50), cls._calc_holding_profit_pct(h)),
            reverse=True,
        )

    @classmethod
    def _calc_profit_exceeding_ratio(cls, holdings: List[HoldingSchema], threshold_pct: float) -> float:
        """Return ratio of holdings whose profit_pct exceeds threshold.
        Pure function, no I/O."""
        if not holdings:
            return 0.0
        exceeding = sum(
            1 for h in holdings
            if cls._calc_holding_profit_pct(h) >= threshold_pct
        )
        return exceeding / len(holdings)

    @staticmethod
    def _calc_holding_profit_pct(holding: HoldingSchema) -> float:
        """Calculate profit % for a single holding. Pure function."""
        buy_price = holding.buy_price
        current_price = holding.current_price or 0.0
        if not buy_price or buy_price <= 0:
            return 0.0
        return (current_price - buy_price) / buy_price * 100
