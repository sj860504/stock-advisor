"""
TradeExecutorService: order execution, weight calculation, alerts and related logic.
- TradeContext dataclass
- Sector/market weight calculation helpers
- Cash ratio / entry condition checks
- Buy/sell order execution (_execute_trade_v2)
- Trade alerts (Slack)
"""
import json
from typing import Optional, List

from services.market.market_hour_service import MarketHourService
from services.market.market_data_service import MarketDataService
from services.market.stock_meta_service import StockMetaService
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from services.kis.kis_service import KisService
from repositories.trade_history_repo import TradeHistoryRepo
from models.schemas import TradeResult, MacroDataSnapshot, HoldingSchema
from utils.logger import get_logger
from utils.market import is_kr, filter_kr, filter_us

logger = get_logger("execution_service")

TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class TradeExecutorService:
    """Order execution + weight/cash condition checks + alerts."""

    # Tracks KRW spent by the last successful buy (reset before each signal processing).
    # Used by PositionService to update cash_balance within the execution loop.
    _last_buy_spent_krw: float = 0.0

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
        'DCF_UNAVAILABLE': +10,
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
    def _get_ticker_sector(cls, ticker: str, holding: Optional[HoldingSchema] = None) -> str:
        if holding and holding.sector:
            return holding.sector
        meta = StockMetaService.get_stock_meta(ticker)
        return meta.sector if meta and meta.sector else "Others"

    @classmethod
    def _get_sector_group(cls, ticker: str, holding: Optional[HoldingSchema] = None) -> str:
        """Return sector group based on SECTOR_GROUP_MAP ('tech'/'value'/'financial'/'other')."""
        sector = cls._get_ticker_sector(ticker, holding)
        return cls.SECTOR_GROUP_MAP.get(sector, "other")

    # ── Holding Value ─────────────────────────────────────────────────────────────

    @classmethod
    def _get_holding_value(cls, holding: HoldingSchema) -> float:
        price = holding.current_price or holding.buy_price or 0
        if price <= 0:
            state = MarketDataService.get_state(holding.ticker)
            if state and state.current_price:
                price = state.current_price
        return max(0.0, float(price)) * float(holding.quantity)

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
    def _compute_group_values(cls, holdings: List[HoldingSchema], exchange_rate: float) -> dict:
        """Loop through holdings and return sector group KRW valuation dict."""
        group_values: dict = {"tech": 0.0, "value": 0.0, "financial": 0.0, "other": 0.0}
        for h in holdings:
            if h.quantity <= 0:
                continue
            val = cls._get_holding_value(h)
            if val <= 0:
                continue
            ticker = h.ticker
            if not is_kr(ticker):
                val *= exchange_rate
            grp = cls._get_sector_group(ticker, h)
            group_values[grp] = group_values.get(grp, 0.0) + val
        return group_values

    @classmethod
    def _get_sector_group_weights(cls, holdings: List[HoldingSchema], exchange_rate: float = 1400.0, market: str = "all") -> dict:
        """Return current sector group weights within stock assets and deviation from targets.
        market: 'kr' | 'us' | 'all'
        """
        if market == "kr":
            filtered = [h for h in holdings if is_kr(h.ticker)]
        elif market == "us":
            filtered = [h for h in holdings if not is_kr(h.ticker)]
        else:
            filtered = holdings

        effective_rate = exchange_rate if market == "all" else 1.0
        group_values = cls._compute_group_values(filtered, effective_rate)

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
    def _classify_holdings_by_deviation(cls, holdings: List[HoldingSchema], weights: dict) -> tuple:
        """Classify holdings into underweight/overweight lists by sector deviation."""
        underweight, overweight = [], []
        for h in holdings:
            if h.quantity <= 0:
                continue
            ticker = h.ticker
            grp = cls._get_sector_group(ticker, h)
            if grp == "other":
                continue
            dev = weights.get(grp, {}).get("dev", 0.0)
            entry = {
                "ticker": ticker,
                "name": h.name or "",
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
    def _compute_sector_value_map(cls, holdings: List[HoldingSchema], ticker: str, add_value: float, sector: str, exchange_rate: float) -> dict:
        """Calculate per-sector valuation dict based on holdings (reflecting additional buy)."""
        sector_values: dict = {}
        for h in holdings:
            if h.quantity <= 0:
                continue
            value = cls._get_holding_value(h)
            if value <= 0:
                continue
            sec = cls._get_ticker_sector(h.ticker, h)
            sector_values[sec] = sector_values.get(sec, 0.0) + value
        sector_values[sector] = sector_values.get(sector, 0.0) + add_value
        return sector_values

    @classmethod
    def _check_sector_group_limit(cls, ticker: str, holding: Optional[HoldingSchema], holdings: List[HoldingSchema], exchange_rate: float) -> list:
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
        cls, holdings: List[HoldingSchema], cash_balance: float, exchange_rate: float,
        kr_assets: float, us_assets_krw: float, add_value: float, market: str
    ) -> tuple:
        """Calculate per-market (KR/US) valuations and cash balances."""
        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)
        kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings if h.quantity > 0)
        us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings if h.quantity > 0)
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
    def _passes_allocation_limits(cls, ticker: str, add_value: float, holdings: list, cash_balance: float, holding: Optional[HoldingSchema] = None, kr_assets: float = 0.0, us_assets_krw: float = 0.0) -> tuple:
        """Check market/sector weight limits (KR/US separated)."""
        market = cls._get_ticker_market(ticker)
        market_total = kr_assets if market == 'KR' else us_assets_krw
        if market_total <= 0:
            return True, []
        exchange_rate = MacroService.get_exchange_rate()
        sector = cls._get_ticker_sector(ticker, holding)
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
    def _is_panic_market(cls, macro: MacroDataSnapshot) -> bool:
        vix = macro.vix or 20.0
        fng = macro.fear_greed or 50
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
    def _calculate_total_assets(cls, holdings: List[HoldingSchema], cash_balance: float, macro_data: MacroDataSnapshot) -> tuple:
        """Calculate per-market assets and regime-based cash ratio targets. Returns (kr_total, us_total_krw, target_cash_kr, target_cash_us)."""
        usd_cash = PortfolioService.get_usd_cash_balance()
        exchange_rate = MacroService.get_exchange_rate()

        kr_holdings = filter_kr(holdings)
        us_holdings = filter_us(holdings)

        kr_market_value = sum((h.current_price or 0) * h.quantity for h in kr_holdings)
        us_market_value_usd = sum((h.current_price or 0) * h.quantity for h in us_holdings)
        us_market_value_krw = us_market_value_usd * exchange_rate
        usd_cash_krw = usd_cash * exchange_rate

        kr_total = kr_market_value + max(0.0, cash_balance)
        us_total_krw = us_market_value_krw + usd_cash_krw

        regime_status = (macro_data.market_regime.status if macro_data else 'Neutral').upper()
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
    def _is_cash_below_target(cls, ticker: str, holdings: List[HoldingSchema], cash_balance: float, exchange_rate: float, target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: MacroDataSnapshot) -> bool:
        """Return True if current cash ratio is at or below the target minimum — buy should be blocked."""
        is_kr_ticker = is_kr(ticker)
        regime_status = (macro.market_regime.status if macro else 'Neutral').upper()
        target_cash_ratio = target_cash_ratio_kr if is_kr_ticker else target_cash_ratio_us

        if target_cash_ratio is None:
            target_cash_ratio = cls._get_target_cash_ratio('KR' if is_kr_ticker else 'US', regime_status)

        # Subtract pending buy orders from available cash to prevent over-leveraging
        pending_orders = TradeHistoryRepo.get_pending_orders()

        if is_kr_ticker:
            pending_krw = sum((o.price or 0) * (o.quantity or 0) for o in pending_orders if o.order_type == 'buy' and is_kr(o.ticker))
            effective_cash = max(0.0, cash_balance - pending_krw)
            kr_holdings = [h for h in filter_kr(holdings or []) if h.quantity > 0]
            kr_market_value = sum(cls._get_holding_value(h) for h in kr_holdings)
            kr_total = kr_market_value + effective_cash
            cash_ratio = effective_cash / kr_total if kr_total > 0 else 0
            if pending_krw > 0:
                logger.debug(f"💰 [CashCheck] Pending KRW Buy: {pending_krw:,.0f}, Effective Cash: {effective_cash:,.0f}, Ratio: {cash_ratio:.2%}")
        else:
            pending_us_usd = sum((o.price or 0) * (o.quantity or 0) for o in pending_orders if o.order_type == 'buy' and not is_kr(o.ticker))
            usd_cash = PortfolioService.get_usd_cash_balance() or 0.0
            effective_usd = max(0.0, usd_cash - pending_us_usd)
            us_cash_krw = effective_usd * exchange_rate
            us_holdings = [h for h in filter_us(holdings or []) if h.quantity > 0]
            us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in us_holdings)
            us_total = us_market_value_krw + us_cash_krw
            cash_ratio = us_cash_krw / us_total if us_total > 0 else 0
            if pending_us_usd > 0:
                logger.debug(f"💵 [CashCheck] Pending US Buy: ${pending_us_usd:,.2f}, Effective USD: ${effective_usd:,.2f}, Ratio: {cash_ratio:.2%}")

        return cash_ratio <= target_cash_ratio and not cls._is_panic_market(macro)

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
    def _classify_reason(cls, reason: str) -> str:
        """내부 reason 문자열을 사용자 친화적 한글 라벨로 변환."""
        r = reason.lower()
        if "stop_loss" in r:
            return "손절"
        if "take_profit" in r:
            return "익절"
        if "trailing_stop" in r:
            return "트레일링스탑"
        if "asset_management" in r:
            return "에셋 확보"
        if "budget_buy" in r:
            return "예산 매수"
        if "add_position" in r:
            return "추매"
        if "split" in r:
            return "분할 매수"
        if "score" in r:
            return "점수기반"
        return reason or "전략"

    @classmethod
    def _send_trade_alert(
        cls, ticker: str, side: str, score: int, current_price: float,
        change_rate: float, trade_qty: int, profit_pct: float,
        holding: HoldingSchema, executed: bool, reason: str = "",
        market_total: float = 0.0, cash_balance: float = 0.0, exchange_rate: float = 1350.0,
    ) -> None:
        if not executed:
            return
        meta = StockMetaService.get_stock_meta(ticker)
        name = (holding.name if holding and holding.name
                else (meta.name_ko or meta.name_en or "" if meta else ""))
        is_kr_flag = is_kr(ticker)
        reason_label = cls._classify_reason(reason)

        if is_kr_flag:
            price_str = f"₩{current_price:,.0f}"
            asset_str = f"₩{market_total:,.0f}"
            cash_str = f"₩{cash_balance:,.0f}"
        else:
            price_str = f"${current_price:,.2f}"
            usd_total = market_total / exchange_rate if exchange_rate > 0 else 0
            usd_cash = PortfolioService.get_usd_cash_balance()
            asset_str = f"${usd_total:,.0f}"
            cash_str = f"${usd_cash:,.2f}"

        side_icon = "🔵" if side == "buy" else "🔴"
        side_tag = "[B]" if side == "buy" else "[S]"

        if side == "sell":
            buy_price = float(holding.buy_price or 0) if holding else 0
            profit_amt = (current_price - buy_price) * trade_qty if buy_price else 0
            if is_kr_flag:
                profit_str = f"₩{profit_amt:+,.0f} ({profit_pct:+.2f}%)"
            else:
                profit_str = f"${profit_amt:+,.2f} ({profit_pct:+.2f}%)"
            msg = (
                f"{side_icon} *{side_tag} {name}* ({price_str} × {trade_qty})"
                f" | Profit: {profit_str}"
                f" | {reason_label}"
                f" | 총자산: {asset_str} (여유: {cash_str})"
            )
        else:
            msg = (
                f"{side_icon} *{side_tag} {name}* ({price_str} × {trade_qty})"
                f" | {reason_label}"
                f" | 총자산: {asset_str} (여유: {cash_str})"
            )

        AlertService.send_slack_alert(msg)

    # ── Order Execution Helpers ────────────────────────────────────────────────────────

    @classmethod
    def _has_absolute_cash(cls, ticker: str, cash_balance: float) -> bool:
        """Return False if there is no cash available for the market."""
        if is_kr(ticker) and cash_balance <= 0:
            logger.info(f"⏭️ {ticker} KRW cash insufficient ({cash_balance:,.0f}KRW). Buy blocked.")
            return False
        if not is_kr(ticker):
            _usd_cash = PortfolioService.get_usd_cash_balance()
            if _usd_cash <= 0:
                logger.info(f"⏭️ {ticker} USD cash insufficient (${_usd_cash:.2f}). Buy blocked.")
                return False
        return True

    @classmethod
    def _passes_add_buy_entry(cls, ticker: str, is_holding: bool, profit_pct: float) -> bool:
        """Return False if add-buy profit condition is not met for existing positions."""
        if not is_holding:
            return True
        add_position_below = SettingsService.get_float("STRATEGY_ADD_POSITION_BELOW", -5.0)
        if profit_pct > add_position_below:
            logger.info(f"⏭️ {ticker} Add-buy condition not met. Order skipped.")
            return False
        return True

    @classmethod
    def _check_buy_cash_and_entry_conditions(
        cls, ticker: str, cash_balance: float, is_holding: bool, profit_pct: float,
        holdings: List[HoldingSchema], exchange_rate: float,
        target_cash_ratio_kr: float, target_cash_ratio_us: float, macro: MacroDataSnapshot,
    ) -> bool:
        """Check cash balance and entry conditions. Returns True if buy is allowed."""
        if not cls._has_absolute_cash(ticker, cash_balance):
            return False
        if not cls._passes_add_buy_entry(ticker, is_holding, profit_pct):
            return False
        if cls._is_cash_below_target(ticker, holdings, cash_balance, exchange_rate, target_cash_ratio_kr, target_cash_ratio_us, macro):
            logger.info(f"⏭️ {ticker} Cash below target ratio. Buy skipped.")
            return False
        return True

    @classmethod
    def _compute_buy_market_totals(cls, ticker: str, holdings: Optional[List[HoldingSchema]], cash_balance: float, exchange_rate: float, user_id: str) -> tuple:
        """Calculate per-market totals (KRW) for buy. Returns (is_kr_flag, holdings, kr_assets, us_assets_krw, usd_cash_krw)."""
        holdings = holdings or PortfolioService.load_portfolio(user_id)
        is_kr_flag = is_kr(ticker)
        kr_market_value = sum(cls._get_holding_value(h) for h in filter_kr(holdings) if h.quantity > 0)
        us_market_value_krw = sum(cls._get_holding_value(h) * exchange_rate for h in filter_us(holdings) if h.quantity > 0)
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
    def _refresh_us_price(cls, ticker: str, current_price: float) -> float:
        """Return refreshed price for US tickers, current_price for KR. Pure I/O."""
        if is_kr(ticker):
            return current_price
        return cls._fetch_fresh_us_price(ticker, current_price)

    @classmethod
    def _has_pending_order(cls, ticker: str, side: str) -> bool:
        """DB에 해당 종목/방향의 pending 주문이 있는지 확인."""
        return OrderService.has_pending_order(ticker, side)

    @classmethod
    def _place_and_record(
        cls, ticker: str, side: str, qty: int, price: float,
        reason: str, user_id: str, buy_price: Optional[float] = None,
    ) -> bool:
        """Send KIS order and record trade on success. Common for buy and sell.
        Skips if there is a pending (unfilled) order for the same ticker and side.
        Returns True if order succeeded."""
        if cls._has_pending_order(ticker, side):
            logger.info(f"⏸️ {ticker} {side.upper()} 스킵: 미체결 주문 대기 중")
            return False
        excg_cd = StockMetaService.get_exchange_code(ticker) if not is_kr(ticker) else None
        if is_kr(ticker):
            order_result = KisService.send_order(ticker, qty, 0, side)
        else:
            order_result = KisService.send_overseas_order(ticker, qty, round(float(price), 2), side, market=excg_cd)
        if order_result.get("status") == "success":
            OrderService.record_trade(ticker, side, qty, price, reason, "v3_strategy", buy_price=buy_price)
            return True
        logger.error(f"Order failed: {order_result}")
        return False

    @classmethod
    def _execute_buy_order(
        cls, ticker: str, score: int, profit_pct: float, is_holding: bool,
        current_price: float, market_total: float, cash_balance: float,
        exchange_rate: float, holdings: List[HoldingSchema], user_id: str, holding: Optional[HoldingSchema],
        macro: MacroDataSnapshot, target_cash_ratio_kr: float, target_cash_ratio_us: float,
        forced_qty: int = None, reason: str = "",
    ) -> TradeResult:
        """Execute buy order."""
        if not cls._check_buy_cash_and_entry_conditions(
            ticker, cash_balance, is_holding, profit_pct,
            holdings, exchange_rate,
            target_cash_ratio_kr, target_cash_ratio_us, macro,
        ):
            return TradeResult.no_op()
        is_kr_flag, holdings, kr_assets, us_assets_krw, usd_cash_krw = cls._compute_buy_market_totals(ticker, holdings, cash_balance, exchange_rate, user_id)
        if forced_qty is not None:
            quantity = forced_qty
            final_price = current_price if is_kr_flag else current_price * exchange_rate
        else:
            market_total_krw = kr_assets if is_kr_flag else us_assets_krw
            quantity, _, final_price = cls._calculate_buy_quantity(score, cash_balance, current_price, exchange_rate, is_kr_flag, market_total_krw=market_total_krw, usd_cash_krw=usd_cash_krw)
        if quantity <= 0:
            logger.warning(f"⚠️ {ticker} Insufficient balance (required: {final_price:,.0f}KRW)")
            return TradeResult.no_op()
        logger.info(f"⚖️ {ticker} Split buy scheduled ({quantity} shares)")
        current_price = cls._refresh_us_price(ticker, current_price)
        record_price = current_price if not is_kr_flag else final_price
        executed = cls._place_and_record(ticker, "buy", quantity, record_price, reason or "strategy_execution", user_id)
        if executed:
            spent_krw = quantity * final_price if is_kr_flag else 0.0
            spent_usd = quantity * current_price if not is_kr_flag else 0.0
            logger.info(f"💰 {ticker} Buy spent ≈ {spent_krw:,.0f}KRW / ${spent_usd:,.2f} (qty={quantity})")
            return TradeResult(executed=True, spent_krw=spent_krw, spent_usd=spent_usd)
        return TradeResult.no_op()

    @classmethod
    def _execute_sell_order(
        cls, ticker: str, score: int, current_price: float, holdings: Optional[List[HoldingSchema]], user_id: str,
        forced_qty: int = None, reason: str = "",
    ) -> tuple:
        """Execute sell order. Returns (executed, trade_qty)."""
        portfolio = holdings or PortfolioService.load_portfolio(user_id)
        current_holding = next((h for h in portfolio if h.ticker == ticker), None)
        if not current_holding:
            return False, 0
        holding_qty = current_holding.quantity
        if forced_qty is not None and forced_qty > 0:
            sell_qty = min(forced_qty, holding_qty)
        else:
            split_count = SettingsService.get_int("STRATEGY_SELL_SPLIT_COUNT", 5)
            sell_qty = max(1, int(holding_qty / split_count))
        msg = reason or ("forced_sell(full)" if (forced_qty is not None and forced_qty > 0) else "partial_sell(take_profit)")
        buy_price_val = float(current_holding.buy_price or 0) or None
        current_price = cls._refresh_us_price(ticker, current_price)
        executed = cls._place_and_record(ticker, "sell", sell_qty, current_price, msg, user_id, buy_price=buy_price_val)
        if executed:
            return True, sell_qty
        return False, 0

    # ── Main Order Execution ────────────────────────────────────────────────────────

    @classmethod
    def _get_change_rate(cls, ticker: str) -> float:
        """Return current change_rate from in-memory state. 0.0 if unavailable."""
        state = MarketDataService.get_state(ticker)
        return getattr(state, "change_rate", 0.0)

    @classmethod
    def _execute_trade_v2(
        cls, ticker: str, side: str, reason: str, profit_pct: float, is_holding: bool,
        score: int, current_price: float, market_total: float, cash_balance: float,
        exchange_rate: float, holdings: Optional[List[HoldingSchema]] = None, user_id: str = "sean",
        holding: Optional[HoldingSchema] = None, macro: Optional[MacroDataSnapshot] = None,
        target_cash_ratio_kr: float = None, target_cash_ratio_us: float = None,
        forced_qty: int = None
    ) -> TradeResult:
        """Split buy/sell execution logic."""
        logger.info(f"📢 Signal [{side.upper()}] {ticker} - Reason: {reason}")
        if not cls._check_market_hours(ticker):
            logger.info(f"⏭️ {ticker} Market closed. Order skipped.")
            return TradeResult.no_op()

        change_rate = cls._get_change_rate(ticker)

        if side == "buy":
            result = cls._execute_buy_order(
                ticker, score, profit_pct, is_holding, current_price, market_total,
                cash_balance, exchange_rate, holdings, user_id, holding, macro,
                target_cash_ratio_kr, target_cash_ratio_us, forced_qty=forced_qty,
                reason=reason,
            )
            trade_qty = int(result.spent_krw / current_price) if result.spent_krw else int(result.spent_usd / current_price) if result.spent_usd else 0
        elif side == "sell":
            executed, trade_qty = cls._execute_sell_order(ticker, score, current_price, holdings, user_id, forced_qty=forced_qty, reason=reason)
            if executed and trade_qty > 0:
                is_kr_flag = is_kr(ticker)
                sold_krw = trade_qty * current_price if is_kr_flag else 0.0
                sold_usd = trade_qty * current_price if not is_kr_flag else 0.0
                result = TradeResult(executed=True, spent_krw=sold_krw, spent_usd=sold_usd)
            else:
                result = TradeResult(executed=executed)
        else:
            return TradeResult.no_op()

        cls._send_trade_alert(
            ticker, side, score, current_price, change_rate, trade_qty, profit_pct,
            holding, result.executed, reason=reason,
            market_total=market_total, cash_balance=cash_balance, exchange_rate=exchange_rate,
        )
        return result
