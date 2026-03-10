"""
TradeExecutorService: order execution, weight calculation, alerts and related logic.
- TradeContext dataclass
- Sector/market weight calculation helpers
- Cash ratio / entry condition checks
- Buy/sell order execution (_execute_trade_v2)
- Trade alerts (Slack)
"""
import json
from typing import Optional

from services.market.market_hour_service import MarketHourService
from services.market.market_data_service import MarketDataService
from services.market.stock_meta_service import StockMetaService
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from services.kis.kis_service import KisService
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us

logger = get_logger("execution_service")

TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class TradeExecutorService:
    """Order execution + weight/cash condition checks + alerts."""

    WEIGHTS = {
        'RSI_OVERSOLD': -20, 'RSI_OVERBOUGHT': +15,
        'DIP_BUY_5PCT': -15, 'SURGE_SELL_5PCT': +15,
        'SUPPORT_EMA': -10, 'RESISTANCE_EMA': +10,
        'ADD_POSITION_LOSS': -10, 'GOLDEN_CROSS_DROP': +15,
        'PANIC_MARKET_BUY': -30, 'PROFIT_TAKE_TARGET': +30,
        'BULL_MARKET_SECTOR': -15, 'CASH_PENALTY': +15,
        'DCF_UNDERVALUE_HIGH': -25,
        'DCF_UNDERVALUE_MID': -15,
        'DCF_UNDERVALUE_LOW': -10,
        'DCF_FAIR_VALUE': -5,
        'DCF_OVERVALUE_LOW': +10,
        'DCF_OVERVALUE_HIGH': +20,
    }

    SECTOR_GROUP_MAP: dict = {
        "tech": "tech", "value": "value", "financial": "financial", "other": "other",
        "Technology": "tech", "IT": "tech", "기술": "tech",
        "Information Technology": "tech",
        "Communication Services": "tech", "통신서비스": "tech",
        "Consumer Staples": "value", "Consumer Defensive": "value",
        "Healthcare": "value", "Health Care": "value", "헬스케어": "value",
        "Utilities": "value", "Energy": "value", "Industrials": "value",
        "Materials": "value", "Consumer Discretionary": "value",
        "Consumer Cyclical": "value", "Real Estate": "value",
        "Financials": "financial", "Financial": "financial",
        "Financial Services": "financial", "Insurance": "financial",
        "ETF": "other", "Others": "other",
    }
    SECTOR_TARGET_WEIGHT: dict = {"tech": 0.50, "value": 0.30, "financial": 0.20}
    SECTOR_REBAL_THRESHOLD: float = 0.05

    # ── Ticker Classification Helpers ────────────────────────────────────────────────────────

    @classmethod
    def _get_ticker_market(cls, ticker: str) -> str:
        return "KR" if is_kr(ticker) else "US"

    @classmethod
    def _get_ticker_sector(cls, ticker: str, holding: Optional[dict] = None) -> str:
        if holding and holding.get("sector"):
            return (getattr(holding, "sector", None) if not isinstance(holding, dict) else holding.get("sector", None))
        meta = StockMetaService.get_stock_meta(ticker)
        return meta.sector if meta and meta.sector else "Others"

    @classmethod
    def _get_sector_group(cls, ticker: str, holding: Optional[dict] = None) -> str:
        """Return sector group based on SECTOR_GROUP_MAP ('tech'/'value'/'financial'/'other')."""
        sector = cls._get_ticker_sector(ticker, holding)
        return cls.SECTOR_GROUP_MAP.get(sector, "other")

    # ── Holding Value ─────────────────────────────────────────────────────────────

    @classmethod
    def _get_holding_value(cls, holding: dict) -> float:
        price = holding.get("current_price") or holding.get("buy_price") or 0
        if price <= 0:
            state = MarketDataService.get_state(holding.get("ticker", ""))
            if state and state.current_price:
                price = state.current_price
        return max(0.0, float(price)) * float(holding.get("quantity", 0))

    # ── Sector Group Weights ────────────────────────────────────────────────────────

    @classmethod
    def _get_sector_target_weights(cls, market: str) -> dict:
        """Per-market sector target weights (Settings priority, otherwise defaults)."""
        prefix = f"SECTOR_TARGET_{market.upper()}_"
        return {
            grp: SettingsService.get_float(f"{prefix}{grp.upper()}", cls.SECTOR_TARGET_WEIGHT.get(grp, 0.0))
            for grp in ("tech", "value", "financial")
        }

    @classmethod
    def _compute_group_values(cls, holdings: list, exchange_rate: float) -> dict:
        """Loop through holdings and return sector group KRW valuation dict."""
        group_values: dict = {"tech": 0.0, "value": 0.0, "financial": 0.0, "other": 0.0}
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            val = cls._get_holding_value(h)
            if val <= 0:
                continue
            ticker = h.get("ticker", "")
            if not is_kr(ticker):
                val *= exchange_rate
            grp = cls._get_sector_group(ticker, h)
            group_values[grp] = group_values.get(grp, 0.0) + val
        return group_values

    @classmethod
    def _get_sector_group_weights(cls, holdings: list, exchange_rate: float = 1400.0, market: str = "all") -> dict:
        """Return current sector group weights within stock assets and deviation from targets.
        market: 'kr' | 'us' | 'all'
        """
        if market == "kr":
            filtered = [h for h in holdings if is_kr(h.get("ticker", ""))]
        elif market == "us":
            filtered = [h for h in holdings if not is_kr(h.get("ticker", ""))]
        else:
            filtered = holdings

        group_values: dict = {"tech": 0.0, "value": 0.0, "financial": 0.0, "other": 0.0}
        for h in filtered:
            if h.get("quantity", 0) <= 0:
                continue
            val = cls._get_holding_value(h)
            if val <= 0:
                continue
            ticker = h.get("ticker", "")
            if market == "all" and not is_kr(ticker):
                val *= exchange_rate
            grp = cls._get_sector_group(ticker, h)
            group_values[grp] = group_values.get(grp, 0.0) + val

        total = sum(group_values.values())
        target_weights = cls._get_sector_target_weights(market if market != "all" else "kr")
        weights: dict = {}
        for grp, val in group_values.items():
            w = val / total if total > 0 else 0.0
            target = target_weights.get(grp, cls.SECTOR_TARGET_WEIGHT.get(grp, 0.0))
            weights[grp] = {"value": round(val), "weight": round(w, 4),
                            "target": target, "dev": round(w - target, 4)}
        currency = "USD" if market == "us" else "KRW"
        return {"total": round(total), "currency": currency, "weights": weights}

    @classmethod
    def _classify_holdings_by_deviation(cls, holdings: list, weights: dict) -> tuple:
        """Classify holdings into underweight/overweight lists by sector deviation."""
        underweight, overweight = [], []
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            ticker = h.get("ticker", "")
            grp = cls._get_sector_group(ticker, h)
            if grp == "other":
                continue
            dev = weights.get(grp, {}).get("dev", 0.0)
            entry = {
                "ticker": ticker,
                "name": h.get("name", ""),
                "group": grp,
                "dev": round(dev, 4),
                "current_weight": weights.get(grp, {}).get("weight", 0),
                "target_weight": weights.get(grp, {}).get("target", 0),
            }
            if dev < -cls.SECTOR_REBAL_THRESHOLD:
                entry["action"] = "buy_priority"
                underweight.append(entry)
            elif dev > cls.SECTOR_REBAL_THRESHOLD:
                entry["action"] = "sell_consider"
                overweight.append(entry)
        return underweight, overweight

    # ── Allocation Limit Checks ────────────────────────────────────────────────────────

    @classmethod
    def _compute_sector_value_map(cls, holdings: list, ticker: str, add_value: float, sector: str, exchange_rate: float) -> dict:
        """Calculate per-sector valuation dict based on holdings (reflecting additional buy)."""
        sector_values: dict = {}
        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            value = cls._get_holding_value(h)
            if value <= 0:
                continue
            sec = cls._get_ticker_sector(h["ticker"], h)
            sector_values[sec] = sector_values.get(sec, 0.0) + value
        sector_values[sector] = sector_values.get(sector, 0.0) + add_value
        return sector_values

    @classmethod
    def _check_sector_group_limit(cls, ticker: str, holding, holdings: list, exchange_rate: float) -> list:
        """Return soft warning reasons list for sector group target weight (1 entry if exceeded)."""
        reasons = []
        grp = cls._get_sector_group(ticker, holding)
        if grp != "other":
            sw = cls._get_sector_group_weights(holdings, exchange_rate)
            grp_info = sw["weights"].get(grp, {})
            grp_weight = grp_info.get("weight", 0.0)
            grp_target = cls.SECTOR_TARGET_WEIGHT.get(grp, 0.0)
            if grp_weight > grp_target + cls.SECTOR_REBAL_THRESHOLD:
                reasons.append(f"sector_group_weight_exceeded({grp} {grp_weight:.1%} > target {grp_target:.1%})")
        return reasons

    @classmethod
    def _compute_market_balances(
        cls, holdings: list, cash_balance: float, exchange_rate: float,
        kr_assets: float, us_assets_krw: float, add_value: float, market: str
    ) -> tuple:
        """Calculate per-market (KR/US) valuations and cash balances."""
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)
        kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.get("quantity", 0) > 0)
        us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
        kr_cash = cash_balance if kr_assets <= 0 else kr_assets - kr_market_value
        if us_assets_krw <= 0:
            us_cash_krw = PortfolioService.get_usd_cash_balance() * exchange_rate
        else:
            us_cash_krw = us_assets_krw - us_market_value_krw
        if market == "KR":
            kr_market_value += add_value
        else:
            us_market_value_krw += add_value
        return kr_market_value, us_market_value_krw, kr_cash, us_cash_krw

    @classmethod
    def _passes_allocation_limits(cls, ticker: str, add_value: float, holdings: list, cash_balance: float, holding: Optional[dict] = None, kr_assets: float = 0.0, us_assets_krw: float = 0.0) -> tuple:
        """Check market/sector weight limits (KR/US separated)."""
        market = cls._get_ticker_market(ticker)
        market_total = kr_assets if market == 'KR' else us_assets_krw
        if market_total <= 0:
            return True, []
        exchange_rate = MacroService.get_exchange_rate()
        sector = cls._get_ticker_sector(ticker, holding)
        cls._compute_market_balances(holdings, cash_balance, exchange_rate, kr_assets, us_assets_krw, add_value, market)
        sector_values = cls._compute_sector_value_map(holdings, ticker, add_value, sector, exchange_rate)
        max_sector = SettingsService.get_float("STRATEGY_MAX_SECTOR_RATIO", 0.3)
        reasons = []
        if max_sector > 0:
            ratio = sector_values.get(sector, 0.0) / market_total
            if ratio > max_sector:
                reasons.append(f"sector_weight_exceeded({sector} {ratio:.2%} > {max_sector:.2%})")
        reasons.extend(cls._check_sector_group_limit(ticker, holding, holdings, exchange_rate))
        return len(reasons) == 0, reasons

    # ── Target Cash Ratio ────────────────────────────────────────────────────────

    @classmethod
    def _is_panic_market(cls, macro: dict) -> bool:
        vix = macro.get("vix", 20.0)
        fng = macro.get("fear_greed", 50)
        return vix >= 25 or fng <= 30

    @classmethod
    def _get_target_cash_ratio(cls, market: str, regime_status: str) -> float:
        """Get target cash ratio based on market regime (KR/US separated)."""
        regime_key = regime_status.upper()
        if regime_key not in ['BEAR', 'NEUTRAL', 'BULL']:
            regime_key = 'NEUTRAL'
        market_key = 'KR' if market == 'KR' else 'US'
        setting_key = f"STRATEGY_TARGET_CASH_RATIO_{market_key}_{regime_key}"
        default_ratios = {
            'KR': {'BEAR': 0.20, 'NEUTRAL': 0.40, 'BULL': 0.50},
            'US': {'BEAR': 0.20, 'NEUTRAL': 0.40, 'BULL': 0.50}
        }
        default = default_ratios.get(market_key, {}).get(regime_key, 0.40)
        return SettingsService.get_float(setting_key, default)

    @classmethod
    def _calculate_total_assets(cls, holdings: list, cash_balance: float, macro_data: dict) -> tuple:
        """Calculate per-market assets and regime-based cash ratio targets. Returns (kr_total, us_total_krw, target_cash_kr, target_cash_us)."""
        usd_cash = PortfolioService.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()

        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

        kr_market_value = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in kr_holdings)
        us_market_value_usd = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in us_holdings)
        us_market_value_krw = us_market_value_usd * exchange_rate
        usd_cash_krw = usd_cash * exchange_rate

        kr_total = kr_market_value + max(0.0, cash_balance)
        us_total_krw = us_market_value_krw + usd_cash_krw

        regime = macro_data.get('market_regime') if macro_data else None
        regime_status = getattr(regime, 'status', 'Neutral').upper()
        target_cash_kr = cls._get_target_cash_ratio('KR', regime_status)
        target_cash_us = cls._get_target_cash_ratio('US', regime_status)
        logger.info(f"💰 Market regime: {regime_status} → KR total: {kr_total:,.0f}KRW, US total: {us_total_krw:,.0f}KRW | KR cash ratio target: {target_cash_kr:.1%}, US cash ratio target: {target_cash_us:.1%}")

        return kr_total, us_total_krw, target_cash_kr, target_cash_us

    # ── Top Weight Overrides ─────────────────────────────────────────────────

    @classmethod
    def get_top_weight_overrides(cls) -> dict:
        """Get per-ticker user weight overrides."""
        raw = SettingsService.get_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", "{}")
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}

    @classmethod
    def set_top_weight_overrides(cls, overrides: dict) -> dict:
        """Save per-ticker user weight overrides."""
        value = overrides or {}
        SettingsService.set_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", json.dumps(value, ensure_ascii=False))
        return value

    # ── Market Hours ────────────────────────────────────────────────────────

    @classmethod
    def _check_market_hours(cls, ticker: str) -> bool:
        """Check market operating hours."""
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        return MarketHourService.is_kr_market_open(allow_extended=allow_extended) if is_kr(ticker) else MarketHourService.is_us_market_open(allow_extended=allow_extended)

    # ── Cash Ratio Conditions ────────────────────────────────────────────────────────

    @classmethod
    def _is_cash_ratio_sufficient(cls, ticker: str, holdings: list, cash_balance: float, exchange_rate: float, target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: dict) -> bool:
        """Check if target cash ratio condition is met."""
        is_kr_ticker = is_kr(ticker)
        regime = (macro or {}).get('market_regime')
        regime_status = getattr(regime, 'status', 'Neutral').upper()
        target_cash_ratio = target_cash_ratio_kr if is_kr_ticker else target_cash_ratio_us

        if target_cash_ratio is None:
            target_cash_ratio = cls._get_target_cash_ratio('KR' if is_kr_ticker else 'US', regime_status)

        if is_kr_ticker:
            kr_holdings = [h for h in filter_kr(holdings or []) if h.get("quantity", 0) > 0]
            kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings)
            kr_total = kr_market_value + cash_balance
            cash_ratio = cash_balance / kr_total if kr_total > 0 else 0
        else:
            us_holdings = [h for h in filter_us(holdings or []) if h.get("quantity", 0) > 0]
            us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.get("quantity", 0) > 0)
            usd_cash = PortfolioService.get_usd_cash_balance()
            us_cash_krw = usd_cash * exchange_rate
            us_total = us_market_value_krw + us_cash_krw
            cash_ratio = us_cash_krw / us_total if us_total > 0 else 0

        return cash_ratio <= target_cash_ratio and not cls._is_panic_market(macro or {})

    # ── Buy Quantity Calculation ────────────────────────────────────────────────────────

    @classmethod
    def _calculate_buy_quantity(cls, score: int, cash_balance: float, current_price: float, exchange_rate: float, is_kr_flag: bool, market_total_krw: float = 0.0, usd_cash_krw: float = 0.0) -> tuple:
        """Calculate total buy quantity and required capital (KRW) based on investment weight."""
        per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)

        base_assets = market_total_krw
        multiplier = 2.0 if score >= 90 else (1.5 if score >= 80 else 1.0)
        target_invest_krw = base_assets * per_trade_ratio * multiplier
        cash_limit = (usd_cash_krw if (not is_kr_flag and usd_cash_krw > 0) else cash_balance)
        actual_invest_krw = min(target_invest_krw, cash_limit)

        final_price = current_price if is_kr_flag else current_price * exchange_rate
        total_qty = int(actual_invest_krw // final_price) if final_price > 0 else 0

        if total_qty == 0 and cash_balance >= final_price:
            logger.info("💡 Small asset adjustment: increasing allocation to secure minimum qty (1 share)")
            total_qty = 1

        return total_qty, total_qty * final_price, final_price

    # ── Alerts ─────────────────────────────────────────────────────────────────

    @classmethod
    def _send_tick_alert(cls, ticker: str, side: str, current_price: float, qty: int, reason: str, pnl_pct: float = 0.0, holding: dict = None) -> None:
        """Tick trade execution alert (structured Slack message)."""
        meta = StockMetaService.get_stock_meta(ticker)
        name = (holding.get("name") if holding and holding.get("name")
                else (meta.name_ko or meta.name_en or "" if meta else ""))
        is_kr_flag = is_kr(ticker)
        price_str = f"{current_price:,.0f}KRW" if is_kr_flag else f"${current_price:,.2f}"
        if side == "buy":
            msg = f"🔵 *[BUY Executed - Tick]* • Ticker: {ticker} {name}, price: {price_str}, Qty: {qty} shares | Reason: {reason}"
        else:
            buy_price = float(holding.get("buy_price", 0)) if holding else 0
            profit_amt = (current_price - buy_price) * qty if buy_price else 0
            profit_amt_str = f"{profit_amt:+,.0f}KRW" if is_kr_flag else f"${profit_amt:+,.2f}"
            msg = f"🔴 *[SELL Executed - Tick]* • Ticker: {ticker} {name}, price: {price_str}, Qty: {qty} shares, • PnL: {pnl_pct:+.2f}%, Profit: {profit_amt_str} | Reason: {reason}"
        AlertService.send_slack_alert(msg)

    @classmethod
    def _send_trade_alert(cls, ticker: str, side: str, score: int, current_price: float, change_rate: float, trade_qty: int, profit_pct: float, holding: dict, executed: bool) -> None:
        if not executed:
            return
        meta = StockMetaService.get_stock_meta(ticker)
        name = (holding.get("name") if holding and holding.get("name")
                else (meta.name_ko or meta.name_en or "" if meta else ""))
        is_kr_flag = is_kr(ticker)
        currency = "KRW" if is_kr_flag else "USD"
        price_str = f"{current_price:,.0f}{currency}" if is_kr_flag else f"${current_price:,.2f}"

        if side == "buy":
            msg = f"🔵 *[BUY Executed]* • Ticker: {ticker} {name}, price: {price_str}, Qty: {trade_qty} shares | Change: {change_rate:+.2f}%, Score: {score}"
        else:
            buy_price = float(holding.get("buy_price", 0)) if holding else 0
            profit_amt = (current_price - buy_price) * trade_qty if buy_price else 0
            profit_amt_str = (f"{profit_amt:+,.0f}KRW" if is_kr_flag else f"${profit_amt:+,.2f}")
            msg = f"🔴 *[SELL Executed]* • Ticker: {ticker} {name}, price: {price_str}, Qty: {trade_qty} shares, • PnL: {profit_pct:+.2f}%, Profit: {profit_amt_str} | Change: {change_rate:+.2f}%, Score: {score}"
        
        # Add basic asset total to this message, maybe? Just log it for now
        AlertService.send_slack_alert(msg)

    # ── Order Execution Helpers ────────────────────────────────────────────────────────

    @classmethod
    def _check_buy_cash_and_entry_conditions(
        cls, ticker: str, cash_balance: float, is_holding: bool, profit_pct: float,
        holdings: list, exchange_rate: float,
        target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: dict,
    ) -> bool:
        """Check cash balance and entry conditions. Returns True if buy is allowed."""
        is_kr_flag = is_kr(ticker)
        if is_kr_flag and cash_balance <= 0:
            logger.info(f"⏭️ {ticker} KRW cash insufficient ({cash_balance:,.0f}KRW). Buy blocked.")
            return False
        if not is_kr_flag:
            _usd_cash = PortfolioService.get_usd_cash_balance()
            if _usd_cash <= 0:
                logger.info(f"⏭️ {ticker} USD cash insufficient (${_usd_cash:.2f}). Buy blocked.")
                return False
        if is_holding:
            add_position_below = SettingsService.get_float("STRATEGY_ADD_POSITION_BELOW", -5.0)
            if profit_pct > add_position_below:
                logger.info(f"⏭️ {ticker} Add-buy condition not met. Order skipped.")
                return False
        if cls._is_cash_ratio_sufficient(ticker, holdings, cash_balance, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro):
            logger.info(f"⏭️ {ticker} Cash ratio condition not met. Buy skipped.")
            return False
        return True

    @classmethod
    def _compute_buy_market_totals(cls, ticker: str, holdings: list, cash_balance: float, exchange_rate: float, user_id: str) -> tuple:
        """Calculate per-market totals (KRW) for buy. Returns (is_kr_flag, holdings, kr_assets, us_assets_krw, usd_cash_krw)."""
        holdings = holdings or PortfolioService.load_portfolio(user_id)
        is_kr_flag = is_kr(ticker)
        kr_market_value = sum(cls._get_holding_value(h) for h in filter_kr(holdings) if h.get("quantity", 0) > 0)
        us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in filter_us(holdings) if h.get("quantity", 0) > 0)
        usd_cash = PortfolioService.get_usd_cash_balance()
        usd_cash_krw = (usd_cash or 0) * exchange_rate
        kr_assets = kr_market_value + cash_balance
        us_assets_krw = us_market_value_krw + usd_cash_krw
        return is_kr_flag, holdings, kr_assets, us_assets_krw, usd_cash_krw

    @classmethod
    def _fetch_fresh_us_price(cls, ticker: str, fallback: float) -> float:
        """Refresh real-time price before US order. Returns fallback on failure."""
        try:
            from services.kis.fetch.kis_fetcher import KisFetcher
            token = KisService.get_access_token()
            fresh = KisFetcher.fetch_overseas_price(token, ticker)
            price = fresh.get("price", 0)
            if price > 0:
                logger.info(f"🔄 {ticker} Pre-order price refresh: ${price:.2f} (previous: ${fallback:.2f})")
                return price
        except Exception as e:
            logger.warning(f"⚠️ {ticker} Price refresh failed, using previous price: {e}")
        return fallback

    @classmethod
    def _execute_buy_order(
        cls, ticker: str, score: int, profit_pct: float, is_holding: bool,
        current_price: float, market_total: float, cash_balance: float,
        exchange_rate: float, holdings: list, user_id: str, holding: Optional[dict],
        macro: dict, target_cash_ratio_kr: float, target_cash_ratio_us: float,
        forced_qty: int = None,
    ) -> tuple:
        """Execute buy order (includes cash/weight condition checks). Returns (executed, trade_qty)."""
        if not cls._check_buy_cash_and_entry_conditions(
            ticker, cash_balance, is_holding, profit_pct,
            holdings, exchange_rate,
            target_cash_ratio_kr, target_cash_ratio_us, macro,
        ):
            return False, 0
        is_kr_flag, holdings, kr_assets, us_assets_krw, usd_cash_krw = cls._compute_buy_market_totals(ticker, holdings, cash_balance, exchange_rate, user_id)
        if forced_qty is not None:
            quantity = forced_qty
            final_price = current_price if is_kr_flag else current_price * exchange_rate
        else:
            market_total_krw = kr_assets if is_kr_flag else us_assets_krw
            quantity, _, final_price = cls._calculate_buy_quantity(score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw=market_total_krw, usd_cash_krw=usd_cash_krw)
        if quantity <= 0:
            logger.warning(f"⚠️ {ticker} Insufficient balance (required: {final_price:,.0f}KRW)")
            return False, 0
        logger.info(f"⚖️ {ticker} Split buy scheduled ({quantity} shares)")
        if not is_kr_flag:
            current_price = cls._fetch_fresh_us_price(ticker, current_price)
        excg_cd = StockMetaService.get_exchange_code(ticker) if not is_kr_flag else None
        order_result = KisService.send_order(ticker, quantity, 0, "buy") if is_kr_flag else KisService.send_overseas_order(ticker, quantity, round(float(current_price), 2), "buy", market=excg_cd)
        if order_result.get("status") == "success":
            OrderService.record_trade(ticker, "buy", quantity, final_price, "strategy_execution", "v3_strategy")
            return True, quantity
        logger.error(f"Order failed: {order_result}")
        return False, 0

    @classmethod
    def _execute_sell_order(
        cls, ticker: str, score: int, current_price: float, holdings: list, user_id: str,
    ) -> tuple:
        """Execute sell order. Returns (executed, trade_qty)."""
        portfolio = holdings or PortfolioService.load_portfolio(user_id)
        current_holding = next((h for h in portfolio if h["ticker"] == ticker), None)
        if not current_holding:
            return False, 0
        holding_qty = current_holding.get("quantity", 0)
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        sell_qty, msg = max(1, int(holding_qty / split_count)), "partial_sell(take_profit)"
        buy_price_val = float(current_holding.get("buy_price") or 0) or None
        if not is_kr(ticker):
            current_price = cls._fetch_fresh_us_price(ticker, current_price)
        excg_cd = StockMetaService.get_exchange_code(ticker) if not is_kr(ticker) else None
        order_result = KisService.send_order(ticker, sell_qty, 0, "sell") if is_kr(ticker) else KisService.send_overseas_order(ticker, sell_qty, round(float(current_price), 2), "sell", market=excg_cd)
        if order_result.get("status") == "success":
            OrderService.record_trade(ticker, "sell", sell_qty, current_price, msg, "v3_strategy", buy_price=buy_price_val)
            return True, sell_qty
        logger.error(f"Order failed: {order_result}")
        return False, 0

    # ── Main Order Execution ────────────────────────────────────────────────────────

    @classmethod
    def _execute_trade_v2(
        cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool,
        score: int, current_price: float, market_total: float, cash_balance: float,
        exchange_rate: float, holdings: list = None, user_id: str = "sean",
        holding: dict = None, macro: dict = None,
        target_cash_ratio_kr: float = None, target_cash_ratio_us: float = None,
        forced_qty: int = None
    ) -> bool:
        """Split buy/sell execution logic."""
        logger.info(f"📢 Signal [{side.upper()}] {ticker} - Reason: {reason}")
        if not cls._check_market_hours(ticker):
            logger.info(f"⏭️ {ticker} Market closed. Order skipped.")
            return False

        state = MarketDataService.get_state(ticker)
        change_rate = getattr(state, "change_rate", 0.0)

        if side == "buy":
            executed, trade_qty = cls._execute_buy_order(
                ticker, score, profit_pct, is_holding, current_price, market_total,
                cash_balance, exchange_rate, holdings, user_id, holding, macro,
                target_cash_ratio_kr, target_cash_ratio_us, forced_qty=forced_qty,
            )
        elif side == "sell":
            executed, trade_qty = cls._execute_sell_order(ticker, score, current_price, holdings, user_id)
        else:
            return False

        cls._send_trade_alert(ticker, side, score, current_price, change_rate, trade_qty, profit_pct, holding, executed)
        return executed
