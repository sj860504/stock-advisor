"""
PositionService: position management and trading signal execution.
- Profit-taking / add-buy / split-buy / sell handlers
- Single signal processing (_process_single_signal)
- Unmonitored holdings stop-loss/profit-taking check (_check_unmonitored_holdings)
- Batch execution of collected signals (_execute_collected_signals)
"""
from datetime import datetime
from typing import Optional

import pytz

from services.market.market_data_service import MarketDataService
from services.market.macro_service import MacroService
from services.config.settings_service import SettingsService
from services.strategy.execution_service_v2 import TradeExecutorService
from models.schemas import SplitOrderState, BuyCooldownEntry
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("position_service")


class PositionService:
    """Position management and trading signal execution."""

    # ── Profit-Taking ─────────────────────────────────────────────────────────────────

    @classmethod
    def _handle_profit_take_signal(
        cls, ticker: str, holding: dict, profit_pct: float, take_profit_pct: float,
        sell_cooldown: dict, today: str, state, score: int,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> bool:
        """Handle profit-taking condition. Applies cooldown. Returns execution status."""
        if not (holding and profit_pct >= take_profit_pct):
            return False
        if sell_cooldown.get(ticker) == today:
            logger.info(f"⏭️ {ticker} Partial sell cooldown active (already took profit today). Re-evaluate tomorrow.")
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"take_profit_zone({profit_pct:.2f}%)", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        sell_cooldown[ticker] = today  # Prevent same-day retry regardless of success/failure
        return bool(executed)

    # ── Cooldown ───────────────────────────────────────────────────────────────

    @classmethod
    def _is_buy_cooldown_active(cls, ticker: str, today: str, current_price: float, add_buy_cooldown: dict) -> bool:
        """Determine if cooldown is active. Includes backward compat for old format (str).
        Exception: allows same-day rebuy if price drops -5% or more from buy price.
        """
        cd = add_buy_cooldown.get(ticker)
        if not cd:
            return False
        if isinstance(cd, str):             # Legacy format: "YYYY-MM-DD"
            return cd == today
        if cd.date != today:                  # Different day -> cooldown expired
            return False
        buy_price = cd.price
        if buy_price > 0 and current_price <= buy_price * 0.95:  # -5% exception
            return False
        return True

    # ── Add-Buy ─────────────────────────────────────────────────────────────

    @classmethod
    def _handle_add_buy_signal(
        cls, ticker: str, holding: dict, profit_pct: float, stop_loss_pct: float,
        current_rsi: float, add_rsi_limit: float, add_score_limit: int, score: int,
        add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> bool:
        """Handle add-buy condition. Applies cooldown/RSI/score filters. Returns execution status."""
        if not (holding and profit_pct <= -5.0 and profit_pct > stop_loss_pct):
            return False
        if current_rsi >= add_rsi_limit:
            logger.info(f"⏭️ {ticker} Add-buy RSI overbought ({current_rsi:.1f} >= {add_rsi_limit}). Skip.")
            return False
        if score > add_score_limit:
            logger.info(f"⏭️ {ticker} Add-buy score not met ({score} > {add_score_limit}). Skip.")
            return False
        current_price_val = getattr(state, 'current_price', 0)
        if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
            logger.info(f"⏭️ {ticker} Add-buy cooldown active (already added today). Re-evaluate tomorrow.")
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "buy", f"add_position({profit_pct:.2f}%)", profit_pct, True, score,
            current_price_val, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        if executed:
            add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price_val)
        return bool(executed)

    # ── Split Buy ─────────────────────────────────────────────────────────────

    @classmethod
    def _init_split_order(cls, ticker, state, score, cash_balance, current_price, exchange_rate, market_total, today, split_orders) -> bool:
        """Initialize a new split order. Returns False if qty=0 or sector blocked."""
        sector = getattr(state, 'sector', '') or ''
        if sector in ('ETF', 'Others', 'Unclassified/ETF'):
            logger.info(f"⏭️ {ticker} ETF/Other sector new buy blocked (sector={sector}). Skip.")
            return False
        is_kr_flag = is_kr(ticker)
        usd_cash_krw = 0.0
        if not is_kr_flag:
            from services.trading.portfolio_service import PortfolioService as _PS
            usd_cash_krw = (_PS.get_usd_cash_balance() or 0) * exchange_rate
        total_qty, _, _ = TradeExecutorService._calculate_buy_quantity(score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total, usd_cash_krw=usd_cash_krw)
        if total_qty <= 0:
            logger.warning(f"⚠️ {ticker} Insufficient balance or qty 0. Cannot buy.")
            return False
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        split_orders[ticker] = SplitOrderState(
            total_qty=total_qty,
            remaining_qty=total_qty,
            split_count=split_count,
            start_date=today,
            entry_price=current_price,
        )
        return True

    @classmethod
    def _execute_split_tranche(cls, ticker, holding, score, reason_str, profit_pct, split_orders,
                               current_price_val, market_total, cash_balance, exchange_rate,
                               holdings, user_id, macro_data, target_cash_kr, target_cash_us,
                               add_buy_cooldown, today) -> bool:
        """Execute one tranche of a split order."""
        so = split_orders[ticker]
        remaining = so.remaining_qty
        splits_left = so.split_count - so.splits_done
        # Ceiling division to allocate more to earlier tranches: [2,2,1] pattern
        this_run_qty = -(-remaining // splits_left) if splits_left > 0 and remaining > 0 else remaining
        if this_run_qty <= 0:
            split_orders.pop(ticker, None)
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "buy",
            f"score {score} [{reason_str}] ({so.splits_done+1}/{so.split_count} split)",
            profit_pct, bool(holding), score, current_price_val, market_total, cash_balance,
            exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=this_run_qty,
        )
        if executed:
            so.splits_done += 1
            so.remaining_qty -= this_run_qty
            if so.remaining_qty <= 0:
                split_orders.pop(ticker, None)
            add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price_val)
        return bool(executed)

    @classmethod
    def _handle_buy_split(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        buy_max: int, add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
    ) -> bool:
        """New/split buy logic. Caller must ensure score/holding condition gates."""
        has_pending_splits = ticker in split_orders
        current_price_val = getattr(state, 'current_price', 0)
        if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
            logger.info(f"⏭️ {ticker} New buy cooldown active (already bought today). Re-evaluate tomorrow.")
            return False
        if not has_pending_splits:
            if not cls._init_split_order(ticker, state, score, cash_balance, current_price_val, exchange_rate, market_total, today, split_orders):
                return False
        return cls._execute_split_tranche(
            ticker, holding, score, reason_str, profit_pct, split_orders,
            current_price_val, market_total, cash_balance, exchange_rate,
            holdings, user_id, macro_data, target_cash_kr, target_cash_us,
            add_buy_cooldown, today,
        )

    # ── Score-Based Sell ────────────────────────────────────────────────────────

    @classmethod
    def _handle_sell_signal(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        sell_min: int, sell_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
    ) -> bool:
        """Score-based sell logic. Caller must ensure score/holding condition gates."""
        if sell_cooldown.get(ticker) == today:
            logger.info(f"⏭️ {ticker} Partial sell cooldown active (already score-sold today). Re-evaluate tomorrow.")
            return False
        split_orders.pop(ticker, None)  # Cancel remaining split orders
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"score {score} [{reason_str}]", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        sell_cooldown[ticker] = today  # Prevent same-day retry regardless of success/failure
        return bool(executed)

    # ── Score-Based Buy/Sell Combined ──────────────────────────────────────────────

    @classmethod
    def _handle_score_trade(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        buy_max: int, sell_min: int, sell_cooldown: dict, add_buy_cooldown: dict,
        today: str, state, market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict = None,
    ) -> bool:
        """Score-based buy/sell handling - delegates to sub-handlers."""
        if split_orders is None:
            split_orders = {}
        has_pending_splits = ticker in split_orders
        if 0 < score <= buy_max and (not holding or has_pending_splits):  # score=0 excluded from buy
            return cls._handle_buy_split(
                ticker, holding, score, reason_str, profit_pct,
                buy_max, add_buy_cooldown, today, state,
                market_total, cash_balance, exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders,
            )
        if score >= sell_min and holding:
            return cls._handle_sell_signal(
                ticker, holding, score, reason_str, profit_pct,
                sell_min, sell_cooldown, today, state,
                market_total, cash_balance, exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders,
            )
        return False

    # ── Single Signal Processing ────────────────────────────────────────────────────────

    @classmethod
    def _process_single_signal(
        cls, sig: dict, buy_max: int, sell_min: int, take_profit_pct: float,
        stop_loss_pct: float, add_rsi_limit: float, add_score_limit: int,
        sell_cooldown: dict, add_buy_cooldown: dict, today: str,
        holdings: list, user_id: str, kr_total: float, us_total_krw: float, cash_balance: float,
        exchange_rate: float, macro_data: dict, target_cash_kr: float, target_cash_us: float,
        split_orders: dict = None,
    ) -> bool:
        """Process a single signal and return whether a trade was executed."""
        ticker, state, holding = sig['ticker'], sig['state'], sig['holding']
        score, reasons = sig['score'], sig['reasons']
        reason_str = ", ".join(reasons)
        stock_name = getattr(state, "name", "") or (holding.get("name") if holding else "")
        dcf_val = getattr(state, 'dcf_value', None)
        dcf_str = f", DCF={dcf_val:,.0f}" if dcf_val and dcf_val > 0 else ""
        logger.info(f"🔍 Evaluated {ticker} ({stock_name}): Score={score}, RSI={getattr(state, 'rsi', 0):.1f}{dcf_str}, Reasons=[{reason_str}]")
        profit_pct = 0.0
        if holding:
            buy_price = holding.get('buy_price', 0)
            ref_price = float(holding.get("current_price") or getattr(state, 'current_price', 0))
            if buy_price > 0: profit_pct = (ref_price - buy_price) / buy_price * 100
        market_total = kr_total if is_kr(ticker) else us_total_krw
        common_kwargs = dict(holdings=holdings, user_id=user_id, macro_data=macro_data, target_cash_kr=target_cash_kr, target_cash_us=target_cash_us)
        if cls._handle_profit_take_signal(ticker, holding, profit_pct, take_profit_pct, sell_cooldown, today, state, score, market_total, cash_balance, exchange_rate, **common_kwargs):
            return True
        current_rsi = getattr(state, 'rsi', 50.0)
        if cls._handle_add_buy_signal(ticker, holding, profit_pct, stop_loss_pct, current_rsi, add_rsi_limit, add_score_limit, score, add_buy_cooldown, today, state, market_total, cash_balance, exchange_rate, **common_kwargs):
            return True
        return cls._handle_score_trade(ticker, holding, score, reason_str, profit_pct, buy_max, sell_min, sell_cooldown, add_buy_cooldown, today, state, market_total, cash_balance, exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders=split_orders)

    # ── Unmonitored Holdings Check ─────────────────────────────────────────────

    @classmethod
    def _check_unmonitored_holdings(
        cls, prepared_signals: list, holdings: list, user_id: str,
        kr_total: float, us_total_krw: float, cash_balance: float, exchange_rate: float,
        macro_data: dict, target_cash_kr: float, target_cash_us: float,
        take_profit_pct: float, stop_loss_pct: float,
        sell_cooldown: dict, today: str,
    ) -> bool:
        """Stop-loss/profit-taking check for holdings outside monitoring universe (ETFs, etc.)."""
        monitored_tickers = {sig['ticker'] for sig in prepared_signals}
        trade_executed = False
        for h in holdings:
            ticker = h.get('ticker')
            if not ticker or ticker in monitored_tickers:
                continue
            qty = h.get('quantity', 0)
            if not qty or qty <= 0:
                continue
            buy_price = float(h.get('buy_price') or 0)
            cached_state = MarketDataService.get_state(ticker)
            cached_price = getattr(cached_state, 'current_price', 0) if cached_state else 0
            current_price = cached_price if cached_price > 0 else float(h.get('current_price') or 0)
            if buy_price <= 0 or current_price <= 0:
                continue
            profit_pct = (current_price - buy_price) / buy_price * 100
            logger.info(f"🔍 [Unmonitored holding] {ticker} ({h.get('name', '')}): PnL={profit_pct:.1f}%")
            market_total = kr_total if is_kr(ticker) else us_total_krw
            if profit_pct <= stop_loss_pct:
                executed = TradeExecutorService._execute_trade_v2(
                    ticker, "sell", f"stop_loss({profit_pct:.2f}%)", profit_pct, True, 0,
                    current_price, market_total, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=h, macro=macro_data,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                trade_executed = bool(executed) or trade_executed
            elif profit_pct >= take_profit_pct:
                if sell_cooldown.get(ticker) == today:
                    logger.info(f"⏭️ {ticker} Partial sell cooldown active (already took profit today). Re-evaluate tomorrow.")
                    continue
                executed = TradeExecutorService._execute_trade_v2(
                    ticker, "sell", f"take_profit_zone({profit_pct:.2f}%)", profit_pct, True, 0,
                    current_price, market_total, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=h, macro=macro_data,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                if executed:
                    sell_cooldown[ticker] = today
                trade_executed = bool(executed) or trade_executed
        return trade_executed

    # ── Batch Signal Execution ────────────────────────────────────────────────────────

    @classmethod
    def _execute_collected_signals(
        cls, user_id: str, prepared_signals: list, holdings: list,
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float, macro_data: dict,
        user_state: dict = None,
    ) -> bool:
        """Execute actual orders based on collected signals."""
        buy_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0)
        exchange_rate = MacroService.get_exchange_rate()
        sell_cooldown: dict = (user_state or {}).setdefault('sell_cooldown', {})
        add_buy_cooldown: dict = (user_state or {}).setdefault('add_buy_cooldown', {})
        split_orders: dict = (user_state or {}).setdefault('split_orders', {})
        stop_loss_pct = SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -8.0)
        add_rsi_limit = SettingsService.get_float("STRATEGY_ADD_BUY_RSI_LIMIT", 60.0)
        add_score_limit = SettingsService.get_int("STRATEGY_ADD_BUY_SCORE_LIMIT", 55)
        today: str = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d')
        trade_executed = False
        for sig in prepared_signals:
            trade_executed = cls._process_single_signal(
                sig, buy_max, sell_min, take_profit_pct, stop_loss_pct, add_rsi_limit,
                add_score_limit, sell_cooldown, add_buy_cooldown, today,
                holdings, user_id, kr_total, us_total_krw, cash_balance, exchange_rate,
                macro_data, target_cash_kr, target_cash_us, split_orders=split_orders,
            ) or trade_executed

        # Stop-loss/profit-taking check for holdings outside monitoring universe (ETFs, etc.)
        trade_executed = cls._check_unmonitored_holdings(
            prepared_signals, holdings, user_id, kr_total, us_total_krw, cash_balance,
            exchange_rate, macro_data, target_cash_kr, target_cash_us,
            take_profit_pct, stop_loss_pct, sell_cooldown, today,
        ) or trade_executed
        return trade_executed
