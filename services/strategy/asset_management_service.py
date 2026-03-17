from typing import List, Optional, Tuple
from models.schemas import HoldingSchema, MacroDataSnapshot, MarketRegimeSchema, UserState
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

        if is_kr_open:
            cls._rebalance_market(user_id, "KR", kr_cash, kr_stock_total, target_ratio, holdings, user_state)
        if is_us_open:
            cls._rebalance_market(user_id, "US", usd_cash, us_stock_total_usd, target_ratio, holdings, user_state)

    @classmethod
    def _rebalance_market(
        cls, user_id: str, market: str, cash: float, stock_total: float,
        target_ratio: float, holdings: List[HoldingSchema],
        user_state: Optional[UserState] = None,
    ) -> None:
        """단일 시장(KR/US) 현금갭 계산 후 매수 또는 매도 위임."""
        from services.strategy.signal_service import SignalService
        from services.strategy.position_service import PositionService

        gap = cls._calc_cash_gap(cash, stock_total, target_ratio)
        gap_str = f"{gap:,.0f}원" if market == "KR" else f"${gap:,.2f}"
        logger.info(f"[AssetMgmt] {market} gap={gap_str}")

        if gap > 0:
            signals = SignalService.get_latest_signals()
            budget_krw = gap if market == "KR" else 0.0
            budget_usd = 0.0 if market == "KR" else gap
            PositionService.execute_buy_budget(user_id, budget_krw=budget_krw, budget_usd=budget_usd, signals=signals, user_state=user_state)
        elif gap < 0:
            market_holdings = [h for h in holdings if (is_kr(h.ticker) if market == "KR" else not is_kr(h.ticker))]
            candidates = cls._select_sell_candidates(market_holdings)
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

        Rules:
          - fear_greed < 10 → 0.0  (extreme fear, full investment)
          - BEAR    base 0.20, +10%p if ≥30% of holdings exceed 3% profit
          - NEUTRAL base 0.40, +10%p if ≥30% of holdings exceed 5% profit
          - BULL    base 0.40, +10%p if ≥30% of holdings exceed 7% profit
          - Upper bound: 0.60
        """
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
    def _select_sell_candidates(cls, holdings: List[HoldingSchema]) -> List[HoldingSchema]:
        """Return profit-positive holdings sorted by profit rate descending.
        Returns empty list if no profitable holdings → caller skips sell.
        Pure function, no I/O."""
        profitable = [h for h in holdings if cls._calc_holding_profit_pct(h) > 0]
        return sorted(profitable, key=cls._calc_holding_profit_pct, reverse=True)

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
