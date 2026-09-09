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
from services.strategy.uptrend_rules import (
    tanh_scaled, uptrend_stop_loss_pct, drop_confirmation_factor,
)
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("signal_service")

class SignalService:
    """Score calculation and trading signal collection."""

    _cached_signals: list[SignalSchema] = []

    @classmethod
    def get_latest_signals(cls) -> list:
        """Return cached signals from last _collect_trading_signals() call.
        Used by AssetManagementService to avoid re-computation."""
        return cls._cached_signals

    # ── Score Components ─────────────────────────────────────────────────────────

    @classmethod
    def _score_rsi(cls, rsi: float, oversold_rsi: float, overbought_rsi: float) -> tuple:
        """RSI deviation from 50, capped ±RSI_CAP (1 point per 1 RSI unit).
        Above 50 → positive (sell signal), below 50 → negative (buy signal)."""
        cap = SettingsService.get_int("STRATEGY_RSI_DEVIATION_CAP", 15)
        # D1: 선형 클램프(59% 가 캡 포화) → tanh 스케일. scale=0 이면 기존 선형.
        delta = tanh_scaled(rsi - 50, cap, SettingsService.get_float("STRATEGY_RSI_TANH_SCALE", 15.0))
        if delta == 0:
            return 0, []
        return delta, [f"RSI_deviation({rsi:.1f},{delta:+d})"]

    @classmethod
    def _score_dcf(cls, dcf_value: float, curr_price: float, confidence: float = 1.0) -> tuple:
        """DCF deviation %-based score, capped ±DCF_CAP.
        D1: tanh 스케일(포화 완화) × 소스 신뢰도 계수(override 1.0 … kis 0.4)."""
        if not (dcf_value and dcf_value > 0):
            WEIGHTS = TradeExecutorService.WEIGHTS
            w = WEIGHTS.get('DCF_UNAVAILABLE', 10)
            return w, [f"no_dcf_data(+{w})"]
        cap = SettingsService.get_int("STRATEGY_DCF_DEVIATION_CAP", 25)
        undervalue_pct = (dcf_value - curr_price) / curr_price * 100
        delta = tanh_scaled(-undervalue_pct, cap, SettingsService.get_float("STRATEGY_DCF_TANH_SCALE", 20.0))
        conf = 1.0
        if SettingsService.get_int("STRATEGY_DCF_CONFIDENCE_ENABLED", 1) == 1 and confidence is not None:
            conf = max(0.0, min(1.0, float(confidence)))
            delta = int(round(delta * conf))
        if delta == 0:
            return 0, []
        conf_str = f",conf{conf:.1f}" if conf < 1.0 else ""
        return delta, [f"DCF_deviation({undervalue_pct:+.1f}%,{delta:+d}{conf_str})"]

    @classmethod
    def _score_technical(cls, state, curr_price: float, oversold_rsi: float, overbought_rsi: float, dip_buy_pct: float) -> tuple:
        """[A] RSI + change_rate + DCF — all linear/proportional."""
        delta = 0
        reasons = []
        rsi_delta, rsi_reasons = cls._score_rsi(state.rsi, oversold_rsi, overbought_rsi)
        delta += rsi_delta; reasons.extend(rsi_reasons)

        # Day's change_rate %-based: 1% = 3 points, capped ±CHANGE_CAP
        change_rate = getattr(state, 'change_rate', 0) or 0
        cap = SettingsService.get_int("STRATEGY_CHANGE_DEVIATION_CAP", 15)
        change_delta = max(-cap, min(cap, int(change_rate * 3)))
        # D2: 급락(음수) 가산은 저가 대비 반등 또는 거래량 동반 시에만 전액 (falling knife 완화)
        if change_delta < 0:
            factor = drop_confirmation_factor(
                change_rate, curr_price, getattr(state, 'low_price', 0) or 0,
                getattr(state, 'volume', 0) or 0, getattr(state, 'avg_volume_20d', 0) or 0,
            )
            if factor < 1.0:
                change_delta = int(round(change_delta * factor))
                reasons.append(f"drop_unconfirmed(x{factor:.1f})")
        if change_delta != 0:
            delta += change_delta
            reasons.append(f"change_deviation({change_rate:+.1f}%,{change_delta:+d})")

        dcf_d, dcf_r = cls._score_dcf(state.dcf_value, curr_price, getattr(state, 'dcf_confidence', 1.0))
        delta += dcf_d; reasons.extend(dcf_r)
        return delta, reasons

    @classmethod
    def _score_portfolio(cls, holding, profit_pct: float, take_profit_pct: float, stop_loss_pct: float,
                         atr_pct: float = None) -> tuple:
        """[B] Profit-taking / add-buy / stop-loss -> (delta, reasons, forced_sell).
        atr_pct: Uptrend 안전망 손절을 ATR 기반으로 산출 (S4). None 이면 고정 -25%."""
        if not holding:
            return 0, [], False
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        if profit_pct >= take_profit_pct:
            delta += WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"take_profit_zone({profit_pct:.1f}%)")
        elif profit_pct <= -5.0 and profit_pct > stop_loss_pct:
            delta += WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"add_position_zone({profit_pct:.1f}%)")
        elif stop_loss_pct < 0 and profit_pct <= stop_loss_pct:
            # Uptrend DCA 활성 시 레거시 손절 임계(-5~-8%) 는 추매 신호이지 강제매도 X.
            # 단, 극단 손실(STRATEGY_UPTREND_STOP_LOSS_PCT, 기본 -25%) 은 안전망 손절
            # → forced_sell (실행은 UPTREND_STOP_LOSS_DAYS 연속 + Crash 보류 규칙 적용).
            if SettingsService.get_int("STRATEGY_UPTREND_DCA_ENABLED", 1) == 1:
                uptrend_sl = uptrend_stop_loss_pct(atr_pct)
                if uptrend_sl < 0 and profit_pct <= uptrend_sl:
                    return 0, [f"uptrend_stop_loss_hit({profit_pct:.1f}%<={uptrend_sl:.0f}%)"], True
                delta += WEIGHTS['ADD_POSITION_LOSS']
                reasons.append(f"uptrend_deep_loss({profit_pct:.1f}%)")
                return delta, reasons, False
            return 0, ["stop_loss_hit"], True  # forced_sell: score=100
        return delta, reasons, False

    @classmethod
    def _score_market_context(cls, macro: MacroDataSnapshot, regime: str, ticker: Optional[str] = None) -> tuple:
        """[C] VIX, F&G, regime_score — all linear/proportional from baselines.
        High VIX / low F&G / low regime_score → buy signal (negative delta).
        Low VIX / high F&G / high regime_score → sell signal (positive delta).
        ticker 가 주어지면 지수 5d 보너스는 종목 시장 지수(KR→KOSPI, US→SPX) 기준."""
        delta = 0
        reasons = []

        # VIX baseline 20, 1 unit = 1 point, capped ±VIX_CAP (high VIX = fear = buy)
        vix_cap = SettingsService.get_int("STRATEGY_VIX_DEVIATION_CAP", 10)
        vix = macro.vix or 20.0
        vix_delta = max(-vix_cap, min(vix_cap, int(-(vix - 20))))
        if vix_delta != 0:
            delta += vix_delta
            reasons.append(f"VIX_deviation({vix:.1f},{vix_delta:+d})")

        # F&G baseline 50, every 5 units = 1 point, capped ±FNG_CAP (high F&G = greed = sell)
        fng_cap = SettingsService.get_int("STRATEGY_FNG_DEVIATION_CAP", 10)
        fng = macro.fear_greed if macro.fear_greed is not None else 50
        fng_delta = max(-fng_cap, min(fng_cap, int((fng - 50) / 5)))
        if fng_delta != 0:
            delta += fng_delta
            reasons.append(f"FNG_deviation({fng},{fng_delta:+d})")

        # Regime score baseline 50, every 3 units = 1 point, capped ±REGIME_CAP
        # 역추세(contrarian): 강세장(높은 regime)=과열 위험 → +score(매도) /
        #                     약세장(낮은 regime)=공포 저점 → -score(매수)
        regime_score = None
        if macro.market_regime:
            regime_score = getattr(macro.market_regime, 'regime_score', None)
        # Guard: regime_score must be a valid 0~100 (negative/None = sentinel for "Unknown")
        if regime_score is not None and 0 <= regime_score <= 100:
            reg_cap = SettingsService.get_int("STRATEGY_REGIME_DEVIATION_CAP", 10)
            reg_delta = max(-reg_cap, min(reg_cap, int((regime_score - 50) / 3)))
            if reg_delta != 0:
                delta += reg_delta
                reasons.append(f"Regime_deviation({regime_score},{reg_delta:+d})")

        # [Uptrend DCA] 지수 5d 변화율 보너스 — 폭락 단계별 BUY 가산.
        # KR 종목=KOSPI, US 종목=SPX. tier1: 0 / tier2: -5 / tier3: -10 / tier4: -15
        if SettingsService.get_int("STRATEGY_UPTREND_DCA_ENABLED", 1) == 1:
            from services.strategy.crash_guard_service import CrashGuardService
            idx_boost = CrashGuardService.score_boost(macro, ticker)
            if idx_boost != 0:
                delta += idx_boost
                tier = CrashGuardService.index_5d_tier(macro, ticker)
                chg_5d = CrashGuardService.index_5d_change(macro, ticker)
                label = CrashGuardService.index_label(ticker)
                reasons.append(f"{label}_5d_boost(t{tier},{chg_5d:+.1f}%,{idx_boost:+d})")
        return delta, reasons

    @classmethod
    def _score_target_prices(cls, state, curr_price: float) -> tuple:
        """[D] EMA200 deviation %-based score (1% per point, capped ±EMA_CAP)."""
        ema200 = state.ema.get(200) if state.ema else None
        if not ema200 or ema200 <= 0:
            return 0, []
        cap = SettingsService.get_int("STRATEGY_EMA200_DEVIATION_CAP", 15)
        deviation_pct = (curr_price - ema200) / ema200 * 100
        delta = max(-cap, min(cap, int(deviation_pct)))
        if delta == 0:
            return 0, []
        return delta, [f"EMA200_deviation({deviation_pct:+.1f}%,{delta:+d})"]

    @classmethod
    def _score_bonuses(cls, ticker: str, holding, macro: MacroDataSnapshot, user_state: UserState) -> tuple:
        """[F] User weight override만 잔존. top10 시총 보너스 / 섹터 보정 모두 제거."""
        delta = 0
        reasons = []
        overrides = TradeExecutorService.get_top_weight_overrides()
        if ticker in overrides:
            custom_bonus = int(overrides[ticker])
            if custom_bonus != 0:
                delta += custom_bonus; reasons.append(f"user_weight_override({custom_bonus:+d})")
        return delta, reasons

    # ── Score Integration ─────────────────────────────────────────────────────────────

    @classmethod
    def _get_take_profit_pct_by_regime(cls, regime: str) -> float:
        """레짐별 익절 기준 반환. BULL 7%, NEUTRAL 5%, BEAR 3%."""
        if regime == "BULL":
            return SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT_BULL", 7.0)
        elif regime == "BEAR":
            return SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT_BEAR", 3.0)
        return SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT_NEUTRAL", 5.0)

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

        # ticker별 mode×market×regime 동적 take_profit/stop_loss 사용 (portfolio scoring 한정)
        from services.strategy.position_service import PositionService
        tp_dyn = PositionService._get_take_profit_pct(ticker, macro)
        sl_dyn = PositionService._get_stop_loss_pct(ticker, macro)
        d, r, forced_sell = cls._score_portfolio(holding, profit_pct, tp_dyn, sl_dyn, atr_pct=getattr(state, 'atr_pct', None))
        if forced_sell:
            return 100, r, True, {"base": t["base_score"], "forced_sell": True}
        score += d; reasons.extend(r); breakdown["portfolio"] = d

        d, r = cls._score_target_prices(state, curr_price); score += d; reasons.extend(r); breakdown["target_prices"] = d
        d, r = cls._score_bonuses(ticker, holding, macro, user_state); score += d; reasons.extend(r); breakdown["bonuses"] = d

        # D1: 종목 점수(stock_score)와 시장 조정치(market_adj) 분리. 시장 컴포넌트는 모든 종목에
        # 동일하게 더해지는 상수이므로 사실상 '임계선 이동'이다. 최종 score = stock + market_adj (기존 의미 유지),
        # 두 값을 breakdown 으로 노출해 UI/튜닝에서 분리 관찰 가능.
        stock_score = score
        m_delta, m_reasons = cls._score_market_context(macro, regime, ticker)
        adj_cap = SettingsService.get_int("STRATEGY_MARKET_ADJ_CAP", 20)
        market_adj = max(-adj_cap, min(adj_cap, m_delta))
        if market_adj != m_delta:
            m_reasons.append(f"market_adj_capped({m_delta:+d}->{market_adj:+d})")
        reasons.extend(m_reasons)
        breakdown["market_context"] = market_adj
        breakdown["stock_score"] = max(1, min(100, stock_score))
        breakdown["market_adj"] = market_adj
        score = stock_score + market_adj

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
        if curr_price <= 0: return 50, ["no_price_data"], {}
        profit_pct = cls._compute_holding_profit_pct(holding, state)
        cash_ratio = cash_balance / market_total_krw if market_total_krw > 0 else 0
        panic_locks = user_state.panic_locks
        regime = (macro.market_regime.status if macro else 'Unknown').upper()
        if market_cash_ratio is None:
            market_cash_ratio = TradeExecutorService._get_target_cash_ratio('KR' if is_kr(ticker) else 'US', regime)
        target_cash_ratio = market_cash_ratio
        thresholds = cls._load_score_thresholds()
        thresholds["take_profit_pct"] = cls._get_take_profit_pct_by_regime(regime)
        if ticker in panic_locks:
            # B6: 재진입은 RSI 과매도 + 손절가 대비 +N% 회복 확인(바닥 확인) 시에만 20점
            lock = panic_locks.get(ticker)
            lock_price = float(lock.get("price") or 0) if isinstance(lock, dict) else 0.0
            recovery = SettingsService.get_float("STRATEGY_REENTRY_RECOVERY_PCT", 3.0)
            recovered = (lock_price <= 0) or (curr_price >= lock_price * (1 + recovery / 100))
            if state.rsi < thresholds["oversold_rsi"] and recovered:
                return 20, ["3day_recovery_wait"], {"panic_lock": True}
            return 50, ["panic_lock_zone" if recovered else f"panic_lock_no_recovery(<{lock_price*(1+recovery/100):,.0f})"], {"panic_lock": True}
        score, reasons, forced_sell, breakdown = cls._apply_score_components(ticker, state, holding, macro, user_state, profit_pct, curr_price, regime, thresholds)
        if forced_sell:
            return 100, reasons, breakdown

        return max(1, min(100, score)), reasons, breakdown

    # ── Analysis Interface ───────────────────────────────────────────────────────

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding, macro: MacroDataSnapshot, user_state: UserState, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """Public interface for external individual stock analysis."""
        score, reasons, breakdown = cls.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_total_krw=market_total_krw)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 70)

        recommendation = "WAIT"
        if score <= buy_threshold_max:
            recommendation = "BUY"
        elif score >= sell_threshold_min:
            recommendation = "SELL"

        market_adj = int(breakdown.get("market_adj", 0) or 0)
        return {
            "ticker": ticker,
            "score": score,
            "stock_score": breakdown.get("stock_score", score),
            "market_adj": market_adj,
            "buy_threshold": buy_threshold_max,
            "sell_threshold": sell_threshold_min,
            # 시장 조정치를 임계선으로 옮겨 본 값: stock_score ≤ eff_buy ⇔ score ≤ buy_threshold
            "effective_buy_threshold": buy_threshold_max - market_adj,
            "effective_sell_threshold": sell_threshold_min - market_adj,
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

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 70)
        port = PortfolioService.load_portfolio(user_id)

        if score <= buy_threshold_max and not holding:
            cls._dispatch_analyze_trade(ticker, "buy", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)
        elif score >= sell_threshold_min and holding:
            cls._dispatch_analyze_trade(ticker, "sell", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)

    # ── Signal Collection ─────────────────────────────────────────────────────────────

    @classmethod
    def _determine_analysis_markets(cls, allow_extended: bool) -> tuple[bool, bool]:
        """개장 여부 기반 분석 대상 시장 판단. 정규장+POST_CLOSE_BUFFER 활성 시간만.
        Returns (analyze_kr: bool, analyze_us: bool)."""
        kr_active = MarketHourService.is_kr_trading_active()
        us_active = MarketHourService.is_us_trading_active()
        analyze_kr = kr_active and not us_active
        analyze_us = us_active
        logger.info(f"📊 Market status: KR_active={kr_active}, US_active={us_active} → KR_analyze={analyze_kr}, US_analyze={analyze_us}")
        return analyze_kr, analyze_us

    @classmethod
    def _is_fear_market_exception(cls, macro: MacroDataSnapshot) -> bool:
        """공포장 예외: fear_greed < 20 AND Bear 레짐일 때 현금 게이트 스킵 허용.
        공포 정점에서 오히려 저가 매수 기회를 놓치지 않기 위한 예외."""
        fear_greed = macro.fear_greed or 50
        regime_status = macro.market_regime.status if macro.market_regime else "Neutral"
        return fear_greed < 20 and regime_status == "Bear"

    @classmethod
    def _is_price_stale(cls, ticker_state) -> bool:
        """D4: last_updated 가 STRATEGY_PRICE_STALE_SEC 초과 경과 → 신호 생성 스킵 (옛 가격 매매 방지)."""
        max_sec = SettingsService.get_int("STRATEGY_PRICE_STALE_SEC", 600)
        if max_sec <= 0:
            return False
        lu = getattr(ticker_state, "last_updated", None)
        if lu is None:
            return False  # 갱신 기록 없음(DB 로드 직후) — 판단 불가, 기존 동작 유지
        return (datetime.now() - lu).total_seconds() > max_sec

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
        watchlist_kr: set[str] | None = None,
        watchlist_us: set[str] | None = None,
        run_kr: bool = True,
        run_us: bool = True,
    ) -> list[SignalSchema]:
        """분석 시장 결정 → 하드게이트 → 스코어 계산 → 신호 수집.

        watchlist_kr/watchlist_us 가 None 이면 Top100 모드 (기존 동작).
        set 이면 Watchlist 모드 — 보유 종목이 아닌 경우 watchlist에 없는 티커는
        점수 계산 및 신호 생성을 건너뜀.
        """
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        analyze_kr_base, analyze_us_base = cls._determine_analysis_markets(allow_extended)
        
        # 외부에서 주입된 활성화 여부(run_kr, run_us)와 개장 여부 조합
        analyze_kr = analyze_kr_base and run_kr
        analyze_us = analyze_us_base and run_us

        holdings_map = {h.ticker: h for h in holdings}
        prepared_signals: list[SignalSchema] = []
        stale_skipped = 0

        for ticker, ticker_state in list(MarketDataService.get_all_states().items()):
            is_kr_ticker = is_kr(ticker)
            if (is_kr_ticker and not analyze_kr) or (not is_kr_ticker and not analyze_us):
                continue
            if not getattr(ticker_state, 'is_ready', False):
                continue
            holding = holdings_map.get(ticker)

            # Watchlist 모드 필터: 미보유 + watchlist 미포함 → BUY 신호·점수 계산 생략
            if holding is None:
                wl = watchlist_kr if is_kr_ticker else watchlist_us
                if wl is not None and ticker not in wl:
                    continue

            if cls._is_price_stale(ticker_state):
                stale_skipped += 1
                continue
            if cls._apply_hard_gates(ticker, ticker_state, holding, cash_balance, usd_cash, exchange_rate, kr_total, us_total_krw, target_cash_kr, target_cash_us, macro=macro_data):
                continue
            market_total = kr_total if is_kr_ticker else us_total_krw
            market_cash_ratio = target_cash_kr if is_kr_ticker else target_cash_us
            score, reasons, breakdown = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, cash_balance, market_cash_ratio=market_cash_ratio, market_total_krw=market_total)
            prepared_signals.append(SignalSchema(
                ticker=ticker, state=ticker_state, holding=holding, score=score, reasons=reasons,
                stock_score=breakdown.get("stock_score"), market_adj=int(breakdown.get("market_adj", 0) or 0),
            ))

        if stale_skipped:
            logger.warning(f"⏸ {stale_skipped} tickers skipped: price stale (> STRATEGY_PRICE_STALE_SEC)")
        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks ready.")
        cls._cached_signals = prepared_signals
        return prepared_signals
