"""
PositionService: 포지션 관리 및 매매 신호 실행
- 익절 / 추가매수 / 분할매수 / 매도 핸들러
- 단일 신호 처리 (_process_single_signal)
- 비유니버스 보유 종목 스탑로스/익절 체크 (_check_unmonitored_holdings)
- 수집된 신호 일괄 실행 (_execute_collected_signals)
"""
from datetime import datetime
from typing import Optional

import pytz

from services.market.macro_service import MacroService
from services.config.settings_service import SettingsService
from services.strategy.execution_service_v2 import TradeExecutorService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("position_service")


class PositionService:
    """포지션 관리 및 매매 신호 실행"""

    # ── 익절 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _handle_profit_take_signal(
        cls, ticker: str, holding: dict, profit_pct: float, take_profit_pct: float,
        sell_cooldown: dict, today: str, state, score: int,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> bool:
        """익절 조건 처리. 쿨다운 적용. 실행 여부 반환."""
        if not (holding and profit_pct >= take_profit_pct):
            return False
        if sell_cooldown.get(ticker) == today:
            logger.info(f"⏭️ {ticker} 분할매도 쿨다운 중 (오늘 이미 익절매도). 내일 재판단.")
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"익절권({profit_pct:.2f}%)", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        sell_cooldown[ticker] = today  # 성공/실패 무관 당일 재시도 방지
        return bool(executed)

    # ── 쿨다운 ───────────────────────────────────────────────────────────────

    @classmethod
    def _is_buy_cooldown_active(cls, ticker: str, today: str, current_price: float, add_buy_cooldown: dict) -> bool:
        """쿨다운 활성 여부 판단. 구버전(str) 하위 호환 포함.
        구매가 대비 -5% 이하 하락 시 쿨다운 예외(당일 재매수 허용).
        """
        cd = add_buy_cooldown.get(ticker)
        if not cd:
            return False
        if isinstance(cd, str):             # 구버전: "YYYY-MM-DD"
            return cd == today
        if cd.get('date') != today:         # 다른 날 → 쿨다운 만료
            return False
        buy_price = cd.get('price', 0)
        if buy_price > 0 and current_price <= buy_price * 0.95:  # -5% 예외
            return False
        return True

    # ── 추가매수 ─────────────────────────────────────────────────────────────

    @classmethod
    def _handle_add_buy_signal(
        cls, ticker: str, holding: dict, profit_pct: float, stop_loss_pct: float,
        current_rsi: float, add_rsi_limit: float, add_score_limit: int, score: int,
        add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float,
    ) -> bool:
        """추가매수 조건 처리. 쿨다운/RSI/스코어 필터 적용. 실행 여부 반환."""
        if not (holding and profit_pct <= -5.0 and profit_pct > stop_loss_pct):
            return False
        if current_rsi >= add_rsi_limit:
            logger.info(f"⏭️ {ticker} 추가매수 RSI 과매수({current_rsi:.1f} ≥ {add_rsi_limit}). 스킵.")
            return False
        if score > add_score_limit:
            logger.info(f"⏭️ {ticker} 추가매수 스코어 불충족({score} > {add_score_limit}). 스킵.")
            return False
        current_price_val = getattr(state, 'current_price', 0)
        if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
            logger.info(f"⏭️ {ticker} 추가매수 쿨다운 중 (오늘 이미 추매). 내일 재판단.")
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "buy", f"추가매수({profit_pct:.2f}%)", profit_pct, True, score,
            current_price_val, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        if executed:
            add_buy_cooldown[ticker] = {"date": today, "price": current_price_val}
        return bool(executed)

    # ── 분할매수 ─────────────────────────────────────────────────────────────

    @classmethod
    def _handle_buy_split(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        buy_max: int, add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
    ) -> bool:
        """신규/분할 매수 로직. 호출자가 score/holding 조건 gate를 보장해야 함."""
        has_pending_splits = ticker in split_orders
        if not has_pending_splits:
            sector = getattr(state, 'sector', '') or ''
            if sector in ('ETF', 'Others', '미분류/ETF'):
                logger.info(f"⏭️ {ticker} ETF/기타 섹터 신규 매수 차단 (sector={sector}). 스킵.")
                return False
        current_price_val = getattr(state, 'current_price', 0)
        if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
            logger.info(f"⏭️ {ticker} 신규매수 쿨다운 중 (오늘 이미 매수). 내일 재판단.")
            return False
        # 분할 주문 초기화 또는 기존 진행 상황 로드
        if not has_pending_splits:
            is_kr_flag = is_kr(ticker)
            usd_cash_krw = 0.0
            if not is_kr_flag:
                from services.trading.portfolio_service import PortfolioService as _PS
                usd_cash_krw = (_PS.get_usd_cash_balance() or 0) * exchange_rate
            total_qty, _, _ = TradeExecutorService._calculate_buy_quantity(score, cash_balance, current_price_val, exchange_rate, is_kr_flag, market_total, usd_cash_krw=usd_cash_krw)
            if total_qty <= 0:
                logger.warning(f"⚠️ {ticker} 잔고 부족 또는 수량 0. 매수 불가.")
                return False
            split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
            split_orders[ticker] = {
                "total_qty": total_qty,
                "remaining_qty": total_qty,
                "splits_done": 0,
                "split_count": split_count,
                "start_date": today,
                "entry_price": current_price_val,
            }
        so = split_orders[ticker]
        remaining = so["remaining_qty"]
        splits_left = so["split_count"] - so["splits_done"]
        # 올림 나눗셈으로 앞 차수에 더 많이 배분: [2,2,1] 형태
        this_run_qty = -(-remaining // splits_left) if splits_left > 0 and remaining > 0 else remaining
        if this_run_qty <= 0:
            split_orders.pop(ticker, None)
            return False
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "buy",
            f"점수 {score} [{reason_str}] ({so['splits_done']+1}/{so['split_count']}차)",
            profit_pct, bool(holding), score, current_price_val, market_total, cash_balance,
            exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=this_run_qty,
        )
        if executed:
            so["splits_done"] += 1
            so["remaining_qty"] -= this_run_qty
            if so["remaining_qty"] <= 0:
                split_orders.pop(ticker, None)
            add_buy_cooldown[ticker] = {"date": today, "price": current_price_val}
        return bool(executed)

    # ── 점수 기반 매도 ────────────────────────────────────────────────────────

    @classmethod
    def _handle_sell_signal(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        sell_min: int, sell_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
    ) -> bool:
        """점수 기반 매도 로직. 호출자가 score/holding 조건 gate를 보장해야 함."""
        if sell_cooldown.get(ticker) == today:
            logger.info(f"⏭️ {ticker} 분할매도 쿨다운 중 (오늘 이미 점수매도). 내일 재판단.")
            return False
        split_orders.pop(ticker, None)  # 잔여 분할매수 취소
        executed = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"점수 {score} [{reason_str}]", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
        )
        sell_cooldown[ticker] = today  # 성공/실패 무관 당일 재시도 방지
        return bool(executed)

    # ── 점수 기반 매수/매도 통합 ──────────────────────────────────────────────

    @classmethod
    def _handle_score_trade(
        cls, ticker: str, holding, score: int, reason_str: str, profit_pct: float,
        buy_max: int, sell_min: int, sell_cooldown: dict, add_buy_cooldown: dict,
        today: str, state, market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: dict,
        target_cash_kr: float, target_cash_us: float, split_orders: dict = None,
    ) -> bool:
        """점수 기반 매수/매도 처리 - 하위 핸들러로 위임."""
        if split_orders is None:
            split_orders = {}
        has_pending_splits = ticker in split_orders
        if 0 < score <= buy_max and (not holding or has_pending_splits):  # score=0 은 매수 제외
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

    # ── 단일 신호 처리 ────────────────────────────────────────────────────────

    @classmethod
    def _process_single_signal(
        cls, sig: dict, buy_max: int, sell_min: int, take_profit_pct: float,
        stop_loss_pct: float, add_rsi_limit: float, add_score_limit: int,
        sell_cooldown: dict, add_buy_cooldown: dict, today: str,
        holdings: list, user_id: str, kr_total: float, us_total_krw: float, cash_balance: float,
        exchange_rate: float, macro_data: dict, target_cash_kr: float, target_cash_us: float,
        split_orders: dict = None,
    ) -> bool:
        """시그널 1건을 처리하여 매매 실행 여부 반환."""
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

    # ── 비유니버스 보유 종목 체크 ─────────────────────────────────────────────

    @classmethod
    def _check_unmonitored_holdings(
        cls, prepared_signals: list, holdings: list, user_id: str,
        kr_total: float, us_total_krw: float, cash_balance: float, exchange_rate: float,
        macro_data: dict, target_cash_kr: float, target_cash_us: float,
        take_profit_pct: float, stop_loss_pct: float,
        sell_cooldown: dict, today: str,
    ) -> bool:
        """모니터링 유니버스 밖 보유 종목(ETF 등)에 대한 스탑로스/익절 체크."""
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
            current_price = float(h.get('current_price') or 0)
            if buy_price <= 0 or current_price <= 0:
                continue
            profit_pct = (current_price - buy_price) / buy_price * 100
            logger.info(f"🔍 [비유니버스 보유] {ticker} ({h.get('name', '')}): PnL={profit_pct:.1f}%")
            market_total = kr_total if is_kr(ticker) else us_total_krw
            if profit_pct <= stop_loss_pct:
                executed = TradeExecutorService._execute_trade_v2(
                    ticker, "sell", f"스탑로스({profit_pct:.2f}%)", profit_pct, True, 0,
                    current_price, market_total, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=h, macro=macro_data,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                trade_executed = bool(executed) or trade_executed
            elif profit_pct >= take_profit_pct:
                if sell_cooldown.get(ticker) == today:
                    logger.info(f"⏭️ {ticker} 분할매도 쿨다운 중 (오늘 이미 익절매도). 내일 재판단.")
                    continue
                executed = TradeExecutorService._execute_trade_v2(
                    ticker, "sell", f"익절권({profit_pct:.2f}%)", profit_pct, True, 0,
                    current_price, market_total, cash_balance, exchange_rate,
                    holdings=holdings, user_id=user_id, holding=h, macro=macro_data,
                    target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
                )
                if executed:
                    sell_cooldown[ticker] = today
                trade_executed = bool(executed) or trade_executed
        return trade_executed

    # ── 신호 일괄 실행 ────────────────────────────────────────────────────────

    @classmethod
    def _execute_collected_signals(
        cls, user_id: str, prepared_signals: list, holdings: list,
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float, macro_data: dict,
        user_state: dict = None,
    ) -> bool:
        """수집된 시그널을 기반으로 실제 주문 집행"""
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

        # 모니터링 유니버스 외 보유 종목(ETF 등) 스탑로스/익절 체크
        trade_executed = cls._check_unmonitored_holdings(
            prepared_signals, holdings, user_id, kr_total, us_total_krw, cash_balance,
            exchange_rate, macro_data, target_cash_kr, target_cash_us,
            take_profit_pct, stop_loss_pct, sell_cooldown, today,
        ) or trade_executed
        return trade_executed
