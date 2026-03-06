"""
SectorRebalancerService: 섹터 리밸런싱
- 섹터 비중 현황 조회 (get_sector_rebalance_status)
- 주간 섹터 리밸런싱 (run_sector_rebalance)
- 초과 섹터 분할 매도 / 부족 섹터 매수
"""
from services.market.market_data_service import MarketDataService
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.config.settings_service import SettingsService
from services.strategy.execution_service_v2 import TradeExecutorService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("sector_rebalancer_service")


class SectorRebalancerService:
    """섹터 비중 리밸런싱 서비스"""

    # ── 비중 현황 ─────────────────────────────────────────────────────────────

    @classmethod
    def _build_market_rebalance(cls, holdings: list, exchange_rate: float, market: str) -> dict:
        sw = TradeExecutorService._get_sector_group_weights(holdings, exchange_rate, market)
        weights = sw["weights"]
        mkt_holdings = [h for h in holdings if (is_kr(h.get("ticker", "")) == (market == "kr"))]
        under, over = TradeExecutorService._classify_holdings_by_deviation(mkt_holdings, weights)
        under.sort(key=lambda x: x["dev"])
        over.sort(key=lambda x: -x["dev"])
        return {"weights": weights, "underweight": under, "overweight": over,
                "total": sw["total"], "currency": sw["currency"]}

    @classmethod
    def get_sector_rebalance_status(cls, user_id: str = "sean") -> dict:
        """섹터 비중 현황 및 리밸런싱 필요 종목 반환 (API용). KR/US 분리."""
        exchange_rate = MacroService.get_exchange_rate()
        holdings = PortfolioService.load_portfolio(user_id)
        return {
            "kr": cls._build_market_rebalance(holdings, exchange_rate, "kr"),
            "us": cls._build_market_rebalance(holdings, exchange_rate, "us"),
        }

    # ── 초과 섹터 매도 ────────────────────────────────────────────────────────

    @classmethod
    def _get_overweight_groups(cls, weights: dict, threshold: float) -> list:
        """비중 초과 그룹 목록을 편차 내림차순으로 반환."""
        return sorted(
            [(grp, info) for grp, info in weights.items()
             if grp != "other" and info["dev"] > threshold],
            key=lambda x: -x[1]["dev"],
        )

    @classmethod
    def _try_sell_overweight_holding(
        cls, h: dict, grp: str, dev: float,
        holdings: list, market_total: float, cash_balance: float, exchange_rate: float,
        user_id: str, macro: dict, target_cash_kr: float, target_cash_us: float,
    ) -> tuple:
        """초과 그룹 내 보유 종목 1건에 대해 매도 시도. Returns (executed, skipped_entry, cash_delta)."""
        ticker = h["ticker"]
        if not TradeExecutorService._check_market_hours(ticker):
            return False, {"ticker": ticker, "reason": "시장비개장"}, 0.0
        current_price = float(h.get("current_price") or 0)
        buy_price     = float(h.get("buy_price") or 0)
        profit_pct    = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        if profit_pct < 0:
            return False, {"ticker": ticker, "reason": f"손실중({profit_pct:.1f}%) 리밸런싱 제외"}, 0.0
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"섹터리밸런싱-초과({grp} {dev:+.1%})",
            profit_pct, True, 60, current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=h, macro=macro,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        if executed:
            sell_val = current_price * max(1, int(h.get("quantity", 0) / 3))
            cash_delta = sell_val * (exchange_rate if not is_kr(ticker) else 1.0)
            return True, None, cash_delta
        return False, None, 0.0

    @classmethod
    def _execute_overweight_sells(
        cls, weights: dict, holdings: list,
        kr_total: float, us_total_krw: float, cash_balance: float, exchange_rate: float,
        user_id: str, macro: dict, target_cash_kr: float, target_cash_us: float,
    ) -> tuple:
        """STEP 1: 초과 섹터 보유 종목 분할 매도 → (sells_executed, sold_list, skipped_list, updated_cash)"""
        overweight_groups = cls._get_overweight_groups(weights, TradeExecutorService.SECTOR_REBAL_THRESHOLD)
        sold, skipped, sells_executed = [], [], 0
        for grp, info in overweight_groups:
            dev = info["dev"]
            grp_holdings = sorted(
                [h for h in holdings if h.get("quantity", 0) > 0 and TradeExecutorService._get_sector_group(h["ticker"], h) == grp],
                key=lambda h: (float(h.get("current_price") or 0) - float(h.get("buy_price") or 1)) / float(h.get("buy_price") or 1),
                reverse=True,
            )
            for h in grp_holdings:
                market_total = kr_total if is_kr(h["ticker"]) else us_total_krw
                executed, skip_entry, cash_delta = cls._try_sell_overweight_holding(
                    h, grp, dev, holdings, market_total, cash_balance, exchange_rate,
                    user_id, macro, target_cash_kr, target_cash_us,
                )
                if skip_entry:
                    skipped.append(skip_entry)
                    continue
                if executed:
                    sells_executed += 1
                    cash_balance += cash_delta
                    sold.append({"ticker": h["ticker"], "group": grp, "dev": round(dev, 4), "profit_pct": round((float(h.get("current_price") or 0) - float(h.get("buy_price") or 0)) / float(h.get("buy_price") or 1) * 100, 2)})
                    break
        return sells_executed, sold, skipped, cash_balance

    # ── 부족 섹터 매수 ────────────────────────────────────────────────────────

    @classmethod
    def _score_underweight_buy_candidates(
        cls, grp: str, all_states: dict, holdings_map: dict, macro: dict, user_state: dict,
        kr_total: float, us_total_krw: float, cash_balance: float, target_cash_kr: float, target_cash_us: float,
    ) -> list:
        """주어진 섹터 그룹의 매수 후보 종목 리스트를 스코어 오름차순으로 반환."""
        from services.strategy.signal_service import SignalService
        candidates = []
        for ticker, state in all_states.items():
            if not getattr(state, "is_ready", False):
                continue
            if TradeExecutorService._get_sector_group(ticker) != grp:
                continue
            if not TradeExecutorService._check_market_hours(ticker):
                continue
            holding = holdings_map.get(ticker)
            market_total = kr_total if is_kr(ticker) else us_total_krw
            score, reasons = SignalService.calculate_score(
                ticker, state, holding, macro, user_state, cash_balance,
                market_cash_ratio=target_cash_kr if is_kr(ticker) else target_cash_us,
                market_total_krw=market_total,
            )
            candidates.append((ticker, state, holding, score, reasons))
        candidates.sort(key=lambda x: x[3])
        return candidates

    @classmethod
    def _buy_best_underweight_candidate(
        cls, grp: str, dev: float, candidates: list, buy_threshold: int,
        holdings: list, kr_total: float, us_total_krw: float, cash_balance: float, exchange_rate: float,
        user_id: str, macro: dict, target_cash_kr: float, target_cash_us: float,
    ) -> tuple:
        """부족 섹터 최우선 후보 1건에 대해 매수 시도. Returns (executed, skipped_list, bought_entry)."""
        skipped = []
        for ticker, state, holding, score, reasons in candidates:
            if score > buy_threshold + 10:
                skipped.append({"ticker": ticker, "reason": f"매수신호미달(score={score})"})
                continue
            market_total = kr_total if is_kr(ticker) else us_total_krw
            executed = TradeExecutorService._execute_trade_v2(
                ticker, "buy", f"섹터리밸런싱-부족({grp} {dev:+.1%})",
                0.0, False, score, getattr(state, "current_price", 0),
                market_total, cash_balance, exchange_rate,
                holdings=holdings, user_id=user_id, holding=holding, macro=macro,
                target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            )
            if executed:
                return True, skipped, {"ticker": ticker, "group": grp, "dev": round(dev, 4), "score": score}
        return False, skipped, None

    @classmethod
    def _execute_underweight_buys(
        cls, weights: dict, holdings: list, kr_total: float, us_total_krw: float,
        cash_balance: float, exchange_rate: float, user_id: str, macro: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> tuple:
        """STEP 2: 부족 섹터 후보 종목 매수 → (buys_executed, bought_list, skipped_list)"""
        underweight_groups = sorted(
            [(grp, info) for grp, info in weights.items()
             if grp != "other" and info["dev"] < -TradeExecutorService.SECTOR_REBAL_THRESHOLD],
            key=lambda x: x[1]["dev"],
        )
        all_states   = MarketDataService.get_all_states()
        holdings_map = {h["ticker"]: h for h in holdings}
        user_state   = {"user_id": user_id}
        bought, skipped, buys_executed = [], [], 0
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        for grp, info in underweight_groups:
            dev = info["dev"]
            candidates = cls._score_underweight_buy_candidates(
                grp, all_states, holdings_map, macro, user_state,
                kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us,
            )
            executed, grp_skipped, bought_entry = cls._buy_best_underweight_candidate(
                grp, dev, candidates, buy_threshold, holdings, kr_total, us_total_krw, cash_balance,
                exchange_rate, user_id, macro, target_cash_kr, target_cash_us,
            )
            skipped.extend(grp_skipped)
            if executed and bought_entry:
                buys_executed += 1
                bought.append(bought_entry)
        return buys_executed, bought, skipped

    # ── 요약 및 알림 ─────────────────────────────────────────────────────────

    @classmethod
    def _build_rebalance_summary(cls, sold: list, bought: list, weights_before: dict, weights_after: dict) -> str:
        """섹터 리밸런싱 결과 Slack 요약 문자열 생성"""
        summary = (
            f"🔄 *주간 섹터 리밸런싱 완료*\n"
            f"매도: {len(sold)}건  |  매수: {len(bought)}건\n"
        )
        if sold:
            summary += "매도: " + ", ".join(f"{s['ticker']}({s['group']} {s['profit_pct']:+.1f}%)" for s in sold) + "\n"
        if bought:
            summary += "매수: " + ", ".join(f"{b['ticker']}({b['group']})" for b in bought) + "\n"
        for grp in ["tech", "value", "financial"]:
            bef = weights_before.get(grp, {}).get("weight", 0)
            aft = weights_after.get(grp, {}).get("weight", 0)
            tgt = TradeExecutorService.SECTOR_TARGET_WEIGHT.get(grp, 0)
            summary += f"  {grp}: {bef:.1%} → {aft:.1%}  (목표 {tgt:.0%})\n"
        return summary

    @classmethod
    def _notify_rebalance_slack(cls, summary: str) -> None:
        """섹터 리밸런싱 완료 요약 메시지를 Slack으로 전송."""
        try:
            from services.notification.alert_service import AlertService
            AlertService.send_slack_alert(summary)
        except Exception:
            pass

    # ── 리밸런싱 실행 ─────────────────────────────────────────────────────────

    @classmethod
    def _execute_rebalance_trades(
        cls, weights: dict, holdings: list, kr_total: float, us_total_krw: float, cash_balance: float,
        exchange_rate: float, user_id: str, macro: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> dict:
        """초과 매도 + 부족 매수를 수행하여 결과 dict(sold, bought, skipped) 반환."""
        result: dict = {"sold": [], "bought": [], "skipped": []}
        _, sold, skipped_s, cash_balance = cls._execute_overweight_sells(
            weights, holdings, kr_total, us_total_krw, cash_balance, exchange_rate,
            user_id, macro, target_cash_kr, target_cash_us,
        )
        result["sold"].extend(sold)
        result["skipped"].extend(skipped_s)
        _, bought, skipped_b = cls._execute_underweight_buys(
            weights, holdings, kr_total, us_total_krw, cash_balance, exchange_rate,
            user_id, macro, target_cash_kr, target_cash_us,
        )
        result["bought"].extend(bought)
        result["skipped"].extend(skipped_b)
        return result

    @classmethod
    def run_sector_rebalance(cls, user_id: str = "sean") -> dict:
        """주 1회 섹터 그룹 비중 리밸런싱.

        편차 < 5% → 패스, 5~10% → 절반 리밸런싱, >10% → 전체 리밸런싱
        초과 섹터 → 수익 높은 보유 종목 분할 매도
        부족 섹터 → DCF 저평가 + RSI 낮은 후보 매수
        """
        logger.info("🔄 주간 섹터 리밸런싱 시작...")
        exchange_rate = MacroService.get_exchange_rate()
        holdings      = PortfolioService.load_portfolio(user_id)
        macro         = MacroService.get_macro_data()
        cash_balance  = PortfolioService.get_cash_balance(user_id) or 0.0
        kr_total, us_total_krw, target_cash_kr, target_cash_us = TradeExecutorService._calculate_total_assets(holdings, cash_balance, macro)

        weights = TradeExecutorService._get_sector_group_weights(holdings, exchange_rate)["weights"]
        result = cls._execute_rebalance_trades(weights, holdings, kr_total, us_total_krw, cash_balance, exchange_rate, user_id, macro, target_cash_kr, target_cash_us)
        result["weights_before"] = weights

        sw_after = TradeExecutorService._get_sector_group_weights(PortfolioService.load_portfolio(user_id), exchange_rate)
        result["weights_after"] = sw_after["weights"]
        summary = cls._build_rebalance_summary(result["sold"], result["bought"], weights, sw_after["weights"])
        cls._notify_rebalance_slack(summary)
        logger.info(summary)
        result["summary"] = summary
        return result
