import json
from config import Config
from typing import Optional
from datetime import datetime, timedelta
import pytz
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.market_data_service import MarketDataService
from services.market.market_hour_service import MarketHourService
from services.market.data_service import DataService
from services.kis.kis_service import KisService
from services.market.stock_meta_service import StockMetaService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from services.strategy.execution_service_v2 import TradeExecutorService
from services.strategy.signal_service import SignalService
from services.strategy.position_service import PositionService
from services.strategy.sector_rebalancer_service import SectorRebalancerService
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
        """Restore saved enabled state on app startup. Includes one-time JSON -> DB migration."""
        cls._migrate_json_to_db()
        try:
            persisted = SettingsService.get_setting("STRATEGY_ENABLED", None)
            if persisted == "true":
                cls._enabled = True
                logger.info("⚙️ Trading Strategy Engine restored: ENABLED (from last session)")
            else:
                logger.info("⚙️ Trading Strategy Engine restored: DISABLED (default or last session)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore strategy enabled state: {e}")

    @classmethod
    def _migrate_json_to_db(cls) -> None:
        """One-time strategy_state.json -> DB migration. Skipped if JSON file doesn't exist."""
        import os
        json_path = os.path.join(os.path.dirname(__file__), "..", "data", "strategy_state.json")
        if not os.path.exists(json_path):
            return
        try:
            from repositories.strategy_state_repo import StrategyStateRepo
            with open(json_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            migrated = False
            for key, val in old.items():
                if key.startswith("_") or not isinstance(val, dict):
                    continue
                existing = StrategyStateRepo.load(key)
                if existing:
                    continue
                StrategyStateRepo.save(key, val)
                migrated = True
                logger.info(f"strategy_state migration complete: user={key}")
            if "_enabled" in old and SettingsService.get_setting("STRATEGY_ENABLED", None) is None:
                SettingsService.set_setting("STRATEGY_ENABLED", "true" if old["_enabled"] else "false")
            overrides = old.get("_global", {}).get("top_weight_overrides")
            if overrides and SettingsService.get_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", None) is None:
                SettingsService.set_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", json.dumps(overrides, ensure_ascii=False))
            if migrated:
                bak = json_path + ".migrated"
                os.rename(json_path, bak)
                logger.info(f"Migration done. JSON backup: {bak}")
        except Exception as e:
            logger.warning(f"strategy_state JSON migration failed: {e}")

    # ── State Save/Load ────────────────────────────────────────────────────────

    @classmethod
    def _load_state(cls, user_id: str = "sean") -> dict:
        """Load user_id strategy state from DB."""
        from repositories.strategy_state_repo import StrategyStateRepo
        user_state = StrategyStateRepo.load(user_id)
        return {user_id: user_state} if user_state else {user_id: {"panic_locks": {}, "sell_cooldown": {}, "add_buy_cooldown": {}, "tick_trade": {}, "split_orders": {}}}

    @classmethod
    def _save_state(cls, state: dict) -> None:
        """Save per-user state from state dict to DB."""
        from repositories.strategy_state_repo import StrategyStateRepo
        for user_id, user_state in state.items():
            if isinstance(user_state, dict):
                StrategyStateRepo.save(user_id, user_state)

    # ── Public API Delegation Wrappers ───────────────────────────────────────────────────

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, market_cash_ratio: float = None, market_total_krw: float = 0.0) -> tuple:
        """Calculate individual stock investment score (delegates to SignalService)."""
        return SignalService.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """Public interface for external individual stock analysis (delegates to SignalService)."""
        return SignalService.analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)

    @classmethod
    def get_sector_rebalance_status(cls, user_id: str = "sean") -> dict:
        """Return sector weight status and stocks needing rebalancing (delegates to SectorRebalancerService)."""
        return SectorRebalancerService.get_sector_rebalance_status(user_id)

    @classmethod
    def run_sector_rebalance(cls, user_id: str = "sean") -> dict:
        """Weekly sector group weight rebalancing (delegates to SectorRebalancerService)."""
        return SectorRebalancerService.run_sector_rebalance(user_id)

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
        kr_holdings = [h for h in filter_kr(holdings) if h.get('quantity', 0) > 0]
        us_holdings = [h for h in filter_us(holdings) if h.get('quantity', 0) > 0]

        kr_stock_val = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_stock_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)

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

    # ── Tick Trading ──────────────────────────────────────────────────────────────

    @classmethod
    def _is_near_market_close(cls, ticker: str, minutes: int = 5) -> bool:
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if is_kr(ticker):
            tz = pytz.timezone("Asia/Seoul")
            now = datetime.now(tz)
            kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS)
            end_h, end_m = (18, 0) if kr_allow_extended else (15, 30)
            close_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
            return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        end_h, end_m = (20, 0) if allow_extended else (16, 0)
        close_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time

    @classmethod
    def _evaluate_tick_sell_conditions(cls, ticker: str, holding: dict, state, pnl_pct: float, tp_pct: float, sl_pct: float, trade_state: dict) -> bool:
        """Check and execute tick trade sell conditions."""
        hold_qty = int(holding.get("quantity", 0))
        if hold_qty > 0 and (pnl_pct >= tp_pct or pnl_pct <= sl_pct):
            result = KisService.send_order(ticker, hold_qty, 0, "sell")
            if result.get("status") == "success":
                reason = "Tick TP" if pnl_pct >= tp_pct else "Tick SL"
                OrderService.record_trade(ticker, "sell", hold_qty, getattr(state, 'current_price', 0), reason, "tick_strategy")
                TradeExecutorService._send_tick_alert(ticker, "sell", getattr(state, 'current_price', 0), hold_qty, reason, pnl_pct, holding)
                trade_state.update({"second_done": False, "last_sell_price": float(getattr(state, 'current_price', 0))})
                return True
        return False

    @classmethod
    def _evaluate_tick_buy_conditions(cls, ticker: str, tranche: float, state, holding: dict, pnl_pct: float, add_pct: float, trade_state: dict, low_1h: float, entry_pct: float) -> bool:
        """Check and execute tick trade buy (initial/add) conditions."""
        current_price = getattr(state, 'current_price', 0)
        qty = int(tranche // current_price) if current_price > 0 else 0
        if qty <= 0: return False

        if holding and not trade_state.get("second_done") and pnl_pct <= add_pct:
            result = KisService.send_order(ticker, qty, 0, "buy")
            if result.get("status") == "success":
                OrderService.record_trade(ticker, "buy", qty, current_price, "Tick Add", "tick_strategy")
                TradeExecutorService._send_tick_alert(ticker, "buy", current_price, qty, "Tick Add", holding=holding)
                trade_state["second_done"] = True
                return True
        elif not holding:
            last_sell = trade_state.get("last_sell_price")
            reentry = float(last_sell) * (1 + entry_pct / 100.0) if last_sell else None
            trigger = float(current_price) <= reentry if reentry else float(current_price) <= low_1h * 1.001
            if trigger:
                result = KisService.send_order(ticker, qty, 0, "buy")
                if result.get("status") == "success":
                    reason = "Tick ReEntry" if reentry else "Tick Entry (1h low)"
                    OrderService.record_trade(ticker, "buy", qty, current_price, reason, "tick_strategy")
                    TradeExecutorService._send_tick_alert(ticker, "buy", current_price, qty, reason)
                    trade_state["second_done"] = False
                    return True
        return False

    @classmethod
    def _get_or_reset_tick_state(cls, user_state: dict, today: str) -> dict:
        """Return tick_trade state, resetting if the date has changed."""
        trade_state = user_state.get(
            "tick_trade",
            {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}
        )
        if trade_state.get("date") != today:
            trade_state = {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}
        return trade_state

    @classmethod
    def _update_price_window(cls, trade_state: dict, current_price: float) -> tuple:
        """Update 1-hour price window and return (price_window, low_1h)."""
        now_ts = datetime.now().timestamp()
        pw = [p for p in trade_state.get("price_window", []) if p[0] >= now_ts - 3600]
        pw.append([now_ts, float(current_price)])
        trade_state["price_window"] = pw
        low_1h = min((p[1] for p in pw), default=float(current_price))
        return pw, low_1h

    @classmethod
    def _execute_tick_eod_sell(cls, ticker: str, holding: dict, holdings: list, current_price: float, trade_state: dict, user_state: dict, tick_state: dict) -> bool:
        """Tick trade EOD full sell near market close. Returns True on success."""
        qty = int(holding.get("quantity", 0))
        if qty > 0 and KisService.send_order(ticker, qty, 0, "sell").get("status") == "success":
            OrderService.record_trade(ticker, "sell", qty, current_price, "Tick EOD", "tick_strategy")
            holding_eod = next((h for h in holdings if h["ticker"] == ticker), None)
            buy_p = float(holding_eod.get("buy_price", 0)) if holding_eod else 0
            pnl_eod = (current_price - buy_p) / buy_p * 100 if buy_p else 0
            TradeExecutorService._send_tick_alert(ticker, "sell", current_price, qty, "Tick EOD", pnl_eod, holding_eod)
            trade_state.update({"second_done": False, "last_sell_price": float(current_price)})
            user_state["tick_trade"] = trade_state
            cls._save_state(tick_state)
            return True
        return False

    @classmethod
    def _run_tick_intraday(cls, ticker: str, state, holding, holdings: list, market_total: float, cash_balance: float, trade_state: dict, low_1h: float) -> bool:
        """Execute tick trade intraday sell/buy conditions. Returns executed."""
        tranche = min(cash_balance, max(0.0, market_total * SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.2))) / 2
        buy_price = float(holding.get("buy_price", 1)) if holding and float(holding.get("buy_price", 1)) > 0 else 1.0
        pnl_pct = (getattr(state, 'current_price', 0) - buy_price) / buy_price * 100 if holding else 0
        executed = cls._evaluate_tick_sell_conditions(ticker, holding, state, pnl_pct, SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0), SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0), trade_state) if holding else False
        if not executed and tranche > 0:
            executed = cls._evaluate_tick_buy_conditions(ticker, tranche, state, holding, pnl_pct, SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0), trade_state, low_1h, SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0))
        return executed

    @classmethod
    def _run_tick_trade(cls, user_id: str, holdings: list, kr_total: float, us_total_krw: float, cash_balance: float) -> bool:
        """Single-stock daily tick trade (entry/exit/hold)."""
        if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1: return False
        ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
        if not ticker: return False
        MarketDataService.register_ticker(ticker)
        state = MarketDataService.get_state(ticker)
        if not state or getattr(state, 'current_price', 0) <= 0: return False
        current_price = getattr(state, 'current_price', 0)
        allow_ext = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if (is_kr(ticker) and not MarketHourService.is_kr_market_open(allow_extended=allow_ext)) or \
           (not is_kr(ticker) and not MarketHourService.is_us_market_open(allow_extended=allow_ext)): return False

        tick_state = cls._load_state(user_id)
        user_state = tick_state.setdefault(user_id, {})
        today = datetime.now().strftime("%Y-%m-%d")
        trade_state = cls._get_or_reset_tick_state(user_state, today)
        _, low_1h = cls._update_price_window(trade_state, current_price)
        holding = next((h for h in holdings if h["ticker"] == ticker), None)
        if holding and cls._is_near_market_close(ticker, SettingsService.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5)):
            return cls._execute_tick_eod_sell(ticker, holding, holdings, current_price, trade_state, user_state, tick_state)
        market_total = kr_total if is_kr(ticker) else us_total_krw
        executed = cls._run_tick_intraday(ticker, state, holding, holdings, market_total, cash_balance, trade_state, low_1h)
        user_state["tick_trade"] = trade_state
        cls._save_state(tick_state)
        return executed

    # ── Universe Management ─────────────────────────────────────────────────────────

    @classmethod
    def _update_target_universe(cls, user_id: str) -> set:
        """Detect Top 100 changes and clean up universe."""
        def _norm_ticker(t: str) -> str:
            t = str(t or "").strip().upper()
            if not t: return ""
            if t.isdigit() and len(t) < 6: t = t.zfill(6)
            return t

        kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
        us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
        portfolio = PortfolioService.load_portfolio(user_id)
        holdings = [_norm_ticker(h.get('ticker')) for h in portfolio]

        kr_holdings = [t for t in holdings if t and is_kr(t) and len(t) == 6]
        us_holdings = [t for t in holdings if t and t.isalpha()]

        all_kr = list(set([t for t in kr_tickers if t and is_kr(t) and len(t) == 6] + kr_holdings))
        all_us = list(set([t for t in us_tickers if t and t.isalpha()] + us_holdings))
        target_universe = set(all_kr + all_us)

        MarketDataService.prune_states(target_universe)
        logger.info(f"Top 100 change detected: universe {len(target_universe)} (KR={len(all_kr)}, US={len(all_us)})")
        return target_universe

    # ── Portfolio Report ─────────────────────────────────────────────────────

    @classmethod
    def _send_portfolio_report(cls, user_id: str, before_snapshot: dict) -> None:
        """Compare pre/post-trade balances and send report for changed positions only."""
        try:
            from services.notification.report_service import ReportService
            PortfolioService.sync_with_kis(user_id)
            latest_holdings = PortfolioService.load_portfolio(user_id)
            summary = PortfolioService.get_last_balance_summary()
            latest_cash = PortfolioService.load_cash(user_id)

            after_snapshot = {h["ticker"]: h.get("quantity", 0) for h in latest_holdings}
            if before_snapshot == after_snapshot:
                logger.info("No position changes. Skipping trade report.")
                return

            # Filter changed positions only (new buy, qty change, full sell)
            changed_tickers = set()
            all_tickers = set(before_snapshot.keys()) | set(after_snapshot.keys())
            for ticker in all_tickers:
                before_qty = before_snapshot.get(ticker, 0)
                after_qty = after_snapshot.get(ticker, 0)
                if before_qty != after_qty:
                    changed_tickers.add(ticker)

            changed_holdings = [h for h in latest_holdings if h["ticker"] in changed_tickers]
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
    def _init_strategy_user_state(cls, state: dict, user_id: str) -> dict:
        """Initialize (or restore) user_id section in state dict and return it."""
        user_state = state.setdefault(user_id, {})
        if 'panic_locks' not in user_state:
            user_state['panic_locks'] = {}
        user_state.setdefault('split_orders', {})
        return user_state

    @classmethod
    def _run_signals_and_tick(
        cls, user_id: str, holdings: list, macro_data: dict, user_state: dict,
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float,
    ) -> bool:
        """Perform signal collection + execution + tick trading and return whether trades were executed."""
        prepared_signals = SignalService._collect_trading_signals(
            holdings, macro_data, user_state, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us
        )
        trade_executed = PositionService._execute_collected_signals(
            user_id, prepared_signals, holdings, kr_total, us_total_krw, cash_balance,
            target_cash_kr, target_cash_us, macro_data, user_state
        )
        try:
            tick_executed = cls._run_tick_trade(user_id, holdings, kr_total, us_total_krw, cash_balance)
            trade_executed = trade_executed or bool(tick_executed)
        except Exception as e:
            logger.warning(f"⚠️ Tick trading process error: {e}")
        return trade_executed

    @classmethod
    def run_strategy(cls, user_id: str = "sean") -> None:
        """Full strategy execution loop."""
        if not cls.is_enabled():
            logger.debug(f"⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return

        logger.info(f"🚀 Running Trading Strategy for {user_id}...")
        cls._update_target_universe(user_id)

        holdings = PortfolioService.sync_with_kis(user_id)
        before_snapshot = {h["ticker"]: h.get("quantity", 0) for h in holdings}
        macro_data = MacroService.get_macro_data()
        cash_balance = PortfolioService.load_cash(user_id)

        state = cls._load_state(user_id)
        user_state = cls._init_strategy_user_state(state, user_id)
        kr_total, us_total_krw, target_cash_kr, target_cash_us = TradeExecutorService._calculate_total_assets(holdings, cash_balance, macro_data)

        exchange_rate = MacroService.get_exchange_rate()
        usd_cash = PortfolioService.get_usd_cash_balance()
        cls._log_intramarket_cash_ratio(holdings, cash_balance, usd_cash, exchange_rate, target_cash_kr, target_cash_us)

        trade_executed = cls._run_signals_and_tick(user_id, holdings, macro_data, user_state, kr_total, us_total_krw, cash_balance, target_cash_kr, target_cash_us)
        cls._save_state(state)
        logger.info("Strategy execution and trade decisions complete.")
        if trade_executed:
            cls._send_portfolio_report(user_id, before_snapshot)

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
        macro_data = MacroService.get_macro_data()

        state = cls._load_state(user_id)
        user_state = state.get(user_id, {})

        cash_balance = PortfolioService.load_cash(user_id)
        kr_total, us_total_krw, _, _ = TradeExecutorService._calculate_total_assets(holdings, cash_balance, macro_data)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        holdings_map = {h['ticker']: h for h in holdings}
        waiting_list = []
        for ticker, ticker_state in all_state_items:
            holding = holdings_map.get(ticker)
            market_total = kr_total if is_kr(ticker) else us_total_krw
            score, reasons = SignalService.calculate_score(ticker, ticker_state, holding, macro_data, user_state, cash_balance, market_total_krw=market_total)
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
        holding = next((h for h in holdings if h['ticker'] == ticker), None)

        if not holding:
            return {"status": "failed", "msg": "Not a held stock."}

        max_qty = (getattr(holding, "quantity", None) if not isinstance(holding, dict) else holding.get("quantity", None))
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty

        logger.info(f"manual sell execution: {ticker} {quantity} qty")

        current_price = holding.get('current_price', 0)
        ok, err = OrderService.sell_single_holding(ticker, holding.get('name', ticker), quantity, current_price)
        if ok:
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=current_price,
                result_msg="Manual Sell Execution",
                strategy_name="manual",
                buy_price=holding.get('buy_price', None),
            )
            return {"status": "success", "msg": f"{ticker} {quantity} shares sold"}
        return {"status": "failed", "msg": err}
