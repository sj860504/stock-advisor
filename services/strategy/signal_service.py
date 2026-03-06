"""
SignalService: 점수 계산, 신호 수집
- 종목 점수 산출 (RSI, DCF, 기술, 포트폴리오, 시장 컨텍스트, 목표가, 보너스)
- 매매 신호 수집 (_collect_trading_signals)
- 개별 종목 분석 (analyze_ticker, _analyze_stock_v3)
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
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("signal_service")

TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class SignalService:
    """점수 계산 및 매매 신호 수집"""

    _top10_cache = {"timestamp": 0, "tickers": set()}

    # ── Top10 시총 캐시 ───────────────────────────────────────────────────────

    @classmethod
    def _get_top10_market_cap_tickers(cls) -> set:
        """미국/한국 시가총액 상위 10개 티커 캐시 반환"""
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

    # ── 점수 컴포넌트 ─────────────────────────────────────────────────────────

    @classmethod
    def _score_rsi(cls, rsi: float, oversold_rsi: float, overbought_rsi: float) -> tuple:
        """RSI 구간별 점수 계산. Returns (delta, reasons)."""
        delta = 0
        reasons = []
        if rsi <= 30:
            rsi_score = -(20 - (rsi / 30) * 10)
            delta += int(rsi_score)
            reasons.append(f"RSI극과매도({rsi:.1f},{int(rsi_score)})")
        elif rsi < 50:
            rsi_score = -(10 - ((rsi - 30) / 20) * 10)
            if rsi_score <= -5:
                delta += int(rsi_score)
                reasons.append(f"RSI과매도({rsi:.1f},{int(rsi_score)})")
        elif rsi <= 70:
            rsi_score = ((rsi - 50) / 20) * 10
            if rsi_score >= 5:
                delta += int(rsi_score)
                reasons.append(f"RSI과매수({rsi:.1f},+{int(rsi_score)})")
        else:
            rsi_score = 10 + ((rsi - 70) / 30) * 10
            delta += int(rsi_score)
            reasons.append(f"RSI극과매수({rsi:.1f},+{int(rsi_score)})")
        return delta, reasons

    @classmethod
    def _score_dcf(cls, dcf_value: float, curr_price: float) -> tuple:
        """DCF 대비 저/고평가 점수 계산. Returns (delta, reasons)."""
        if not (dcf_value and dcf_value > 0):
            return 0, []
        delta = 0
        reasons = []
        undervalue_pct = (dcf_value - curr_price) / curr_price * 100
        WEIGHTS = TradeExecutorService.WEIGHTS
        if undervalue_pct >= 20:
            delta += WEIGHTS['DCF_UNDERVALUE_HIGH']; reasons.append(f"DCF고저평가({undervalue_pct:.1f}%)")
        elif undervalue_pct >= 10:
            delta += WEIGHTS['DCF_UNDERVALUE_MID']; reasons.append(f"DCF중저평가({undervalue_pct:.1f}%)")
        elif undervalue_pct >= 5:
            delta += WEIGHTS['DCF_UNDERVALUE_LOW']; reasons.append(f"DCF저평가({undervalue_pct:.1f}%)")
        elif undervalue_pct >= -5:
            delta += WEIGHTS['DCF_FAIR_VALUE']; reasons.append("DCF적정가")
        elif undervalue_pct >= -15:
            delta += WEIGHTS['DCF_OVERVALUE_LOW']; reasons.append(f"DCF고평가({-undervalue_pct:.1f}%)")
        else:
            delta += WEIGHTS['DCF_OVERVALUE_HIGH']; reasons.append(f"DCF고고평가({-undervalue_pct:.1f}%)")
        return delta, reasons

    @classmethod
    def _score_technical(cls, state, curr_price: float, oversold_rsi: float, overbought_rsi: float, dip_buy_pct: float) -> tuple:
        """[A] RSI + 급락/급등 + DCF + EMA200 → (delta, reasons)"""
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        rsi_delta, rsi_reasons = cls._score_rsi(state.rsi, oversold_rsi, overbought_rsi)
        delta += rsi_delta; reasons.extend(rsi_reasons)

        change_rate = getattr(state, 'change_rate', 0)
        if change_rate <= dip_buy_pct:
            delta += WEIGHTS['DIP_BUY_5PCT']; reasons.append(f"급락({change_rate:.1f}%)")
        elif change_rate >= 5.0:
            delta += WEIGHTS['SURGE_SELL_5PCT']; reasons.append(f"급등({change_rate:.1f}%)")

        dcf_d, dcf_r = cls._score_dcf(state.dcf_value, curr_price)
        delta += dcf_d; reasons.extend(dcf_r)

        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 <= curr_price <= ema200 * 1.02):
            delta += WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200지지")
        return delta, reasons

    @classmethod
    def _score_portfolio(cls, holding, profit_pct: float, take_profit_pct: float, stop_loss_pct: float) -> tuple:
        """[B] 익절 / 추매 / 손절 → (delta, reasons, forced_sell)"""
        if not holding:
            return 0, [], False
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        if profit_pct >= take_profit_pct:
            delta += WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"익절권({profit_pct:.1f}%)")
        elif profit_pct <= -5.0 and profit_pct > stop_loss_pct:
            delta += WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"추매권({profit_pct:.1f}%)")
        elif profit_pct <= stop_loss_pct:
            return 0, ["손절도달"], True  # forced_sell: score=100
        return delta, reasons, False

    @classmethod
    def _score_market_context(cls, macro: dict, regime: str) -> tuple:
        """[C] 공포/과열 + 상승/하락장 → (delta, reasons)"""
        WEIGHTS = TradeExecutorService.WEIGHTS
        delta = 0
        reasons = []
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        if vix >= 25 or fng <= 30:
            delta += WEIGHTS['PANIC_MARKET_BUY']; reasons.append("극도의공포(매수기회)")
        elif vix <= 15 or fng >= 70:
            delta += WEIGHTS['PROFIT_TAKE_TARGET'] // 2; reasons.append("시장과열(분할익절)")
        if regime == 'BULL':
            delta += WEIGHTS['BULL_MARKET_SECTOR']; reasons.append("상승장어드밴티지")
        elif regime == 'BEAR':
            delta += 10; reasons.append("하락장리스크관리")
        return delta, reasons

    @classmethod
    def _score_target_prices(cls, state, curr_price: float) -> tuple:
        """[D] 사용자 설정 목표 진입가/매도가 도달 → (delta, reasons)"""
        delta = 0
        reasons = []
        target_buy = getattr(state, 'target_buy_price', 0)
        target_sell = getattr(state, 'target_sell_price', 0)
        if target_buy > 0 and curr_price <= target_buy:
            delta -= 30; reasons.append(f"목표진입가도달(${target_buy})")
        if target_sell > 0 and curr_price >= target_sell:
            delta += 30; reasons.append(f"목표매도가도달(${target_sell})")
        return delta, reasons

    @classmethod
    def _score_bonuses(cls, ticker: str, holding, macro: dict, user_state: dict) -> tuple:
        """[E-G] 시총상위10 / 사용자가중치 / 섹터비중 보너스 → (delta, reasons)"""
        delta = 0
        reasons = []
        top10_bonus = SettingsService.get_int("STRATEGY_TOP10_BONUS", 10)
        if top10_bonus and ticker in cls._get_top10_market_cap_tickers():
            delta -= top10_bonus; reasons.append(f"시총상위10(-{top10_bonus})")

        overrides = TradeExecutorService.get_top_weight_overrides()
        if ticker in overrides:
            custom_bonus = int(overrides[ticker])
            if custom_bonus != 0:
                delta += custom_bonus; reasons.append(f"가중치사용자설정({custom_bonus:+d})")

        try:
            grp = TradeExecutorService._get_sector_group(ticker, holding)
            if grp != "other":
                exchange_rate_g = MacroService.get_exchange_rate()
                all_holdings = PortfolioService.load_portfolio(user_state.get("user_id", "sean"))
                sw = TradeExecutorService._get_sector_group_weights(all_holdings, exchange_rate_g)
                dev = sw["weights"].get(grp, {}).get("dev", 0.0)
                if dev < -TradeExecutorService.SECTOR_REBAL_THRESHOLD:
                    delta -= 10; reasons.append(f"섹터부족매수우선({grp} {dev:+.1%})")
                elif dev > TradeExecutorService.SECTOR_REBAL_THRESHOLD:
                    delta += 10; reasons.append(f"섹터초과매도우선({grp} {dev:+.1%})")
        except Exception:
            pass
        return delta, reasons

    # ── 점수 통합 ─────────────────────────────────────────────────────────────

    @classmethod
    def _load_score_thresholds(cls) -> dict:
        """SettingsService에서 점수 계산에 필요한 6개 임계값을 로드해 dict로 반환."""
        return {
            "oversold_rsi":    SettingsService.get_float("STRATEGY_OVERSOLD_RSI", 30.0),
            "overbought_rsi":  SettingsService.get_float("STRATEGY_OVERBOUGHT_RSI", 70.0),
            "dip_buy_pct":     SettingsService.get_float("STRATEGY_DIP_BUY_PCT", -5.0),
            "take_profit_pct": SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 3.0),
            "stop_loss_pct":   SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -8.0),
            "base_score":      SettingsService.get_int("STRATEGY_BASE_SCORE", 50),
        }

    @classmethod
    def _apply_score_components(cls, ticker: str, state, holding, macro: dict, user_state: dict, profit_pct: float, curr_price: float, regime: str, thresholds: dict) -> tuple:
        """[A]~[G] 점수 컴포넌트를 누적하여 (score, reasons, forced_sell) 반환."""
        t = thresholds
        score = t["base_score"]
        reasons: list = []
        d, r = cls._score_technical(state, curr_price, t["oversold_rsi"], t["overbought_rsi"], t["dip_buy_pct"])
        score += d; reasons.extend(r)
        d, r, forced_sell = cls._score_portfolio(holding, profit_pct, t["take_profit_pct"], t["stop_loss_pct"])
        if forced_sell:
            return 100, r, True
        score += d; reasons.extend(r)
        d, r = cls._score_market_context(macro, regime); score += d; reasons.extend(r)
        d, r = cls._score_target_prices(state, curr_price); score += d; reasons.extend(r)
        d, r = cls._score_bonuses(ticker, holding, macro, user_state); score += d; reasons.extend(r)
        return score, reasons, False

    @classmethod
    def _compute_holding_profit_pct(cls, holding, state) -> float:
        """보유 종목의 수익률(%) 계산. 미보유 시 0.0 반환."""
        if not holding:
            return 0.0
        buy_price = (getattr(holding, "buy_price", None) if not isinstance(holding, dict) else holding.get("buy_price", None))
        ref_price = getattr(holding, "current_price", 0) if not isinstance(holding, dict) else float(holding.get("current_price") or 0)
        if ref_price <= 0:
            ref_price = getattr(state, "current_price", 0)
        return (ref_price - buy_price) / buy_price * 100 if buy_price and buy_price > 0 else 0.0

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, market_cash_ratio: float = None, market_total_krw: float = 0.0) -> tuple:
        """개별 종목의 투자 점수 계산 ([A]~[G] 헬퍼 통합)"""
        curr_price = state.current_price
        if curr_price <= 0: return 0, ["가격정보없음"]
        profit_pct = cls._compute_holding_profit_pct(holding, state)
        cash_ratio = cash_balance / market_total_krw if market_total_krw > 0 else 0
        panic_locks = user_state.get('panic_locks', {})
        regime = macro.get('market_regime', {}).get('status', 'Unknown').upper()
        if market_cash_ratio is None:
            market_cash_ratio = TradeExecutorService._get_target_cash_ratio('KR' if is_kr(ticker) else 'US', regime)
        target_cash_ratio = market_cash_ratio
        thresholds = cls._load_score_thresholds()
        if ticker in panic_locks:
            return (20, ["3일룰회복대기"]) if state.rsi < thresholds["oversold_rsi"] else (50, ["패닉락구간"])
        score, reasons, forced_sell = cls._apply_score_components(ticker, state, holding, macro, user_state, profit_pct, curr_price, regime, thresholds)
        if forced_sell:
            return 100, reasons
        if cash_ratio < target_cash_ratio and score > 50:
            score += TradeExecutorService.WEIGHTS['CASH_PENALTY']; reasons.append("현금부족")
        return max(0, min(100, score)), reasons

    # ── 분석 인터페이스 ───────────────────────────────────────────────────────

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """외부에서 개별 종목 분석 결과를 받을 수 있도록 공개된 인터페이스"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_total_krw=market_total_krw)

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
            "current_price": state.current_price,
            "rsi": state.rsi,
            "dcf_value": getattr(state, 'dcf_value', None),
        }

    @classmethod
    def _dispatch_analyze_trade(cls, ticker: str, side: str, score: int, reason_str: str, state, profit_pct: float, market_total: float, cash_balance: float, exchange_rate: float, holdings: list, user_id: str, holding, macro: dict) -> None:
        """_analyze_stock_v3 에서 매수/매도 _execute_trade_v2 호출을 위임."""
        is_holding = bool(holding)
        TradeExecutorService._execute_trade_v2(
            ticker, side, f"점수 {score} [{reason_str}]", profit_pct, is_holding, score,
            state.current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro,
        )

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, market_total: float, cash_balance: float, exchange_rate: float, user_id: str = "sean") -> None:
        """기존 내부 분석 루프 (리팩토링된 calculate_score 활용)"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_total_krw=market_total)
        profit_pct = cls._compute_holding_profit_pct(holding, state)
        reason_str = ", ".join(reasons)

        buy_threshold_max = SettingsService.get_int("STRATEGY_BUY_THRESHOLD_MAX", 30)
        sell_threshold_min = SettingsService.get_int("STRATEGY_SELL_THRESHOLD_MIN", 70)
        port = PortfolioService.load_portfolio(user_id)

        if score <= buy_threshold_max and not holding:
            cls._dispatch_analyze_trade(ticker, "buy", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)
        elif score >= sell_threshold_min and holding:
            cls._dispatch_analyze_trade(ticker, "sell", score, reason_str, state, profit_pct, market_total, cash_balance, exchange_rate, port, user_id, holding, macro)

    # ── 신호 수집 ─────────────────────────────────────────────────────────────

    @classmethod
    def _collect_trading_signals(cls, holdings: list, macro_data: dict, user_state: dict, kr_total: float, us_total_krw: float, cash_balance: float, target_cash_kr: float, target_cash_us: float) -> list:
        """시장 상태를 확인하고 유효한 매매 시그널을 수집"""
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)

        analyze_kr = not is_us_open
        analyze_us = not is_kr_open
        logger.info(f"📊 시장 상태: KR개장={is_kr_open}, US개장={is_us_open} → KR분석={analyze_kr}, US분석={analyze_us}")

        all_states = MarketDataService.get_all_states()
        prepared_signals = []

        holdings_map = {h['ticker']: h for h in holdings}
        for ticker, ticker_state in list(all_states.items()):
            is_kr_ticker = is_kr(ticker)
            if (is_kr_ticker and not analyze_kr) or (not is_kr_ticker and not analyze_us):
                continue
            if not getattr(ticker_state, 'is_ready', False):
                continue

            holding = holdings_map.get(ticker)
            market_cash_ratio = target_cash_kr if is_kr_ticker else target_cash_us
            market_total = kr_total if is_kr_ticker else us_total_krw
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, cash_balance, market_cash_ratio=market_cash_ratio, market_total_krw=market_total)

            prepared_signals.append({"ticker": ticker, "state": ticker_state, "holding": holding, "score": score, "reasons": reasons})

        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks ready.")
        return prepared_signals
