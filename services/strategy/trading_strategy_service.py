from typing import Optional
from datetime import datetime
from models.schemas import MacroDataSnapshot, UserState
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.market_data_service import MarketDataService
from services.market.market_hour_service import MarketHourService
from services.market.data_service import DataService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from services.strategy.execution_service_v2 import TradeExecutorService
from services.strategy.signal_service import SignalService
from services.strategy.position_service import PositionService
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us

logger = get_logger("strategy_service")

# Cache TTL (seconds)
TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class TradingStrategyService:
    """
    Trading signal evaluation and execution service based on user investment strategy.
    (Orchestrator - delegates actual logic to sub-services)
    """
    _enabled = False

    # ── Enabled State Management ─────────────────────────────────────────────────────

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        cls._enabled = enabled
        logger.info(f"⚙️ Trading Strategy Engine {'ENABLED' if enabled else 'DISABLED'}")
        try:
            SettingsService.set_setting("STRATEGY_ENABLED", "true" if enabled else "false")
        except Exception as e:
            logger.warning(f"⚠️ Failed to persist strategy enabled state: {e}")

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def _restore_enabled_state(cls) -> None:
        """Restore saved enabled state on app startup."""
        try:
            persisted = SettingsService.get_setting("STRATEGY_ENABLED", None)
            if persisted == "true":
                cls._enabled = True
                logger.info("⚙️ Trading Strategy Engine restored: ENABLED (from last session)")
            else:
                logger.info("⚙️ Trading Strategy Engine restored: DISABLED (default or last session)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore strategy enabled state: {e}")

    # ── State Save/Load ────────────────────────────────────────────────────────

    @classmethod
    def _load_state(cls, user_id: str = "sean") -> dict:
        """Load user_id strategy state from DB."""
        from repositories.strategy_state_repo import StrategyStateRepo
        raw = StrategyStateRepo.load(user_id)
        if raw:
            return {user_id: UserState(user_id=user_id, **raw)}
        return {user_id: UserState(user_id=user_id)}

    @classmethod
    def _save_state(cls, state: dict) -> None:
        """Save per-user state from state dict to DB."""
        from repositories.strategy_state_repo import StrategyStateRepo
        for user_id, user_state in state.items():
            if isinstance(user_state, UserState):
                StrategyStateRepo.save(user_id, user_state.model_dump(exclude={"user_id"}))
            elif isinstance(user_state, dict):
                StrategyStateRepo.save(user_id, user_state)

    @classmethod
    def reset_cooldown(cls, user_id: str = "sean", ticker: Optional[str] = None, action: Optional[str] = None) -> bool:
        """Reset cooldown for a specific ticker/action or all cooldowns if not specified."""
        try:
            logger.info(f"🔄 reset_cooldown request: user={user_id}, ticker={ticker}, action={action}")
            state = cls._load_state(user_id)
            user_state = state.get(user_id)
            if not user_state:
                logger.warning(f"⚠️ No user state found for {user_id}")
                return False

            if ticker and ticker.strip():
                ticker = ticker.strip().upper()
                if action == "buy" or not action:
                    removed = user_state.add_buy_cooldown.pop(ticker, None)
                    if removed: logger.info(f"🔓 Buy cooldown removed for {ticker}")
                if action == "sell" or not action:
                    removed = user_state.sell_cooldown.pop(ticker, None)
                    if removed: logger.info(f"🔓 Sell cooldown removed for {ticker}")
                logger.info(f"🔓 Cooldown reset for {ticker} ({action or 'all'}) complete.")
            else:
                # If ticker is None, it means reset ALL for the specified action (or all actions)
                if action == "buy":
                    user_state.add_buy_cooldown = {}
                    logger.info(f"🔓 All BUY cooldowns reset for user {user_id}")
                elif action == "sell":
                    user_state.sell_cooldown = {}
                    logger.info(f"🔓 All SELL cooldowns reset for user {user_id}")
                else:
                    user_state.add_buy_cooldown = {}
                    user_state.sell_cooldown = {}
                    logger.info(f"🔓 ALL (BUY+SELL) cooldowns reset for user {user_id}")

            cls._save_state(state)
            return True
        except Exception as e:
            logger.error(f"Failed to reset cooldown: {e}")
            return False

    # ── Public API Delegation Wrappers ───────────────────────────────────────────────────

    @classmethod
    def calculate_score(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, cash_balance: float, market_cash_ratio: float = None, market_total_krw: float = 0.0) -> tuple:
        """Calculate individual stock investment score (delegates to SignalService)."""
        return SignalService.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """Public interface for external individual stock analysis (delegates to SignalService)."""
        return SignalService.analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)

    @classmethod
    def get_top_weight_overrides(cls) -> dict:
        """Get per-ticker user weight overrides (delegates to TradeExecutorService)."""
        return TradeExecutorService.get_top_weight_overrides()

    @classmethod
    def set_top_weight_overrides(cls, overrides: dict) -> dict:
        """Save per-ticker user weight overrides (delegates to TradeExecutorService)."""
        return TradeExecutorService.set_top_weight_overrides(overrides)

    # ── Asset Calculation ─────────────────────────────────────────────────────────────

    @classmethod
    def _log_intramarket_cash_ratio(cls, holdings: list, cash_balance: float, usd_cash: float, exchange_rate: float, target_cash_kr: float, target_cash_us: float) -> None:
        """Log per-market cash ratio (warning only, no auto-sell)."""
        kr_holdings = [h for h in filter_kr(holdings) if h.quantity > 0]
        us_holdings = [h for h in filter_us(holdings) if h.quantity > 0]

        kr_stock_val = sum((h.current_price or 0) * h.quantity for h in kr_holdings)
        us_stock_usd = sum((h.current_price or 0) * h.quantity for h in us_holdings)

        kr_total = kr_stock_val + max(0.0, cash_balance)
        us_total_usd = us_stock_usd + usd_cash

        kr_cash_ratio = cash_balance / kr_total if kr_total > 0 else 0.0
        us_cash_ratio = usd_cash / us_total_usd if us_total_usd > 0 else 0.0

        kr_stock_ratio = 1.0 - kr_cash_ratio
        us_stock_ratio = 1.0 - us_cash_ratio

        logger.info(
            f"[Portfolio Allocation] "
            f"KR Stock {kr_stock_ratio:.1%} / Cash {kr_cash_ratio:.1%} (Target {target_cash_kr:.1%}) | "
            f"US Stock {us_stock_ratio:.1%} / Cash {us_cash_ratio:.1%} (Target {target_cash_us:.1%})"
        )
        if kr_cash_ratio < target_cash_kr - 0.05 and kr_total > 0:
            logger.warning(f"KR cash low ({kr_cash_ratio:.1%} < target {target_cash_kr:.1%}). Consider taking profit.")
        if us_cash_ratio < target_cash_us - 0.05 and us_total_usd > 0:
            logger.warning(f"US cash low ({us_cash_ratio:.1%} < target {target_cash_us:.1%}). Consider taking profit.")

    # ── Universe Management ─────────────────────────────────────────────────────────

    @classmethod
    def _update_target_universe(cls, user_id: str, run_kr: bool = True, run_us: bool = True) -> set:
        """Detect Top 100 changes and clean up universe."""
        def _norm_ticker(t: str) -> str:
            t = str(t or "").strip().upper()
            if not t: return ""
            if t.isdigit() and len(t) < 6: t = t.zfill(6)
            return t

        from repositories.watchlist_repo import WatchlistRepo
        from repositories.settings_repo import SettingsRepo

        def _norm_mode(raw: str) -> str:
            val = str(raw or "universe")
            return "custom" if val == "watchlist" else val

        kr_mode = _norm_mode(SettingsRepo.get("kr_strategy_mode"))
        us_mode = _norm_mode(SettingsRepo.get("us_strategy_mode"))

        kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)] if run_kr else []
        us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)] if run_us else []
        portfolio = PortfolioService.load_portfolio(user_id)
        holdings = [_norm_ticker(h.ticker) for h in portfolio]

        kr_holdings = [t for t in holdings if t and is_kr(t) and len(t) == 6]
        us_holdings = [t for t in holdings if t and t.isalpha()]

        watchlist_raw = WatchlistRepo.get_tickers(user_id)
        watchlist_norm = [_norm_ticker(t) for t in watchlist_raw]
        wl_kr = [t for t in watchlist_norm if t and is_kr(t) and len(t) == 6]
        wl_us = [t for t in watchlist_norm if t and t.isalpha()]

        if kr_mode == "universe":
            all_kr = list(set([t for t in kr_tickers if t and is_kr(t) and len(t) == 6] + kr_holdings + wl_kr))
        else:
            all_kr = list(set(kr_holdings + wl_kr))

        if us_mode == "universe":
            all_us = list(set([t for t in us_tickers if t and t.isalpha()] + us_holdings + wl_us))
        else:
            all_us = list(set(us_holdings + wl_us))

        target_universe = set(all_kr + all_us)

        MarketDataService.prune_states(target_universe)
        logger.info(
            f"Universe updated: {len(target_universe)} tickers "
            f"(KR={len(all_kr)} [{kr_mode}], US={len(all_us)} [{us_mode}])"
        )
        return target_universe

    # ── Portfolio Report ─────────────────────────────────────────────────────

    @classmethod
    def _load_latest_portfolio(cls, user_id: str) -> tuple[list, float, dict]:
        """KIS 재동기화 후 최신 보유종목/현금/summary 반환.
        Returns (latest_holdings, latest_cash, summary)."""
        PortfolioService.sync_with_kis(user_id)
        latest_holdings = PortfolioService.load_portfolio(user_id)
        summary = PortfolioService.get_last_balance_summary()
        latest_cash = PortfolioService.load_cash(user_id)
        return latest_holdings, latest_cash, summary

    @classmethod
    def _filter_report_changes(
        cls, before_snapshot: dict, after_snapshot: dict,
        executed_tickers: set, latest_holdings: list,
    ) -> tuple[set, list]:
        """before/after 스냅샷 비교 → executed_tickers 교차필터 → 실제 체결 변경 반환.
        Returns (changed_tickers: set, changed_holdings: list). 순수 함수, I/O 없음."""
        all_tickers = set(before_snapshot.keys()) | set(after_snapshot.keys())
        changed_tickers = {
            t for t in all_tickers
            if before_snapshot.get(t, 0) != after_snapshot.get(t, 0)
        }
        if executed_tickers is not None:
            changed_tickers &= executed_tickers
        changed_holdings = [h for h in latest_holdings if h.ticker in changed_tickers]
        return changed_tickers, changed_holdings

    @classmethod
    def _send_portfolio_report(cls, user_id: str, before_snapshot: dict, executed_tickers: set = None) -> None:
        """체결 내역 비교 후 변경 포지션만 Slack 발송. 조율만."""
        try:
            from services.notification.report_service import ReportService
            latest_holdings, latest_cash, summary = cls._load_latest_portfolio(user_id)
            after_snapshot = {h.ticker: h.quantity for h in latest_holdings}
            if before_snapshot == after_snapshot:
                logger.info("No position changes. Skipping trade report.")
                return
            changed_tickers, changed_holdings = cls._filter_report_changes(
                before_snapshot, after_snapshot, executed_tickers, latest_holdings,
            )
            if not changed_tickers:
                logger.info("No executed ticker changes after filtering. Skipping trade report.")
                return
            states = MarketDataService.get_all_states()
            msg = ReportService.format_trade_result_report(
                changed_holdings, changed_tickers, before_snapshot, after_snapshot,
                latest_cash, states, summary,
            )
            AlertService.send_slack_alert(msg)
        except Exception as e:
            logger.warning(f"Trade report send failed: {e}")

    # ── sell_all_and_rebuy ────────────────────────────────────────────────────

    @classmethod
    def _build_sell_rebuy_result(cls, success_count: int, fail_count: int, failed_tickers: list, strategy_error: str = None) -> dict:
        """Build and return sell_all_and_rebuy result dict."""
        if strategy_error is None:
            return {
                "status": "success",
                "message": f"Sell all & rebuy complete (sold: {success_count}, failed: {fail_count})",
                "sold": success_count,
                "failed": fail_count,
                "failed_tickers": failed_tickers or None,
            }
        return {
            "status": "partial",
            "message": f"Sell done (success: {success_count}, failed: {fail_count}), strategy execution failed",
            "sold": success_count,
            "failed": fail_count,
            "failed_tickers": failed_tickers or None,
            "strategy_error": strategy_error,
        }

    @classmethod
    def sell_all_and_rebuy(cls, user_id: str = "sean") -> dict:
        """Sell all holdings then rebuy according to strategy."""
        logger.info("Sell all holdings & rebuy with strategy starting")
        holdings = PortfolioService.sync_with_kis(user_id)
        if not holdings:
            return {"status": "success", "message": "No holdings to sell.", "sold": 0, "failed": 0}
        logger.info(f"Found {len(holdings)} holdings to sell")
        success_count, fail_count, failed_tickers = OrderService.execute_mass_sell(holdings)
        PortfolioService.sync_with_kis(user_id)
        try:
            cls.run_strategy(user_id)
            logger.info("Strategy execution complete")
            return cls._build_sell_rebuy_result(success_count, fail_count, failed_tickers)
        except Exception as e:
            logger.error(f"Strategy execution error: {e}")
            return cls._build_sell_rebuy_result(success_count, fail_count, failed_tickers, str(e))

    # ── Strategy Execution ─────────────────────────────────────────────────────────────

    @classmethod
    def _init_strategy_user_state(cls, state: dict, user_id: str) -> UserState:
        """Initialize (or restore) user_id section in state dict and return UserState."""
        if user_id not in state:
            state[user_id] = UserState(user_id=user_id)
        return state[user_id]

    @classmethod
    def _validate_preconditions(cls, user_id: str) -> tuple[bool, bool, bool]:
        """Check strategy enabled + market open status.
        Returns (can_run: bool, is_kr_open: bool, is_us_open: bool)."""
        if not cls.is_enabled():
            logger.debug("⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return False, False, False

        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)

        if not is_kr_open and not is_us_open:
            logger.debug("⏸️ Both markets closed. Skipping strategy run.")
            return False, False, False

        return True, is_kr_open, is_us_open

    @classmethod
    def _load_and_sync_portfolio(cls, user_id: str) -> tuple[list, float, float, dict]:
        """Sync portfolio with KIS and return holdings + cash info.
        Returns (holdings, kr_cash, usd_cash, before_snapshot)."""
        holdings = PortfolioService.sync_with_kis(user_id)
        before_snapshot = {h.ticker: h.quantity for h in holdings}
        kr_cash = PortfolioService.load_cash(user_id)
        usd_cash = PortfolioService.get_usd_cash_balance()
        return holdings, kr_cash, usd_cash, before_snapshot

    @classmethod
    def _load_macro_and_assets(cls, holdings: list, kr_cash: float) -> tuple:
        """Load macro data and calculate asset totals.
        Returns (macro_snapshot, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us)."""
        macro_data = MacroService.get_macro_data()
        macro_snapshot = MacroDataSnapshot(**{k: v for k, v in macro_data.items() if k != "timestamp"})
        exchange_rate = MacroService.get_exchange_rate()
        kr_total, us_total_krw, target_cash_kr, target_cash_us = TradeExecutorService._calculate_total_assets(holdings, kr_cash, macro_snapshot)
        return macro_snapshot, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us

    @classmethod
    def _verify_pending_orders(cls) -> None:
        """DB의 pending 주문을 KIS 미체결 API로 확인하여 체결 상태 갱신."""
        try:
            results = OrderService.verify_and_update_pending_orders()
            if results:
                filled = [r for r in results if r.is_filled]
                pending = [r for r in results if not r.is_filled]
                if filled:
                    logger.info(f"✅ 체결 확인: {', '.join(r.ticker for r in filled)}")
                if pending:
                    logger.info(f"⏳ 미체결 대기: {', '.join(f'{r.ticker}({r.remaining_qty}주)' for r in pending)}")
        except Exception as e:
            logger.warning(f"⚠️ 미체결 확인 실패 (무시하고 계속): {e}")

    @classmethod
    def _load_user_state(cls, user_id: str) -> tuple[dict, UserState]:
        """Load and initialize user strategy state.
        Returns (state, user_state)."""
        state = cls._load_state(user_id)
        user_state = cls._init_strategy_user_state(state, user_id)
        return state, user_state

    @classmethod
    def _run_signals_and_execute(
        cls, user_id: str, holdings: list, macro_snapshot: MacroDataSnapshot, user_state: UserState,
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float,
        usd_cash: float = 0.0, exchange_rate: float = 1350.0,
        run_kr: bool = True, run_us: bool = True,
    ) -> tuple[bool, set]:
        """Collect signals and execute trades via PositionService.
        Returns (trade_executed: bool, executed_tickers: set)."""
        from repositories.watchlist_repo import WatchlistRepo
        from repositories.settings_repo import SettingsRepo

        kr_mode = SettingsRepo.get("kr_strategy_mode")
        kr_mode = "custom" if kr_mode == "watchlist" else (kr_mode or "universe")
        us_mode = SettingsRepo.get("us_strategy_mode")
        us_mode = "custom" if us_mode == "watchlist" else (us_mode or "universe")

        watchlist_raw = WatchlistRepo.get_tickers(user_id)
        watchlist_set = {t.strip().upper().zfill(6) if t.strip().isdigit() else t.strip().upper() for t in watchlist_raw}

        prepared_signals = SignalService._collect_trading_signals(
            holdings, macro_snapshot, user_state, kr_total, us_total_krw, cash_balance,
            target_cash_kr, target_cash_us, usd_cash=usd_cash, exchange_rate=exchange_rate,
            watchlist_kr=watchlist_set if kr_mode == "custom" else None,
            watchlist_us=watchlist_set if us_mode == "custom" else None,
            run_kr=run_kr, run_us=run_us,  # 필트링 플래그 전달
        )
        return PositionService._execute_collected_signals(
            user_id, prepared_signals, holdings, kr_total, us_total_krw, cash_balance,
            target_cash_kr, target_cash_us, macro_snapshot, user_state, usd_cash=usd_cash,
            run_kr=run_kr, run_us=run_us,
        )

    @classmethod
    def run_strategy(cls, user_id: str = "sean") -> None:
        """Full strategy execution loop."""
        can_run, is_kr_open, is_us_open = cls._validate_preconditions(user_id)
        if not can_run:
            return
        is_kr_strategy_enabled = SettingsService.get_bool("STRATEGY_ENABLED_KR", True)
        is_us_strategy_enabled = SettingsService.get_bool("STRATEGY_ENABLED_US", True)
        
        # 활성화된 시장만 실행 대상
        run_kr = is_kr_open and is_kr_strategy_enabled
        run_us = is_us_open and is_us_strategy_enabled

        if not run_kr and not run_us:
            logger.info("⏸️ All active market strategies are disabled or markets closed.")
            return

        markets = (["KR"] if run_kr else []) + (["US"] if run_us else [])
        logger.info(f"🚀 Running Trading Strategy for {user_id} (markets: {', '.join(markets)})...")

        cls._verify_pending_orders()
        cls._update_target_universe(user_id, run_kr=run_kr, run_us=run_us)

        holdings, kr_cash, usd_cash, before_snapshot = cls._load_and_sync_portfolio(user_id)
        macro_snapshot, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us = cls._load_macro_and_assets(holdings, kr_cash)
        state, user_state = cls._load_user_state(user_id)

        cls._log_intramarket_cash_ratio(holdings, kr_cash, usd_cash, exchange_rate, target_cash_kr, target_cash_us)

        trade_executed, executed_tickers = cls._run_signals_and_execute(
            user_id, holdings, macro_snapshot, user_state, kr_total, us_total_krw,
            kr_cash, target_cash_kr, target_cash_us, usd_cash=usd_cash, exchange_rate=exchange_rate,
            run_kr=run_kr, run_us=run_us,  # 필터링 플래그 전달
        )
        cls._save_state(state)
        logger.info("Strategy execution and trade decisions complete.")

        from services.strategy.asset_management_service import AssetManagementService
        AssetManagementService.run(
            user_id, holdings, kr_cash, usd_cash, macro_snapshot,
            is_kr_open=is_kr_open, is_us_open=is_us_open,
            user_state=user_state,
        )
        cls._save_state(state)  # AssetManagement 쿨다운 변경사항 영속

        # 개별 체결 알림(_send_trade_alert)이 트리거/자산/여유 포함하므로 요약 리포트 생략
        # if trade_executed and executed_tickers:
        #     cls._send_portfolio_report(user_id, before_snapshot, executed_tickers)

    # ── Waiting List / Opportunities ────────────────────────────────────────────────

    @classmethod
    def _build_waiting_list_entry(cls, ticker: str, ticker_state, score: int, reasons: list) -> dict:
        """Build individual waiting list entry dict."""
        action = "BUY" if score <= SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30) else "SELL"
        return {
            "ticker": ticker,
            "name": getattr(ticker_state, "name", None) or ticker,
            "current_price": ticker_state.current_price,
            "score": score,
            "action": action,
            "reasons": reasons,
            "rsi": ticker_state.rsi,
        }

    @classmethod
    def get_waiting_list(cls, user_id: str = "sean") -> list:
        """Get trading waiting list (stocks with BUY/SELL signals)."""
        all_states = MarketDataService.get_all_states()
        all_state_items = list(all_states.items())
        holdings = PortfolioService.load_portfolio(user_id)
        raw_macro = MacroService.get_macro_data()
        macro_snapshot = MacroDataSnapshot(**{k: v for k, v in raw_macro.items() if k != "timestamp"})

        state = cls._load_state(user_id)
        user_state = state.get(user_id, UserState())

        cash_balance = PortfolioService.load_cash(user_id)
        kr_total, us_total_krw, _, _ = TradeExecutorService._calculate_total_assets(holdings, cash_balance, macro_snapshot)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        holdings_map = {h.ticker: h for h in holdings}
        waiting_list = []
        for ticker, ticker_state in all_state_items:
            holding = holdings_map.get(ticker)
            market_total = kr_total if is_kr(ticker) else us_total_krw
            score, reasons, _breakdown = SignalService.calculate_score(ticker, ticker_state, holding, macro_snapshot, user_state, cash_balance, market_total_krw=market_total)
            if score <= buy_threshold_max or score >= sell_threshold_min:
                waiting_list.append(cls._build_waiting_list_entry(ticker, ticker_state, score, reasons))

        return sorted(waiting_list, key=lambda item: item["score"], reverse=True)

    @classmethod
    def get_opportunities(cls, user_id: str = "sean") -> list:
        """Alias for get_waiting_list for script compatibility."""
        return cls.get_waiting_list(user_id)

    # ── Manual Sell ────────────────────────────────────────────────────────────

    @classmethod
    def execute_sell(cls, ticker: str, quantity: int = 0, user_id: str = "sean") -> dict:
        """Execute manual sell."""
        holdings = PortfolioService.sync_with_kis(user_id)
        holding = next((h for h in holdings if h.ticker == ticker), None)

        if not holding:
            return {"status": "failed", "msg": "Not a held stock."}

        max_qty = holding.quantity
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty

        logger.info(f"manual sell execution: {ticker} {quantity} qty")

        current_price = holding.current_price or 0
        ok, err = OrderService.sell_single_holding(ticker, holding.name or ticker, quantity, current_price)
        if ok:
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=current_price,
                result_msg="Manual Sell Execution",
                strategy_name="manual",
                buy_price=holding.buy_price,
            )
            return {"status": "success", "msg": f"{ticker} {quantity} shares sold"}
        return {"status": "failed", "msg": err}
