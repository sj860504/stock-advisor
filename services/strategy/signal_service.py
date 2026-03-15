"""
SignalService: score calculation and signal collection.
- Stock score computation (RSI, DCF, technical, portfolio, market context, target price, bonuses)
- Trading signal collection (_collect_trading_signals)
- Individual stock analysis (analyze_ticker, _analyze_stock_v3)
"""
from datetime import datetime
from typing import Optional

from services.market.market_hour_service import MarketHourService
from services.market.market_data_service import MarketDataService
from services.market.data_service import DataService
from services.trading.portfolio_service import PortfolioService
from services.market.macro_service import MacroService
from services.config.settings_service import SettingsService
from services.strategy.execution_service_v2 import TradeExecutorService
from models.schemas import MacroDataSnapshot, SignalSchema, UserState
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("signal_service")

TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class SignalService:
    """Score calculation and trading signal collection."""

    _top10_cache = {"timestamp": 0, "tickers": set()}
    _cached_signals: list[SignalSchema] = []

    @classmethod
    def get_latest_signals(cls) -> list:
        """Return cached signals from last _collect_trading_signals() call.
        Used by AssetManagementService to avoid re-computation."""
        return cls._cached_signals

    # ── Top10 Market Cap Cache ───────────────────────────────────────────────────────

    @classmethod
    def _get_top10_market_cap_tickers(cls) -> set:
        """Return cached top 10 US/KR market cap tickers."""
        now = datetime.now().timestamp()
        if now - cls._top10_cache["timestamp"] < TOP10_CACHE_TTL_SEC:
            return cls._top10_cache["tickers"]

        try:
            kr_top = DataService.get_top_krx_tickers(limit=100)[:10]
            us_top = DataService.get_top_us_tickers(limit=100)[:10]
            top10 = set(kr_top + us_top)
        except Exception as e:
            logger.warning(f"⚠️ Failed to refresh top10 market cap tickers: {e}")
            top10 = cls._top10_cache["tickers"]

        cls._top10_cache = {"timestamp": now, "tickers": top10}
        return top10

    # ── Score Components ─────────────────────────────────────────────────────────

    @classmethod
    def _score_rsi(cls, rsi: float, oversold_rsi: float, overbought_rsi: float) -> tuple:
        """Calculate RSI score by range. Returns (delta, reasons)."""
        delta = 0
        reasons = []
        if rsi <= 30:
            rsi_score = -(20 - (rsi / 30) * 10)
            delta += int(rsi_score)
            reasons.append(f"RSI_extreme_oversold({rsi:.1f},{int(rsi_score)})")
        elif rsi < 50:
            rsi_score = -(10 - ((rsi - 30) / 20) * 10)
            if rsi_score <= -5:
                delta += int(rsi_score)
                reasons.append(f"RSI_oversold({rsi:.1f},{int(rsi_score)})")
        elif rsi <= 70:
            rsi_score = ((rsi - 50) / 20) * 10
            if rsi_score >= 5:
                delta += int(rsi_score)
                reasons.append(f"RSI_overbought({rsi:.1f},+{int(rsi_score)})")
        else:
            rsi_score = 10 + ((rsi - 70) / 30) * 10
            delta += int(rsi_score)
            reasons.append(f"RSI_extreme_overbought({rsi:.1f},+{int(rsi_score)})")
        return delta, reasons

    @classmethod
    def _score_dcf(cls, dcf_value: float, curr_price: float) -> tuple:
        """Calculate undervalue/overvalue score vs DCF. Returns (delta, reasons)."""
        if not (dcf_value and dcf_value > 0):
            WEIGHTS = TradeExecutorService.WEIGHTS
            w = WEIGHTS.get('DCF_UNAVAILABLE', 10)
            return w, [f"no_dcf_data(+{w})"]
        delta = 0
        reasons = []
        undervalue_pct = (dcf_value - curr_price) / curr_price * 100
        WEIGHTS = TradeExecutorService.WEIGHTS
        if undervalue_pct >= 20:
            delta += WEIGHTS['DCF_UNDERVALUE_HIGH']; reasons.append(f"DCF_high_undervalue({undervalue_pct:.1f}%)")
        elif undervalue_pct >= 10:
            delta += WEIGHTS['DCF_UNDERVALUE_MID']; reasons.append(f"DCF_mid_undervalue({undervalue_pct:.1f}%)")
        elif undervalue_pct >= 5:
            delta += WEIGHTS['DCF_UNDERVALUE_LOW']; reasons.append(f"DCF_undervalue({undervalue_pct:.1f}%)")
        elif undervalue_pct >= -5:
            delta += WEIGHTS['DCF_FAIR_VALUE']; reasons.append("DCF_fair_value")
        elif undervalue_pct >= -15:
            delta += WEIGHTS['DCF_OVERVALUE_LOW']; reasons.append(f"DCF_overvalue({-undervalue_pct:.1f}%)")
        else:
            delta += WEIGHTS['DCF_OVERVALUE_HIGH']; reasons.append(f"DCF_high_overvalue({-undervalue_pct:.1f}%)")
        return delta, reasons

    @classmethod
    def _score_technical(cls, state, curr_price: float, oversold_rsi: float, overbought_rsi: float, dip_buy_pct: float) -> tuple:
        """[A] RSI + sharp drop/surge + DCF + EMA200 -> (delta, reasons)"""
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        rsi_delta, rsi_reasons = cls._score_rsi(state.rsi, oversold_rsi, overbought_rsi)
        delta += rsi_delta; reasons.extend(rsi_reasons)

        change_rate = getattr(state, 'change_rate', 0)
        if change_rate <= dip_buy_pct:
            delta += WEIGHTS['DIP_BUY_5PCT']; reasons.append(f"sharp_drop({change_rate:.1f}%)")
        elif change_rate >= 5.0:
            delta += WEIGHTS['SURGE_SELL_5PCT']; reasons.append(f"sharp_surge({change_rate:.1f}%)")

        dcf_d, dcf_r = cls._score_dcf(state.dcf_value, curr_price)
        delta += dcf_d; reasons.extend(dcf_r)

        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 <= curr_price <= ema200 * 1.02):
            delta += WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200_support")
        return delta, reasons

    @classmethod
    def _score_portfolio(cls, holding, profit_pct: float, take_profit_pct: float, stop_loss_pct: float) -> tuple:
        """[B] Profit-taking / add-buy / stop-loss -> (delta, reasons, forced_sell)"""
        if not holding:
            return 0, [], False
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        if profit_pct >= take_profit_pct:
            delta += WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"take_profit_zone({profit_pct:.1f}%)")
        elif profit_pct <= -5.0 and profit_pct > stop_loss_pct:
            delta += WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"add_position_zone({profit_pct:.1f}%)")
        elif profit_pct <= stop_loss_pct:
            return 0, ["stop_loss_hit"], True  # forced_sell: score=100
        return delta, reasons, False

    @classmethod
    def _score_market_context(cls, macro: MacroDataSnapshot, regime: str) -> tuple:
        """[C] Fear/greed + bull/bear market -> (delta, reasons)"""
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        vix = macro.vix or 20.0
        fng = macro.fear_greed or 50
        if vix >= 25 or fng <= 30:
            delta += WEIGHTS['PANIC_MARKET_BUY']; reasons.append("extreme_fear_buy_opportunity")
        elif vix <= 15 or fng >= 70:
            delta += WEIGHTS['PROFIT_TAKE_TARGET'] // 2; reasons.append("market_overheated_partial_profit")
        if regime == 'BULL':
            delta += WEIGHTS['BULL_MARKET_SECTOR']; reasons.append("bull_market_advantage")
            delta += 10; reasons.append("bull_market_profit_take_nudge")
        elif regime == 'BEAR':
            reasons.append("bear_market_hold")  # 약세장 매도 억제: 점수 변화 없음
        return delta, reasons

    @classmethod
    def _score_target_prices(cls, state, curr_price: float) -> tuple:
        """[D] User-set target entry/sell price reached -> (delta, reasons)"""
        delta = 0
        reasons = []
        target_buy = getattr(state, 'target_buy_price', 0)
        target_sell = getattr(state, 'target_sell_price', 0)
        if target_buy > 0 and curr_price <= target_buy:
            delta -= 15; reasons.append(f"target_entry_price_hit(${target_buy})")
        if target_sell > 0 and curr_price >= target_sell:
            delta += 30; reasons.append(f"target_sell_price_hit(${target_sell})")
        return delta, reasons

    @classmethod
    def _score_bonuses(cls, ticker: str, holding, macro: MacroDataSnapshot, user_state: UserState) -> tuple:
        """[E-G] Top10 market cap / user weight / sector weight bonuses -> (delta, reasons)"""
        delta = 0
        reasons = []
        top10_bonus = SettingsService.get_int("STRATEGY_TOP10_BONUS", 10)
        if top10_bonus and ticker in cls._get_top10_market_cap_tickers():
            delta -= top10_bonus; reasons.append(f"top10_market_cap(-{top10_bonus})")

        overrides = TradeExecutorService.get_top_weight_overrides()
        if ticker in overrides:
            custom_bonus = int(overrides[ticker])
            if custom_bonus != 0:
                delta += custom_bonus; reasons.append(f"user_weight_override({custom_bonus:+d})")

        try:
            grp = TradeExecutorService._get_sector_group(ticker, holding)
            if grp != "other":
                exchange_rate_g = MacroService.get_exchange_rate()
                all_holdings = PortfolioService.load_portfolio(user_state.user_id)
                sw = TradeExecutorService._get_sector_group_weights(all_holdings, exchange_rate_g)
                dev = sw["weights"].get(grp, {}).get("dev", 0.0)
                if dev < -TradeExecutorService.SECTOR_REBAL_THRESHOLD:
                    delta -= 10; reasons.append(f"sector_underweight_buy_priority({grp} {dev:+.1%})")
                elif dev > TradeExecutorService.SECTOR_REBAL_THRESHOLD:
                    delta += 10; reasons.append(f"sector_overweight_sell_priority({grp} {dev:+.1%})")
        except Exception:
            pass
        return delta, reasons

    # ── Score Integration ─────────────────────────────────────────────────────────────

    @classmethod
    def _load_score_thresholds(cls) -> dict:
        """Load 6 score thresholds from SettingsService and return as dict."""
        return {
            "oversold_rsi":    SettingsService.get_float("STRATEGY_OVERSOLD_RSI", 30.0),
            "overbought_rsi":  SettingsService.get_float("STRATEGY_OVERBOUGHT_RSI", 70.0),
            "dip_buy_pct":     SettingsService.get_float("STRATEGY_DIP_BUY_PCT", -5.0),
            "take_profit_pct": SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0),
            "stop_loss_pct":   SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -8.0),
            "base_score":      SettingsService.get_int("STRATEGY_BASE_SCORE", 50),
        }

    @classmethod
    def _apply_score_components(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, profit_pct: float, curr_price: float, regime: str, thresholds: dict) -> tuple:
        """Accumulate [A]~[G] score components and return (score, reasons, forced_sell, breakdown)."""
        t = thresholds
        score = t["base_score"]
        reasons: list = []
        breakdown = {"base": t["base_score"]}

        d, r = cls._score_technical(state, curr_price, t["oversold_rsi"], t["overbought_rsi"], t["dip_buy_pct"])
        score += d; reasons.extend(r); breakdown["technical"] = d

        d, r, forced_sell = cls._score_portfolio(holding, profit_pct, t["take_profit_pct"], t["stop_loss_pct"])
        if forced_sell:
            return 100, r, True, {"base": t["base_score"], "forced_sell": True}
        score += d; reasons.extend(r); breakdown["portfolio"] = d

        d, r = cls._score_market_context(macro, regime); score += d; reasons.extend(r); breakdown["market_context"] = d
        d, r = cls._score_target_prices(state, curr_price); score += d; reasons.extend(r); breakdown["target_prices"] = d
        d, r = cls._score_bonuses(ticker, holding, macro, user_state); score += d; reasons.extend(r); breakdown["bonuses"] = d

        return score, reasons, False, breakdown

    @classmethod
    def _compute_holding_profit_pct(cls, holding, state) -> float:
        """Calculate holding's return (%). Returns 0.0 if not held."""
        if not holding:
            return 0.0
        buy_price = holding.buy_price
        ref_price = float(holding.current_price or 0)
        if ref_price <= 0:
            ref_price = getattr(state, "current_price", 0)
        return (ref_price - buy_price) / buy_price * 100 if buy_price and buy_price > 0 else 0.0

    @classmethod
    def calculate_score(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, cash_balance: float, market_cash_ratio: float = None, market_total_krw: float = 0.0) -> tuple:
        """Calculate individual stock investment score (integrates [A]~[G] helpers)."""
        curr_price = state.current_price
        if curr_price <= 0: return 0, ["no_price_data"], {}
        profit_pct = cls._compute_holding_profit_pct(holding, state)
        cash_ratio = cash_balance / market_total_krw if market_total_krw > 0 else 0
        panic_locks = user_state.panic_locks
        regime = (macro.market_regime.status if macro else 'Unknown').upper()
        if market_cash_ratio is None:
            market_cash_ratio = TradeExecutorService._get_target_cash_ratio('KR' if is_kr(ticker) else 'US', regime)
        target_cash_ratio = market_cash_ratio
        thresholds = cls._load_score_thresholds()
        if ticker in panic_locks:
            return (20, ["3day_recovery_wait"], {"panic_lock": True}) if state.rsi < thresholds["oversold_rsi"] else (50, ["panic_lock_zone"], {"panic_lock": True})
        score, reasons, forced_sell, breakdown = cls._apply_score_components(ticker, state, holding, macro, user_state, profit_pct, curr_price, regime, thresholds)
        if forced_sell:
            return 100, reasons, breakdown
        if cash_ratio < target_cash_ratio and score > 50:
            score += TradeExecutorService.WEIGHTS['CASH_PENALTY']; reasons.append("cash_shortage")
            breakdown["cash_penalty"] = TradeExecutorService.WEIGHTS['CASH_PENALTY']
        return max(0, min(100, score)), reasons, breakdown

    # ── Analysis Interface ───────────────────────────────────────────────────────

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """Public interface for external individual stock analysis."""
        score, reasons, breakdown = cls.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_total_krw=market_total_krw)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)

        recommendation = "WAIT"
        if score <= buy_threshold_max:
            recommendation = "BUY"
        elif score >= sell_threshold_min:
            recommendation = "SELL"

        return {
            "ticker": ticker,
            "score": score,
            "recommendation": recommendation,
            "reasons": reasons,
            "score_breakdown": breakdown,
            "current_price": state.current_price,
            "rsi": state.rsi,
            "dcf_value": getattr(state, 'dcf_value', None),
        }

    @classmethod
    def _dispatch_analyze_trade(cls, ticker: str, side: str, score: int, reason_str: str, state, profit_pct: float, market_total: float, cash_balance: float, exchange_rate: float, holdings: list, user_id: str, holding, macro: MacroDataSnapshot) -> None:
        """Delegate buy/sell _execute_trade_v2 calls from _analyze_stock_v3."""
        is_holding = bool(holding)
        TradeExecutorService._execute_trade_v2(
            ticker, side, f"score {score} [{reason_str}]", profit_pct, is_holding, score,
            state.current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro,
        )

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, market_total: float, cash_balance: float, exchange_rate: float, user_id: str = "sean") -> None:
        """Legacy internal analysis loop (uses refactored calculate_score)."""
        score, reasons, _breakdown = cls.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_total_krw=market_total)
        profit_pct = cls._compute_holding_profit_pct(holding, state)
        reason_str = ", ".join(reasons)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        port = PortfolioService.load_portfolio(user_id)

        if score <= buy_threshold_max and not holding:
            cls._dispatch_analyze_trade(ticker, "buy", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)
        elif score >= sell_threshold_min and holding:
            cls._dispatch_analyze_trade(ticker, "sell", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)

    # ── Signal Collection ─────────────────────────────────────────────────────────────

    @classmethod
    def _determine_analysis_markets(cls, allow_extended: bool) -> tuple[bool, bool]:
        """개장 여부 기반 분석 대상 시장 판단. 순수 판단, 부수효과 없음.
        Returns (analyze_kr: bool, analyze_us: bool)."""
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        analyze_kr = not is_us_open
        analyze_us = not is_kr_open or MarketHourService.is_us_strategy_window(allow_extended=allow_extended, lead_minutes=30)
        logger.info(f"📊 Market status: KR_open={is_kr_open}, US_open={is_us_open} → KR_analyze={analyze_kr}, US_analyze={analyze_us}")
        return analyze_kr, analyze_us

    @classmethod
    def _is_fear_market_exception(cls, macro: MacroDataSnapshot) -> bool:
        """공포장 예외: fear_greed < 20 AND Bear 레짐일 때 현금 게이트 스킵 허용.
        공포 정점에서 오히려 저가 매수 기회를 놓치지 않기 위한 예외."""
        fear_greed = macro.fear_greed or 50
        regime_status = macro.market_regime.status if macro.market_regime else "Neutral"
        return fear_greed < 20 and regime_status == "Bear"

    @classmethod
    def _apply_hard_gates(
        cls, ticker: str, ticker_state, holding,
        cash_balance: float, usd_cash: float, exchange_rate: float,
        kr_total: float, us_total_krw: float,
        target_cash_kr: float, target_cash_us: float,
        macro: MacroDataSnapshot = None,
    ) -> bool:
        """신규 매수 후보 하드게이트. True = 이 종목 시그널 수집 스킵.
        보유 종목은 즉시 False (게이트 없음)."""
        if holding:
            return False
        is_kr_ticker = is_kr(ticker)
        rsi_val = getattr(ticker_state, 'rsi', 50)
        rsi_buy_block = SettingsService.get_float("STRATEGY_RSI_BUY_BLOCK", 75.0)
        if rsi_val >= rsi_buy_block:
            logger.info(f"⛔ {ticker} Skip signal: RSI={rsi_val:.1f} >= {rsi_buy_block} (overbought buy block)")
            return True
        mkt_total = kr_total if is_kr_ticker else us_total_krw
        tgt_ratio = target_cash_kr if is_kr_ticker else target_cash_us
        available = cash_balance if is_kr_ticker else usd_cash * exchange_rate
        cur_ratio = available / mkt_total if mkt_total > 0 else 0
        if cur_ratio < tgt_ratio:
            if macro and cls._is_fear_market_exception(macro):
                logger.info(f"🚨 {ticker} Fear market exception: cash gate bypassed (fear_greed<20, Bear)")
                return False
            logger.info(f"⛔ {ticker} Skip signal: avail_cash={cur_ratio:.1%} < target={tgt_ratio:.1%} (cash shortage)")
            return True
        return False

    @classmethod
    def _collect_trading_signals(
        cls, holdings: list, macro_data: MacroDataSnapshot, user_state: UserState,
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float,
        usd_cash: float = 0.0, exchange_rate: float = 1350.0,
    ) -> list[SignalSchema]:
        """분석 시장 결정 → 하드게이트 → 스코어 계산 → 신호 수집."""
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        analyze_kr, analyze_us = cls._determine_analysis_markets(allow_extended)

        holdings_map = {h.ticker: h for h in holdings}
        prepared_signals: list[SignalSchema] = []

        for ticker, ticker_state in list(MarketDataService.get_all_states().items()):
            is_kr_ticker = is_kr(ticker)
            if (is_kr_ticker and not analyze_kr) or (not is_kr_ticker and not analyze_us):
                continue
            if not getattr(ticker_state, 'is_ready', False):
                continue
            holding = holdings_map.get(ticker)
            if cls._apply_hard_gates(ticker, ticker_state, holding, cash_balance, usd_cash, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us, macro=macro_data):
                continue
            market_total = kr_total if is_kr_ticker else us_total_krw
            market_cash_ratio = target_cash_kr if is_kr_ticker else target_cash_us
            score, reasons, _breakdown = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, cash_balance, market_cash_ratio=market_cash_ratio, market_total_krw=market_total)
            prepared_signals.append(SignalSchema(ticker=ticker, state=ticker_state, holding=holding, score=score, reasons=reasons))

        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks ready.")
        cls._cached_signals = prepared_signals
        return prepared_signals
