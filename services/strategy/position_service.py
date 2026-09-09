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
from services.market.market_hour_service import MarketHourService
from services.market.macro_service import MacroService
from services.config.settings_service import SettingsService
from services.strategy.execution_service_v2 import TradeExecutorService
from models.schemas import SplitOrderState, BuyCooldownEntry, SplitSellOrderState, TradeResult, MacroDataSnapshot, ExecutionConfig, UnpackedSignal, SignalSchema, UserState, HoldingSchema
from services.strategy.uptrend_rules import (
    parse_scale_out, stages_done_count, trailing_pct_by_profit, trailing_floor_price,
    dca_stages, dca_reference_mode, uptrend_stop_loss_pct,
)
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("position_service")


class PositionService:
    """Position management and trading signal execution."""

    # ── Uptrend DCA — 부분 익절 + 잔여 trailing ────────────────────────────────

    @classmethod
    def _is_uptrend_dca_enabled(cls) -> bool:
        """Uptrend DCA 알고리즘 활성화 여부. 기본 ON."""
        return SettingsService.get_int("STRATEGY_UPTREND_DCA_ENABLED", 1) == 1

    @classmethod
    def _handle_uptrend_partial_take(
        cls, ticker: str, holding, profit_pct: float, state, score: int,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, user_state,
    ) -> bool:
        """[Uptrend/S2] 다단계 스케일아웃. STRATEGY_UPTREND_SCALE_OUT='10:0.5,20:0.5,35:0.5'
        → +10% 에 보유의 50%, +20% 에 잔여의 50%, +35% 에 잔여의 50%. 단계별 1회
        (partial_take_done[ticker] = 완료 단계 수; 레거시 True 는 1). Crash 중엔 보류."""
        if not holding or not user_state:
            return False
        stages = parse_scale_out()
        done = stages_done_count(user_state.partial_take_done.get(ticker))
        if done >= len(stages):
            return False
        tp_pct, ratio = stages[done]
        if profit_pct < tp_pct:
            return False
        from services.strategy.crash_guard_service import CrashGuardService
        if CrashGuardService.is_crash(macro_data):
            logger.info(f"⏸ {ticker} 스케일아웃 {done + 1}단계 보류 (Crash 가드 활성)")
            return False
        qty = int(holding.quantity)
        sell_qty = max(1, int(qty * ratio))
        if sell_qty >= qty:
            sell_qty = max(1, qty - 1) if qty > 1 else qty
        cur_px = float(getattr(state, "current_price", 0) or 0)
        result = TradeExecutorService._execute_trade_v2(
            ticker, "sell",
            f"uptrend_scale_out(stage{done + 1}/{len(stages)}, +{profit_pct:.2f}%, {ratio * 100:.0f}%)",
            profit_pct, True, score,
            cur_px, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=sell_qty, trigger_reason="partial_take",
        )
        if result.executed:
            user_state.partial_take_done[ticker] = done + 1
            prev_high = float(user_state.remaining_high.get(ticker, 0) or 0)
            user_state.remaining_high[ticker] = max(prev_high, cur_px)
            cls._set_reentry_block(ticker, cur_px, user_state)
        return result.executed

    @classmethod
    def _handle_uptrend_dca_signal(
        cls, ticker: str, holding, profit_pct: float, current_price: float,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, user_state,
        usd_cash: float = 0.0,
    ) -> "TradeResult":
        """[Uptrend] DCA 3단계 추매 (-3/-8/-15% × 3/4/5% 자본).
        지수 5d 변화율 mult 적용 (KR→KOSPI, US→SPX; 1.0/1.5/2.0/2.5×). Frozen(VIX>50) 시 보류.
        종목당 max position 가드, min cash 가드.

        통화 규칙: 모든 금액은 KRW 로 계산한다.
          - market_total 은 시장별 총자산(KRW, US 는 usd_cash 포함)
          - US 종목은 가격을 exchange_rate 로 KRW 환산, 가용현금은 usd_cash×exchange_rate
        (이전: US 종목에 KRW 현금·KRW 예산을 USD 가격으로 나눠 수량이 수천 배 과대 산출 → 미수차단
        가드가 'USD 현금 전액' 으로 축소해 단계 비율이 무시되던 버그)"""
        if not holding or not user_state:
            return TradeResult.no_op()
        from services.strategy.crash_guard_service import CrashGuardService
        if CrashGuardService.is_frozen(macro_data):
            logger.info(f"⏸ {ticker} DCA 보류 (Frozen 가드 — VIX>50)")
            return TradeResult.no_op()
        if current_price <= 0:
            return TradeResult.no_op()
        is_kr_ticker = is_kr(ticker)
        fx = exchange_rate if (exchange_rate and exchange_rate > 0) else 1.0
        price_krw = current_price if is_kr_ticker else current_price * fx
        avail_cash_krw = cash_balance if is_kr_ticker else (usd_cash or 0.0) * fx

        # 3단계 임계 + 비율 (깊은 단계 우선)
        stages = dca_stages()
        ticker_dca = user_state.dca_done.get(ticker) or {}
        # B4: 종목당 하루 최대 N단계 — 갭하락 -16% 에 3분 만에 3단계 전부 투입되는 것 방지
        today_kst = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d')
        max_per_day = SettingsService.get_int("STRATEGY_UPTREND_DCA_MAX_STAGES_PER_DAY", 1)
        if max_per_day > 0 and ticker_dca.get("last_date") == today_kst \
                and int(ticker_dca.get("today_count") or 0) >= max_per_day:
            return TradeResult.no_op()
        # B4: 기준가 — avg(평균단가, 기본·시뮬 검증) | entry(첫 평가 시점 buy_price 고정, 공격적)
        ref_profit_pct = profit_pct
        if dca_reference_mode() == "entry":
            ref_price = float(ticker_dca.get("ref_price") or 0)
            if ref_price <= 0:
                ref_price = float(holding.buy_price or 0)
                if ref_price > 0:
                    ticker_dca["ref_price"] = ref_price
                    user_state.dca_done[ticker] = ticker_dca
            if ref_price > 0:
                ref_profit_pct = (current_price - ref_price) / ref_price * 100
        triggered_stage = None
        triggered_ratio = None
        for threshold, ratio, key in stages:
            if ticker_dca.get(str(key)):  # JSON serialization 호환
                continue
            if ref_profit_pct <= threshold:
                triggered_stage = key
                triggered_ratio = ratio
                break
        if triggered_stage is None:
            return TradeResult.no_op()

        # 지수 5d mult 적용 (종목 시장 기준)
        mult = CrashGuardService.per_trade_mult(macro_data, ticker)
        effective_ratio = triggered_ratio * mult

        # 자본(KRW) = 시장별 총자산. market_total 은 이미 해당 시장의 현금을 포함
        # (kr_total = KR 평가액 + KRW 현금, us_total_krw = US 평가액 + USD 현금×환율).
        total_assets = market_total if market_total > 0 else (
            (holding.quantity or 0) * price_krw + avail_cash_krw
        )
        budget = total_assets * effective_ratio

        # max position 가드 (KRW 기준)
        max_pos_pct = SettingsService.get_float("STRATEGY_UPTREND_MAX_POSITION_PCT", 0.15)
        cur_pos_val = (holding.quantity or 0) * price_krw
        max_allowed = total_assets * max_pos_pct
        if cur_pos_val >= max_allowed:
            logger.info(f"⏭ {ticker} DCA 단계 {triggered_stage}% 소진 (max position {max_pos_pct*100:.0f}% 도달)")
            ticker_dca[str(triggered_stage)] = True  # 포지션 한도 도달 → 이 단계는 종료 처리
            user_state.dca_done[ticker] = ticker_dca
            return TradeResult.no_op()
        if cur_pos_val + budget > max_allowed:
            budget = max(0, max_allowed - cur_pos_val)

        # min cash 가드 — DCA 는 예비현금(RESERVE)을 쓸 수 있고 MIN_CASH 까지만 내려간다
        min_cash_ratio = SettingsService.get_float("STRATEGY_UPTREND_MIN_CASH_RATIO", 0.0)
        min_cash = total_assets * min_cash_ratio
        if avail_cash_krw - budget < min_cash:
            budget = max(0, avail_cash_krw - min_cash)

        if budget < price_krw:
            # 현금 부족은 단계 소진이 아님 — 다른 종목 매도 등으로 현금 생기면 재시도
            logger.info(f"⏸ {ticker} DCA 단계 {triggered_stage}% 대기 (예산 {budget:,.0f} < 1주 {price_krw:,.0f} KRW)")
            return TradeResult.no_op()
        add_qty = int(budget / price_krw)
        if add_qty <= 0:
            return TradeResult.no_op()

        result = TradeExecutorService._execute_trade_v2(
            ticker, "buy",
            f"uptrend_dca({triggered_stage}%, ratio={triggered_ratio*100:.1f}%×mult{mult:.1f})",
            profit_pct, True, 0,
            current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=add_qty, trigger_reason=f"dca_stage_{triggered_stage}",
        )
        if result.executed:
            ticker_dca[str(triggered_stage)] = True
            ticker_dca["today_count"] = (int(ticker_dca.get("today_count") or 0) + 1) if ticker_dca.get("last_date") == today_kst else 1
            ticker_dca["last_date"] = today_kst
            user_state.dca_done[ticker] = ticker_dca
        return result

    @classmethod
    def _set_reentry_block(cls, ticker: str, sell_price: float, user_state) -> None:
        """B6: 매도 후 재매수 차단 — add_buy_cooldown 에 kind='sell' 엔트리.
        해제: STRATEGY_REENTRY_DAYS 경과 OR 매도가 대비 STRATEGY_REENTRY_DROP_PCT 하락."""
        if user_state is None:
            return
        kst = pytz.timezone("Asia/Seoul")
        now_dt = datetime.now(kst)
        user_state.add_buy_cooldown[ticker] = BuyCooldownEntry(
            date=now_dt.strftime("%Y-%m-%d"), price=float(sell_price or 0), timestamp=now_dt.timestamp(),
            kind="sell", expire_days=max(1, SettingsService.get_int("STRATEGY_REENTRY_DAYS", 5)),
        )

    @classmethod
    def _reset_uptrend_state_on_sell(cls, ticker: str, user_state) -> None:
        """매도 후 Uptrend 상태 클리어. 재진입 시 신규 진입처럼 동작."""
        if user_state is None:
            return
        user_state.partial_take_done.pop(ticker, None)
        user_state.remaining_high.pop(ticker, None)
        user_state.dca_done.pop(ticker, None)

    @classmethod
    def _handle_uptrend_remaining_trailing(
        cls, ticker: str, holding, current_price: float,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, user_state,
    ) -> bool:
        """[Uptrend] 부분익절 후 잔여 수량 trailing (-N% from remaining_high). Crash 중엔 보류."""
        if not holding or not user_state:
            return False
        if stages_done_count(user_state.partial_take_done.get(ticker)) == 0:
            return False
        from services.strategy.crash_guard_service import CrashGuardService
        if CrashGuardService.is_crash(macro_data):
            return False
        cur_high = float(user_state.remaining_high.get(ticker, current_price))
        if current_price > cur_high:
            user_state.remaining_high[ticker] = current_price
            return False
        buy_price = float(holding.buy_price or 0)
        # S2: 고점 수익률 구간별 trailing 폭 (+20% 이상 -7%, +35% 이상 -5%; 기본 -10%)
        max_profit_pct = (cur_high - buy_price) / buy_price * 100 if buy_price > 0 else 0.0
        trail_pct = trailing_pct_by_profit(max_profit_pct)
        drawdown = (current_price - cur_high) / cur_high * 100 if cur_high > 0 else 0
        if drawdown > trail_pct:
            return False
        # S1: 손익분기 하한 — 잔여분을 손실(또는 수수료 이하 수익)로 팔지 않는다. 그 아래면 보유 유지(DCA 규칙으로 복귀)
        if buy_price > 0 and current_price < trailing_floor_price(buy_price):
            logger.info(f"⏸ {ticker} 잔여 trailing 보류: 현재가 {current_price:,.0f} < 손익분기 하한 {trailing_floor_price(buy_price):,.0f}")
            return False
        profit_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0
        result = cls._handle_forced_sell(
            ticker, holding, profit_pct, current_price, market_total,
            cash_balance, exchange_rate, holdings=holdings, user_id=user_id,
            macro_data=macro_data, target_cash_kr=target_cash_kr, target_cash_us=target_cash_us,
            reason=f"trailing_remaining({drawdown:.1f}% from {cur_high:,.0f}, trail {trail_pct:.0f}%)",
        )
        if result.executed:
            user_state.remaining_high.pop(ticker, None)
            user_state.partial_take_done.pop(ticker, None)
            cls._set_reentry_block(ticker, current_price, user_state)
        return result.executed

    # ── Profit-Taking ─────────────────────────────────────────────────────────────────

    @classmethod
    def _handle_profit_take_signal(
        cls, ticker: str, holding: HoldingSchema, profit_pct: float, take_profit_pct: float,
        sell_cooldown: dict, today: str, state, score: int,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float,
        sell_split_orders: dict = None,
    ) -> bool:
        """Handle profit-taking condition with split sell tracking. Applies cooldown. Returns execution status."""
        if sell_split_orders is None:
            sell_split_orders = {}
        if not (holding and profit_pct >= take_profit_pct):
            return False
        if sell_cooldown.get(ticker) == today:
            logger.info(f"⏭️ {ticker} Partial sell cooldown active (already took profit today). Re-evaluate tomorrow.")
            return False
        holding_qty = int(holding.quantity)
        if holding_qty <= 0:
            return False
        sell_qty = cls._get_sell_split_qty(ticker, holding_qty, sell_split_orders, today)
        if sell_qty <= 0:
            sell_split_orders.pop(ticker, None)
            return False
        result = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"take_profit_zone({profit_pct:.2f}%)", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=sell_qty, trigger_reason="take_profit",
        )
        if result.executed:
            cls._update_sell_split_state(ticker, sell_qty, sell_split_orders)
        sell_cooldown[ticker] = today  # Prevent same-day retry regardless of success/failure
        return result.executed

    # ── Sell Split Helpers ──────────────────────────────────────────────────────

    @classmethod
    def _get_sell_split_qty(cls, ticker: str, holding_qty: int, sell_split_orders: dict, today: str) -> int:
        """Calculate sell quantity for one tranche. Initializes SplitSellOrderState if needed."""
        if holding_qty <= 0:
            sell_split_orders.pop(ticker, None)
            return 0
        # S6: 장 마감 N분 전이면 잔여 트랜치를 합쳐 1회 처리 (당일 미완 방지)
        merge_min = SettingsService.get_int("STRATEGY_SELL_MERGE_BEFORE_CLOSE_MIN", 10)
        if merge_min > 0 and MarketHourService.minutes_to_close("KR" if is_kr(ticker) else "US") <= merge_min:
            sso_exist = sell_split_orders.get(ticker)
            remaining = min(holding_qty, int(sso_exist.remaining_qty)) if sso_exist else holding_qty
            if remaining > 0:
                logger.info(f"⏱ {ticker} 마감 {merge_min}분 전 — 분할매도 잔여 {remaining}주 일괄 처리")
                return remaining
        sso = sell_split_orders.get(ticker)
        if not sso:
            split_count = SettingsService.get_int("STRATEGY_SELL_SPLIT_COUNT", 5)
            tranche_qty = -(-holding_qty // split_count)  # ceil division
            sso = SplitSellOrderState(
                total_qty=holding_qty,
                remaining_qty=holding_qty,
                split_count=split_count,
                start_date=today,
                tranche_qty=tranche_qty,
            )
            sell_split_orders[ticker] = sso
        if sso.splits_done >= sso.split_count or sso.remaining_qty <= 0:
            return 0
        # 마지막 트랜치는 remaining_qty 전량, 이전 트랜치는 초기 고정값 사용
        if sso.splits_done == sso.split_count - 1:
            return sso.remaining_qty
        return sso.tranche_qty if sso.tranche_qty > 0 else -(-sso.remaining_qty // (sso.split_count - sso.splits_done))

    @classmethod
    def _update_sell_split_state(cls, ticker: str, sold_qty: int, sell_split_orders: dict) -> None:
        """Update split sell state after successful execution."""
        sso = sell_split_orders.get(ticker)
        if not sso:
            return
        sso.splits_done += 1
        sso.remaining_qty -= sold_qty
        if sso.remaining_qty <= 0:
            sell_split_orders.pop(ticker, None)

    # ── Committed Cash ────────────────────────────────────────────────────────

    @classmethod
    def _calculate_committed_cash(cls, split_orders: dict, market: str = None) -> float:
        """Sum remaining_qty * entry_price for pending split orders, filtered by market (KR/US/None=all)."""
        total = 0.0
        for ticker, so in (split_orders or {}).items():
            if market == 'KR' and not is_kr(ticker):
                continue
            if market == 'US' and is_kr(ticker):
                continue
            total += so.remaining_qty * so.entry_price
        return total

    # ── Cooldown ───────────────────────────────────────────────────────────────

    @classmethod
    def _is_buy_cooldown_active(cls, ticker: str, today: str, current_price: float, add_buy_cooldown: dict, gap_pct: float = 0.0) -> bool:
        """Determine if cooldown is active. Backward compat for old formats (str / no-timestamp).
        gap-aware: gap_pct ≥ HIGH_GAP_PCT 시 시간 단위 cooldown(짧음) 적용.
        Exception: allows rebuy if price drops -5% or more from buy price.
        """
        cd = add_buy_cooldown.get(ticker)
        if not cd:
            return False

        # B6: 매도 후 재진입 차단(kind='sell') — expire_days 경과 OR 매도가 대비 REENTRY_DROP% 하락 시 해제
        if not isinstance(cd, str) and getattr(cd, 'kind', 'buy') == 'sell':
            from services.config.settings_service import SettingsService as _SS
            drop = _SS.get_float("STRATEGY_REENTRY_DROP_PCT", -5.0)
            sell_px = float(getattr(cd, 'price', 0) or 0)
            if sell_px > 0 and current_price > 0 and current_price <= sell_px * (1 + drop / 100):
                return False
            try:
                elapsed = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(cd.date, "%Y-%m-%d")).days
            except (TypeError, ValueError):
                elapsed = 0
            return elapsed < int(getattr(cd, 'expire_days', 1) or 1)

        # 가격 -5% 예외는 모든 케이스 적용
        cd_price = cd if isinstance(cd, str) else getattr(cd, 'price', 0)
        if isinstance(cd_price, (int, float)) and cd_price > 0 and current_price <= cd_price * 0.95:
            return False

        # gap-aware: 갭 큰 경우 시간 단위 cooldown
        from services.config.settings_service import SettingsService
        high_gap_pct = SettingsService.get_float("STRATEGY_COOLDOWN_HIGH_GAP_PCT", 30.0)
        if gap_pct >= high_gap_pct and not isinstance(cd, str):
            cd_ts = getattr(cd, 'timestamp', 0) or 0
            if cd_ts > 0:
                cd_hours = SettingsService.get_int("STRATEGY_BUY_COOLDOWN_HOURS_HIGH_GAP", 1)
                elapsed_hours = (datetime.now(pytz.timezone("Asia/Seoul")).timestamp() - cd_ts) / 3600
                if elapsed_hours >= cd_hours:
                    return False
                return True

        # 기본: 일 단위 cooldown
        if isinstance(cd, str):                 # Legacy format: "YYYY-MM-DD"
            return cd == today
        if cd.date != today:                    # Different day -> cooldown expired
            return False
        return True

    # ── Add-Buy ─────────────────────────────────────────────────────────────

    @classmethod
    def _handle_add_buy_signal(
        cls, ticker: str, holding: HoldingSchema, profit_pct: float, stop_loss_pct: float,
        current_rsi: float, add_rsi_limit: float, add_score_limit: int, score: int,
        add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float,
        split_orders: dict = None,
    ) -> TradeResult:
        """Handle add-buy condition. Applies cooldown/RSI/score filters."""
        if not (holding and profit_pct <= -5.0 and profit_pct > stop_loss_pct):
            return TradeResult.no_op()
        if current_rsi >= add_rsi_limit:
            logger.info(f"⏭️ {ticker} Add-buy RSI overbought ({current_rsi:.1f} >= {add_rsi_limit}). Skip.")
            return TradeResult.no_op()
        if score > add_score_limit:
            logger.info(f"⏭️ {ticker} Add-buy score not met ({score} > {add_score_limit}). Skip.")
            return TradeResult.no_op()
        current_price_val = getattr(state, 'current_price', 0)
        if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
            logger.info(f"⏭️ {ticker} Add-buy cooldown active (already added today). Re-evaluate tomorrow.")
            return TradeResult.no_op()
        
        # [Manual Fix] Subtract committed cash for existing split orders
        market = 'KR' if is_kr(ticker) else 'US'
        committed = cls._calculate_committed_cash(split_orders, market)
        effective_cash = max(0.0, cash_balance - committed) if market == 'KR' else cash_balance
        if committed > 0 and market == 'KR':
            logger.info(f"💰 {ticker} Add-buy: committed={committed:,.0f}KRW, effective_cash={effective_cash:,.0f}KRW")
            cash_balance = effective_cash

        result = TradeExecutorService._execute_trade_v2(
            ticker, "buy", f"add_position({profit_pct:.2f}%)", profit_pct, True, score,
            current_price_val, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            trigger_reason="add_position",
        )
        if result.executed:
            add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price_val)
        return result

    # ── Split Buy ─────────────────────────────────────────────────────────────

    @classmethod
    def _init_split_order(cls, ticker, state, score, cash_balance, current_price, exchange_rate, market_total, today, split_orders) -> bool:
        """Initialize a new split order. Returns False if qty=0 or sector blocked."""
        sector = getattr(state, 'sector', '') or ''
        if sector in ('ETF', 'Others', 'Unclassified/ETF'):
            logger.info(f"⏭️ {ticker} ETF/Other sector new buy blocked (sector={sector}). Skip.")
            return False
        is_kr_flag = is_kr(ticker)
        
        # [Manual Fix] Subtract committed cash for existing split orders
        market = 'KR' if is_kr_flag else 'US'
        committed = cls._calculate_committed_cash(split_orders, market)
        
        usd_cash_krw = 0.0
        if not is_kr_flag:
            from services.trading.portfolio_service import PortfolioService as _PS
            usd_cash_krw = (_PS.get_usd_cash_balance() or 0) * exchange_rate
            usd_cash_krw = max(0.0, usd_cash_krw - (committed * exchange_rate if market == 'US' else 0)) # committed is in USD for US stocks
        else:
            cash_balance = max(0.0, cash_balance - committed)

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
    def _execute_split_tranche(cls, ticker: str, holding: Optional[HoldingSchema], score: int, reason_str: str, profit_pct: float, split_orders: dict,
                               current_price_val: float, market_total: float, cash_balance: float, exchange_rate: float,
                               holdings: list[HoldingSchema], user_id: str, macro_data: MacroDataSnapshot, target_cash_kr: float, target_cash_us: float,
                               add_buy_cooldown: dict, today: str) -> TradeResult:
        """Execute one tranche of a split order."""
        so = split_orders[ticker]
        remaining = so.remaining_qty
        splits_left = so.split_count - so.splits_done
        # Ceiling division to allocate more to earlier tranches: [2,2,1] pattern
        this_run_qty = -(-remaining // splits_left) if splits_left > 0 and remaining > 0 else remaining
        if this_run_qty <= 0:
            split_orders.pop(ticker, None)
            return TradeResult.no_op()
        result = TradeExecutorService._execute_trade_v2(
            ticker, "buy",
            f"score {score} [{reason_str}] ({so.splits_done+1}/{so.split_count} split)",
            profit_pct, bool(holding), score, current_price_val, market_total, cash_balance,
            exchange_rate, holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=this_run_qty, trigger_reason="score_buy",
        )
        if result.executed:
            so.splits_done += 1
            so.remaining_qty -= this_run_qty
            so.last_tranche_price = float(current_price_val or 0)
            so.last_tranche_date = today
            if so.remaining_qty <= 0:
                split_orders.pop(ticker, None)
            add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price_val)
        return result

    @classmethod
    def _handle_buy_split(
        cls, ticker: str, holding: Optional[HoldingSchema], score: int, reason_str: str, profit_pct: float,
        buy_max: int, add_buy_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list[HoldingSchema], user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
    ) -> TradeResult:
        """New/split buy logic. Caller must ensure score/holding condition gates."""
        has_pending_splits = ticker in split_orders
        current_price_val = getattr(state, 'current_price', 0)
        if not has_pending_splits:
            if cls._is_buy_cooldown_active(ticker, today, current_price_val, add_buy_cooldown):
                logger.info(f"⏭️ {ticker} New buy cooldown active (already bought today). Re-evaluate tomorrow.")
                return TradeResult.no_op()
            if not cls._init_split_order(ticker, state, score, cash_balance, current_price_val, exchange_rate, market_total, today, split_orders):
                return TradeResult.no_op()
            just_initialized = True
        else:
            just_initialized = False
            # B3: 트랜치 페이싱 — 다음 트랜치는 (전 트랜치가 대비 -N% 하락) OR (다음 거래일) 에만.
            # 없으면 1분 루프마다 실행돼 3분 만에 3트랜치 소진 → 분할의 시간 분산 효과 0.
            so = split_orders[ticker]
            drop_pct = SettingsService.get_float("STRATEGY_SPLIT_TRANCHE_DROP_PCT", -2.0)
            if so.last_tranche_date == today and so.last_tranche_price > 0 and current_price_val > 0:
                if current_price_val > so.last_tranche_price * (1 + drop_pct / 100):
                    logger.info(f"⏭️ {ticker} split tranche 대기: 당일 이미 체결, 가격 {current_price_val:,.0f} > 전 트랜치 {so.last_tranche_price:,.0f}×(1{drop_pct:+.0f}%)")
                    return TradeResult.no_op()
        result = cls._execute_split_tranche(
            ticker, holding, score, reason_str, profit_pct, split_orders,
            current_price_val, market_total, cash_balance, exchange_rate,
            holdings, user_id, macro_data, target_cash_kr, target_cash_us,
            add_buy_cooldown, today,
        )
        # 1차 트랜치 실패 시 split_orders entry 즉시 폐기 — 다음 루프에서 다시 init부터.
        # (없으면 entry_price만 남아 5일 만료까지 매 루프 재시도하며 환경변화 무반응)
        if just_initialized and not result.executed:
            split_orders.pop(ticker, None)
            logger.info(f"🧹 {ticker} 1차 트랜치 실패 → split_order 폐기 (다음 루프에서 재평가)")
        return result

    # ── Score-Based Sell ────────────────────────────────────────────────────────

    @classmethod
    def _handle_sell_signal(
        cls, ticker: str, holding: Optional[HoldingSchema], score: int, reason_str: str, profit_pct: float,
        sell_min: int, sell_cooldown: dict, today: str, state,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list[HoldingSchema], user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, split_orders: dict,
        sell_split_orders: dict = None,
    ) -> bool:
        """Score-based sell logic with split sell tracking. Caller must ensure score/holding condition gates."""
        if sell_split_orders is None:
            sell_split_orders = {}
        holding_qty = int(holding.quantity) if holding else 0
        if score >= sell_min and holding_qty > 0:
            if sell_cooldown.get(ticker) == today:
                # [Manual Fix] High score bypass for sell cooldown
                if score >= 90:
                    logger.info(f"🔓 {ticker} Sell cooldown unlocked: Score {score} is >= 90.")
                else:
                    logger.info(f"⏭️ {ticker} Partial sell cooldown active (already score-sold today). Re-evaluate tomorrow.")
                    return False
        split_orders.pop(ticker, None)  # Cancel remaining buy split orders
        if holding_qty <= 0:
            return False
        sell_qty = cls._get_sell_split_qty(ticker, holding_qty, sell_split_orders, today)
        if sell_qty <= 0:
            sell_split_orders.pop(ticker, None)
            return False
        result = TradeExecutorService._execute_trade_v2(
            ticker, "sell", f"score {score} [{reason_str}]", profit_pct, True, score,
            getattr(state, 'current_price', 0), market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=sell_qty, trigger_reason="score_sell",
        )
        if result.executed:
            cls._update_sell_split_state(ticker, sell_qty, sell_split_orders)
        sell_cooldown[ticker] = today  # Prevent same-day retry regardless of success/failure
        return result.executed

    # ── Score-Based Buy/Sell Combined ──────────────────────────────────────────────

    @classmethod
    def _handle_score_trade(
        cls, ticker: str, holding: Optional[HoldingSchema], score: int, reason_str: str, profit_pct: float,
        buy_max: int, sell_min: int, sell_cooldown: dict, add_buy_cooldown: dict,
        today: str, state, market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list[HoldingSchema], user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, split_orders: dict = None,
        sell_split_orders: dict = None,
    ) -> TradeResult:
        """Score-based buy/sell handling - delegates to sub-handlers."""
        if split_orders is None:
            split_orders = {}
        if sell_split_orders is None:
            sell_split_orders = {}
        has_pending_splits = ticker in split_orders
        if score <= buy_max and (not holding or has_pending_splits):
            # Score improved → cancel pending sell splits
            sell_split_orders.pop(ticker, None)
            return cls._handle_buy_split(
                ticker, holding, score, reason_str, profit_pct,
                buy_max, add_buy_cooldown, today, state,
                market_total, cash_balance, exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders,
            )
        if score >= sell_min and holding:
            executed = cls._handle_sell_signal(
                ticker, holding, score, reason_str, profit_pct,
                sell_min, sell_cooldown, today, state,
                market_total, cash_balance, exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders,
                sell_split_orders=sell_split_orders,
            )
            return TradeResult(executed=bool(executed))
        return TradeResult.no_op()

    # ── Single Signal Processing ────────────────────────────────────────────────────────

    @classmethod
    def _unpack_signal(cls, sig: SignalSchema, kr_total: float, us_total_krw: float) -> UnpackedSignal:
        """SignalSchema → UnpackedSignal. 순수 함수, I/O 없음."""
        ticker = sig.ticker
        holding = sig.holding
        reasons = sig.reasons
        profit_pct = 0.0
        if holding:
            buy_price = holding.buy_price
            ref_price = float((holding.current_price) or getattr(sig.state, 'current_price', 0))
            if buy_price > 0:
                profit_pct = (ref_price - buy_price) / buy_price * 100
        return UnpackedSignal(
            ticker=ticker,
            state=sig.state,
            holding=holding,
            score=sig.score,
            reason_str=", ".join(reasons),
            profit_pct=profit_pct,
            market_total=kr_total if is_kr(ticker) else us_total_krw,
            forced_sell=any(r == "stop_loss_hit" or r.startswith("uptrend_stop_loss_hit") for r in reasons),
        )

    @classmethod
    def _process_single_signal(
        cls, sig: SignalSchema, cfg: ExecutionConfig,
        sell_cooldown: dict, add_buy_cooldown: dict,
        holdings: list, user_id: str, kr_total: float, us_total_krw: float, cash_balance: float,
        macro_data: MacroDataSnapshot, target_cash_kr: float, target_cash_us: float,
        split_orders: dict = None, sell_split_orders: dict = None, trailing_high: dict = None,
        user_state: UserState = None, usd_cash: float = 0.0,
    ) -> tuple:
        """Process a single signal. Returns (executed: bool, ticker_or_None: Optional[str], spent_krw: float, spent_usd: float)."""
        u = cls._unpack_signal(sig, kr_total, us_total_krw)
        cls._log_signal_evaluation(u)
        return cls._route_signal(
            u, cfg, sell_cooldown, add_buy_cooldown, holdings, user_id,
            cash_balance, macro_data, target_cash_kr, target_cash_us,
            split_orders, sell_split_orders, trailing_high, user_state=user_state,
            usd_cash=usd_cash,
        )

    @classmethod
    def _log_signal_evaluation(cls, u: "UnpackedSignal") -> None:
        """신호 평가 결과 로그 출력."""
        stock_name = getattr(u.state, "name", "") or (u.holding.name if u.holding else "")
        dcf_val = getattr(u.state, 'dcf_value', None)
        dcf_str = f", DCF={dcf_val:,.0f}" if dcf_val and dcf_val > 0 else ""
        logger.info(f"🔍 Evaluated {u.ticker} ({stock_name}): Score={u.score}, RSI={getattr(u.state, 'rsi', 0):.1f}{dcf_str}, Reasons=[{u.reason_str}]")

    @classmethod
    def _route_signal(
        cls, u: "UnpackedSignal", cfg: ExecutionConfig,
        sell_cooldown: dict, add_buy_cooldown: dict,
        holdings: list, user_id: str, cash_balance: float,
        macro_data: MacroDataSnapshot, target_cash_kr: float, target_cash_us: float,
        split_orders: dict = None, sell_split_orders: dict = None, trailing_high: dict = None,
        user_state: UserState = None, usd_cash: float = 0.0,
    ) -> tuple:
        """강제매도/트레일링/익절/추매/점수매매 분기 라우터. (executed, ticker_or_None, spent_krw, spent_usd) 반환."""
        # [Manual Fix] Reset buy-spent tracker for v1 compatibility before handling each signal
        TradeExecutorService._last_buy_spent_krw = 0.0
        common_kwargs = dict(holdings=holdings, user_id=user_id, macro_data=macro_data, target_cash_kr=target_cash_kr, target_cash_us=target_cash_us)
        uptrend = cls._is_uptrend_dca_enabled()
        from services.strategy.crash_guard_service import CrashGuardService
        is_crash = CrashGuardService.is_crash(macro_data)
        # 가격이 stop_loss 임계 위로 회복했으면 연속손절 카운터 리셋
        if u.holding and not u.forced_sell:
            cls._reset_stop_loss_streak(u.ticker, user_state)
        if u.forced_sell and u.holding:
            # Uptrend: Crash 중엔 forced_sell 도 보류 (저점 매도 방지)
            if uptrend and is_crash:
                logger.info(f"⏸ {u.ticker} 강제매도 보류 (Crash 가드 활성)")
                return False, None, 0.0, 0.0
            if not cls._should_execute_stop_loss(u.ticker, u.profit_pct, macro_data, user_state, cfg.today):
                return False, None, 0.0, 0.0
            (split_orders or {}).pop(u.ticker, None)
            (sell_split_orders or {}).pop(u.ticker, None)
            result = cls._handle_forced_sell(
                u.ticker, u.holding, u.profit_pct,
                getattr(u.state, 'current_price', 0), u.market_total,
                cash_balance, cfg.exchange_rate, **common_kwargs,
            )
            if result.executed and user_state:
                cls._set_panic_lock(u.ticker, user_state, price=getattr(u.state, 'current_price', 0))
                cls._reset_uptrend_state_on_sell(u.ticker, user_state)
            return result.executed, u.ticker if result.executed else None, 0.0, 0.0
        current_price = getattr(u.state, 'current_price', 0)
        # ── Uptrend DCA 분기 ────────────────────────────────────────────────
        if uptrend and u.holding:
            # 1) 부분익절 (+15% 도달 시 50% 매도, 1회만)
            if cls._handle_uptrend_partial_take(
                u.ticker, u.holding, u.profit_pct, u.state, u.score,
                u.market_total, cash_balance, cfg.exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state,
            ):
                return True, u.ticker, 0.0, 0.0
            # 2) 잔여 trailing (-10% from remaining_high)
            if cls._handle_uptrend_remaining_trailing(
                u.ticker, u.holding, current_price,
                u.market_total, cash_balance, cfg.exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state,
            ):
                cls._reset_uptrend_state_on_sell(u.ticker, user_state)
                return True, u.ticker, 0.0, 0.0
            # 3) DCA 3단계 추매 (loss thresholds)
            dca_result = cls._handle_uptrend_dca_signal(
                u.ticker, u.holding, u.profit_pct, current_price,
                u.market_total, cash_balance, cfg.exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state,
                usd_cash=usd_cash,
            )
            if dca_result.executed:
                return True, u.ticker, dca_result.spent_krw, dca_result.spent_usd
            # 4) S5: 상대 약세 정리 (장기 보유 + 지수 대비 큰 부진 + 지수는 회복 중)
            rw_result = cls._handle_relative_weakness(
                u.ticker, u.holding, u.profit_pct, current_price,
                u.market_total, cash_balance, cfg.exchange_rate,
                holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state, cfg.today,
            )
            if rw_result.executed:
                return True, u.ticker, 0.0, 0.0
            # Uptrend 활성 시: trailing_stop (손실분) 완전 비활성. 익절/추가매수 등 다른 분기로
        else:
            # 레거시 trailing_stop (Uptrend 비활성시만)
            th = trailing_high if trailing_high is not None else {}
            cls._update_trailing_high(u.ticker, current_price, th)
            trail_result = cls._handle_trailing_stop(
                u.ticker, u.holding, current_price, th,
                u.market_total, cash_balance, cfg.exchange_rate, **common_kwargs,
            )
            if trail_result and trail_result.executed:
                return True, u.ticker, 0.0, 0.0
        # ticker별 mode×market×regime 기반 동적 파라미터 조회
        take_profit_pct = cls._get_take_profit_pct(u.ticker, macro_data)
        stop_loss_pct = cls._get_stop_loss_pct(u.ticker, macro_data)
        # Uptrend 활성 시 일반 take_profit/add_buy 분기는 Skip (부분익절/DCA 가 대체)
        if not uptrend:
            if cls._handle_profit_take_signal(u.ticker, u.holding, u.profit_pct, take_profit_pct, sell_cooldown, cfg.today, u.state, u.score, u.market_total, cash_balance, cfg.exchange_rate, sell_split_orders=sell_split_orders, **common_kwargs):
                return True, u.ticker, 0.0, 0.0
            current_rsi = getattr(u.state, 'rsi', 50.0)
            add_result = cls._handle_add_buy_signal(u.ticker, u.holding, u.profit_pct, stop_loss_pct, current_rsi, cfg.add_rsi_limit, cfg.add_score_limit, u.score, add_buy_cooldown, cfg.today, u.state, u.market_total, cash_balance, cfg.exchange_rate, split_orders=split_orders, **common_kwargs)
            if add_result.executed:
                return True, u.ticker, add_result.spent_krw, add_result.spent_usd
        # score-based 매매: Uptrend 활성 시에도 신규 매수/sell threshold 도달 매도는 가능 (단 Crash 보류)
        if uptrend and is_crash and u.holding:
            # Crash 중엔 score-based 매도도 보류 (신규 매수는 허용)
            return False, None, 0.0, 0.0
        is_score_sell = bool(u.holding) and u.score >= cfg.sell_min
        if uptrend and is_score_sell:
            # S3: Uptrend 에서 score 매도는 최소 수익 이상일 때만 — 손실/수수료 이하 구간 score 매도 금지
            min_profit = SettingsService.get_float("STRATEGY_UPTREND_SCORE_SELL_MIN_PROFIT", 3.0)
            if u.profit_pct < min_profit:
                logger.info(f"⏸ {u.ticker} score 매도 보류: 수익 {u.profit_pct:.2f}% < 최소 {min_profit:.1f}% (Uptrend)")
                return False, None, 0.0, 0.0
        result = cls._handle_score_trade(u.ticker, u.holding, u.score, u.reason_str, u.profit_pct, cfg.buy_max, cfg.sell_min, sell_cooldown, add_buy_cooldown, cfg.today, u.state, u.market_total, cash_balance, cfg.exchange_rate, holdings, user_id, macro_data, target_cash_kr, target_cash_us, split_orders=split_orders, sell_split_orders=sell_split_orders)
        if result.executed and user_state:
            cls._reset_uptrend_state_on_sell(u.ticker, user_state)
            if is_score_sell:
                cls._set_reentry_block(u.ticker, current_price, user_state)
        return result.executed, u.ticker if result.executed else None, result.spent_krw, result.spent_usd

    # ── S5: 상대 약세 정리 ───────────────────────────────────────────────────────

    @classmethod
    def _handle_relative_weakness(
        cls, ticker: str, holding, profit_pct: float, current_price: float,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float, user_state, today: str,
    ) -> TradeResult:
        """'우상향' 가정은 지수엔 맞아도 개별 종목엔 틀릴 수 있다.
        보유 ≥ MIN_DAYS AND (종목 수익률 − 지수 90d 수익률) ≤ UNDERPERF AND 지수 20d ≥ MIN(회복 중)
        → 보유의 SELL_RATIO 를 1회 정리 (dca_done[ticker]['rw_done'] 로 영속). Crash 중엔 보류."""
        if not holding or not user_state or current_price <= 0:
            return TradeResult.no_op()
        if SettingsService.get_int("STRATEGY_RELATIVE_WEAKNESS_ENABLED", 1) != 1:
            return TradeResult.no_op()
        ticker_dca = user_state.dca_done.get(ticker) or {}
        if ticker_dca.get("rw_done"):
            return TradeResult.no_op()
        kr = is_kr(ticker)
        idx90 = getattr(macro_data, "kospi_change_90d" if kr else "spx_change_90d", None) if macro_data else None
        idx20 = getattr(macro_data, "kospi_change_20d" if kr else "spx_change_20d", None) if macro_data else None
        if idx90 is None or idx20 is None:
            return TradeResult.no_op()
        if idx20 < SettingsService.get_float("STRATEGY_RELATIVE_WEAKNESS_INDEX_20D_MIN", 0.0):
            return TradeResult.no_op()
        underperf = profit_pct - idx90
        if underperf > SettingsService.get_float("STRATEGY_RELATIVE_WEAKNESS_UNDERPERF_PCT", -20.0):
            return TradeResult.no_op()
        from repositories.trade_history_repo import TradeHistoryRepo
        first_buy = TradeHistoryRepo.get_first_buy_date(ticker)
        if first_buy is None:
            return TradeResult.no_op()  # 매수 이력 없음(외부 매수) → 보유기간 판단 불가
        held_days = (datetime.now() - first_buy).days
        if held_days < SettingsService.get_int("STRATEGY_RELATIVE_WEAKNESS_MIN_DAYS", 90):
            return TradeResult.no_op()
        from services.strategy.crash_guard_service import CrashGuardService
        if CrashGuardService.is_crash(macro_data):
            logger.info(f"⏸ {ticker} 상대약세 정리 보류 (Crash 가드 활성)")
            return TradeResult.no_op()
        ratio = SettingsService.get_float("STRATEGY_RELATIVE_WEAKNESS_SELL_RATIO", 0.5)
        qty = int(holding.quantity)
        sell_qty = max(1, int(qty * ratio))
        result = TradeExecutorService._execute_trade_v2(
            ticker, "sell",
            f"relative_weakness(held {held_days}d, pnl {profit_pct:+.1f}% vs idx90 {idx90:+.1f}% = {underperf:+.1f}%p, idx20 {idx20:+.1f}%)",
            profit_pct, True, 0, current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=sell_qty, trigger_reason="relative_weakness",
        )
        if result.executed:
            ticker_dca["rw_done"] = today
            user_state.dca_done[ticker] = ticker_dca
            cls._set_reentry_block(ticker, current_price, user_state)
        return result

    # ── Trailing Stop ──────────────────────────────────────────────────────────

    @classmethod
    def _get_trailing_stop_pct(cls, ticker: str, macro_data: MacroDataSnapshot) -> float:
        """레짐별 트레일링 스탑 임계값 반환 (ticker × mode × market × regime fallback)."""
        regime = cls._resolve_regime_key(macro_data)
        default = -7.0 if regime == "BULL" else -5.0
        return cls._get_strategy_param("TRAILING_STOP_PCT", ticker, regime, default=default)

    @classmethod
    def _update_trailing_high(cls, ticker: str, current_price: float, trailing_high: dict) -> None:
        """고점 갱신. current_price > 기존 고점이면 업데이트. 순수 상태 변경, I/O 없음."""
        if current_price > 0:
            trailing_high[ticker] = max(trailing_high.get(ticker, 0.0), current_price)

    @classmethod
    def _handle_trailing_stop(
        cls, ticker: str, holding: HoldingSchema, current_price: float,
        trailing_high: dict,
        market_total: float, cash_balance: float, exchange_rate: float,
        holdings: list, user_id: str, macro_data: MacroDataSnapshot = None,
        target_cash_kr: float = 0.0, target_cash_us: float = 0.0,
    ) -> Optional[TradeResult]:
        """고점 대비 drawdown이 레짐별 임계값 초과 시 전량 즉시 매도.
        Returns TradeResult if triggered, None if not applicable."""
        if not holding or current_price <= 0:
            return None
        high = trailing_high.get(ticker, 0.0)
        if high <= 0:
            return None
        buy_price = float(holding.buy_price or 0)
        profit_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0
        max_profit_pct = (high - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        drawdown = (current_price - high) / high * 100

        # [수익 보존(Tight Stop) 로직] 최고점이 trigger% 이상 도달했다면, 방어막을 tight_stop%로 조임.
        tight_trigger = cls._get_strategy_param("TIGHT_STOP_TRIGGER_PCT", ticker, regime=None, default=1.5)
        if max_profit_pct >= tight_trigger:
            threshold = cls._get_strategy_param("TIGHT_STOP_PCT", ticker, regime=None, default=-1.0)
        else:
            threshold = cls._get_trailing_stop_pct(ticker, macro_data)

        if drawdown > threshold:
            return None
        logger.info(f"🔻 {ticker} Trailing stop: high={high:,.2f} current={current_price:,.2f} drawdown={drawdown:.1f}% (threshold={threshold:.1f}%, tight_trigger={tight_trigger:.1f}%)")
        return cls._handle_forced_sell(
            ticker, holding, profit_pct, current_price, market_total,
            cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, macro_data=macro_data,
            target_cash_kr=target_cash_kr, target_cash_us=target_cash_us,
            reason=f"trailing_stop({drawdown:.1f}% from high {high:,.0f})",
        )

    # ── Panic Lock (손절 후 재매수 차단) ─────────────────────────────────────────

    @staticmethod
    def _set_panic_lock(ticker: str, user_state: UserState, price: float = None) -> None:
        """손절 실행된 종목을 panic_locks에 등록하여 재매수 차단.
        B6: 손절가도 기록 → 재진입 시 +N% 회복 확인에 사용. {date, price} (레거시 문자열 호환)."""
        from datetime import datetime
        user_state.panic_locks[ticker] = {"date": datetime.now().strftime("%Y-%m-%d"), "price": float(price or 0)}
        logger.info(f"🔒 {ticker} panic_lock 설정 (손절 후 재매수 차단, 손절가 {float(price or 0):,.0f})")

    @staticmethod
    def _panic_lock_date(entry) -> str:
        return entry.get("date", "") if isinstance(entry, dict) else str(entry or "")

    @staticmethod
    def _clear_expired_panic_locks(user_state: UserState, expire_days: int = 3) -> None:
        """만료된 panic_locks 제거. 기본 3일 후 해제."""
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=expire_days)).strftime("%Y-%m-%d")
        expired = [t for t, d in user_state.panic_locks.items() if PositionService._panic_lock_date(d) <= cutoff]
        for t in expired:
            user_state.panic_locks.pop(t, None)
            logger.info(f"🔓 {t} panic_lock 해제 ({expire_days}일 경과)")

    # ── 3일 연속 손절 룰 (intra-day wick으로 인한 오손절 방지) ─────────────────

    @classmethod
    def _should_execute_stop_loss(
        cls, ticker: str, profit_pct: float, macro_data: MacroDataSnapshot,
        user_state: UserState, today: str,
    ) -> bool:
        """STOP_LOSS_CONSECUTIVE_DAYS 룰 적용.
        - threshold=0 → 즉시 손절 (기본 거동, 룰 비활성)
        - threshold=N (≥1) → N일 연속 손절 임계 초과 시에만 매도, 그 전엔 카운터만 누적 후 보류
        Returns True if sell should fire now, False if hold.
        """
        if user_state is None:
            return True
        if cls._is_uptrend_dca_enabled():
            # Uptrend 안전망 손절(-25%)은 별도의 긴 연속일수(기본 7거래일) 적용
            threshold_days = SettingsService.get_int("STRATEGY_UPTREND_STOP_LOSS_DAYS", 7)
        else:
            threshold_days = int(cls._get_strategy_param(
                "STOP_LOSS_CONSECUTIVE_DAYS", ticker, regime=None, default=0
            ))
        if threshold_days <= 0:
            return True

        streak = user_state.stop_loss_streak.get(ticker) or {}
        last_date = (streak.get("last_date") or "") if isinstance(streak, dict) else ""
        days = int(streak.get("days") or 0) if isinstance(streak, dict) else 0

        if last_date == today:
            pass  # 같은 날 재호출 — 카운트 유지
        else:
            days += 1

        user_state.stop_loss_streak[ticker] = {"days": days, "last_date": today}

        if days >= threshold_days:
            user_state.stop_loss_streak.pop(ticker, None)  # 매도 실행 직전 리셋
            return True

        logger.info(f"⏸ {ticker} 연속손절룰: {days}/{threshold_days}일 (PnL={profit_pct:.2f}% — 오늘 매도 보류)")
        return False

    @staticmethod
    def _reset_stop_loss_streak(ticker: str, user_state: UserState) -> None:
        """profit_pct가 stop_loss 임계 위로 회복 시 카운터 리셋."""
        if user_state is None:
            return
        if ticker in user_state.stop_loss_streak:
            logger.debug(f"🔄 {ticker} 연속손절 카운터 리셋 (가격 회복)")
            user_state.stop_loss_streak.pop(ticker, None)

    # ── Forced Sell (Stop-Loss) ────────────────────────────────────────────────

    @classmethod
    def _handle_forced_sell(
        cls, ticker: str, holding: HoldingSchema, profit_pct: float,
        current_price: float, market_total: float, cash_balance: float,
        exchange_rate: float, holdings: list, user_id: str,
        macro_data: MacroDataSnapshot, target_cash_kr: float, target_cash_us: float,
        reason: str = None,
    ) -> TradeResult:
        """Execute forced stop-loss sell at full quantity. No cooldown — stop-loss must always fire."""
        if reason is None:
            reason = f"stop_loss({profit_pct:.2f}%)"
        # trigger_reason: reason 텍스트 prefix로 구조화 매핑
        trig = "stop_loss"
        if reason.startswith("trailing_stop"):
            trig = "trailing_stop"
        elif reason.startswith("tight_stop"):
            trig = "tight_stop"
        return TradeExecutorService._execute_trade_v2(
            ticker, "sell", reason, profit_pct, True, 0,
            current_price, market_total, cash_balance, exchange_rate,
            holdings=holdings, user_id=user_id, holding=holding, macro=macro_data,
            target_cash_ratio_kr=target_cash_kr, target_cash_ratio_us=target_cash_us,
            forced_qty=holding.quantity, trigger_reason=trig,
        )

    # ── Unmonitored Holdings Check ─────────────────────────────────────────────

    @classmethod
    def _check_unmonitored_holdings(
        cls, prepared_signals: list[SignalSchema], holdings: list[HoldingSchema], user_id: str,
        kr_total: float, us_total_krw: float, cash_balance: float,
        cfg: ExecutionConfig, macro_data: MacroDataSnapshot,
        target_cash_kr: float, target_cash_us: float,
        sell_cooldown: dict, sell_split_orders: dict,
        user_state: UserState = None,
    ) -> tuple:
        """Stop-loss/profit-taking check for holdings outside monitoring universe (ETFs, etc.).
        Returns (trade_executed: bool, executed_tickers: set)."""
        monitored_tickers = {sig.ticker for sig in prepared_signals}
        trade_executed = False
        executed_tickers = set()
        for h in holdings:
            if not h.ticker or h.ticker in monitored_tickers:
                continue
            if not h.quantity or h.quantity <= 0:
                continue
            executed, ticker = cls._process_unmonitored_holding(
                h, kr_total, us_total_krw, cash_balance, cfg, macro_data,
                target_cash_kr, target_cash_us, sell_cooldown, sell_split_orders,
                holdings, user_id, user_state=user_state,
            )
            if executed and ticker:
                executed_tickers.add(ticker)
                trade_executed = True
        return trade_executed, executed_tickers

    @classmethod
    def _process_unmonitored_holding(
        cls, h: HoldingSchema, kr_total: float, us_total_krw: float,
        cash_balance: float, cfg: "ExecutionConfig", macro_data: "MacroDataSnapshot",
        target_cash_kr: float, target_cash_us: float,
        sell_cooldown: dict, sell_split_orders: dict,
        holdings: list[HoldingSchema], user_id: str,
        user_state: UserState = None,
    ) -> tuple[bool, Optional[str]]:
        """단일 미감시 종목의 가격 조회 및 손절/익절 실행. (executed, ticker_or_None) 반환."""
        ticker = h.ticker
        buy_price = float(h.buy_price or 0)
        cached_state = MarketDataService.get_state(ticker)
        cached_price = getattr(cached_state, 'current_price', 0) if cached_state else 0
        current_price = cached_price if cached_price > 0 else float(h.current_price or 0)
        if buy_price <= 0 or current_price <= 0:
            return False, None
        profit_pct = (current_price - buy_price) / buy_price * 100
        logger.info(f"🔍 [Unmonitored holding] {ticker} ({h.name or ''}): PnL={profit_pct:.1f}%")
        market_total = kr_total if is_kr(ticker) else us_total_krw
        common_kwargs = dict(
            holdings=holdings, user_id=user_id, macro_data=macro_data,
            target_cash_kr=target_cash_kr, target_cash_us=target_cash_us,
        )
        # Uptrend 모드: 미감시 종목도 감시 종목과 동일 규칙(-25%×N일 안전망 손절, Crash 보류,
        # +10% 부분익절, 잔여 trailing). 레거시 -5~-8% 손절/익절은 적용하지 않는다.
        if cls._is_uptrend_dca_enabled():
            return cls._process_unmonitored_holding_uptrend(
                h, profit_pct, current_price, cached_state, market_total, cash_balance,
                cfg, macro_data, target_cash_kr, target_cash_us, holdings, user_id, user_state,
            )
        # ticker별 mode×market×regime 기반 동적 파라미터 조회
        take_profit_pct = cls._get_take_profit_pct(ticker, macro_data)
        stop_loss_pct = cls._get_stop_loss_pct(ticker, macro_data)
        if profit_pct <= stop_loss_pct:
            # 3일 연속 룰 — 임계 미달이면 카운터만 누적 후 보류
            if not cls._should_execute_stop_loss(ticker, profit_pct, macro_data, user_state, cfg.today):
                return False, None
            result = cls._handle_forced_sell(
                ticker, h, profit_pct, current_price, market_total,
                cash_balance, cfg.exchange_rate, **common_kwargs,
            )
            return result.executed, ticker if result.executed else None
        else:
            cls._reset_stop_loss_streak(ticker, user_state)
        if profit_pct >= take_profit_pct:
            executed = cls._handle_profit_take_signal(
                ticker, h, profit_pct, take_profit_pct,
                sell_cooldown, cfg.today, cached_state, 0,
                market_total, cash_balance, cfg.exchange_rate,
                sell_split_orders=sell_split_orders, **common_kwargs,
            )
            return executed, ticker if executed else None
        return False, None

    @classmethod
    def _process_unmonitored_holding_uptrend(
        cls, h: HoldingSchema, profit_pct: float, current_price: float, cached_state,
        market_total: float, cash_balance: float, cfg: "ExecutionConfig",
        macro_data: "MacroDataSnapshot", target_cash_kr: float, target_cash_us: float,
        holdings: list[HoldingSchema], user_id: str, user_state: UserState = None,
    ) -> tuple[bool, Optional[str]]:
        """[Uptrend] 미감시 보유 종목 처리 — 감시 종목의 _route_signal 매도 규칙과 동일.
        1) 안전망 손절: profit ≤ UPTREND_STOP_LOSS_PCT → N일 연속 룰 + Crash 보류 후 전량 매도
        2) 부분익절 (+N% → RATIO 매도, 1회)
        3) 잔여 trailing
        DCA 추매는 하지 않는다 (미감시 종목은 유니버스 밖 — 신규 자본 투입 X)."""
        from types import SimpleNamespace
        from services.strategy.crash_guard_service import CrashGuardService
        ticker = h.ticker
        state = cached_state if cached_state is not None else SimpleNamespace(current_price=current_price, rsi=50.0)
        common_kwargs = dict(
            holdings=holdings, user_id=user_id, macro_data=macro_data,
            target_cash_kr=target_cash_kr, target_cash_us=target_cash_us,
        )
        uptrend_sl = uptrend_stop_loss_pct(getattr(cached_state, "atr_pct", None) if cached_state else None)
        if uptrend_sl < 0 and profit_pct <= uptrend_sl:
            if CrashGuardService.is_crash(macro_data):
                logger.info(f"⏸ {ticker} [Unmonitored] 안전망 손절 보류 (Crash 가드 활성)")
                return False, None
            if not cls._should_execute_stop_loss(ticker, profit_pct, macro_data, user_state, cfg.today):
                return False, None
            result = cls._handle_forced_sell(
                ticker, h, profit_pct, current_price, market_total,
                cash_balance, cfg.exchange_rate,
                reason=f"uptrend_stop_loss({profit_pct:.2f}%<={uptrend_sl:.0f}%)", **common_kwargs,
            )
            if result.executed and user_state:
                cls._set_panic_lock(ticker, user_state, price=current_price)
                cls._reset_uptrend_state_on_sell(ticker, user_state)
            return result.executed, ticker if result.executed else None
        cls._reset_stop_loss_streak(ticker, user_state)
        if cls._handle_uptrend_partial_take(
            ticker, h, profit_pct, state, 0,
            market_total, cash_balance, cfg.exchange_rate,
            holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state,
        ):
            return True, ticker
        if cls._handle_uptrend_remaining_trailing(
            ticker, h, current_price,
            market_total, cash_balance, cfg.exchange_rate,
            holdings, user_id, macro_data, target_cash_kr, target_cash_us, user_state,
        ):
            cls._reset_uptrend_state_on_sell(ticker, user_state)
            return True, ticker
        return False, None

    # ── Batch Signal Execution ────────────────────────────────────────────────────────

    @classmethod
    def _expire_split_orders(cls, split_orders: dict) -> None:
        """TTL 초과 split_orders 제거. start_date 기준 N일 경과 항목 삭제."""
        expire_days = SettingsService.get_int("STRATEGY_SPLIT_EXPIRE_DAYS", 5)
        today = datetime.now(pytz.timezone('Asia/Seoul')).date()
        expired = [
            t for t, so in split_orders.items()
            if (today - datetime.strptime(so.start_date, "%Y-%m-%d").date()).days >= expire_days
        ]
        for t in expired:
            split_orders.pop(t, None)
            logger.info(f"⏰ {t} split_order TTL {expire_days}일 만료 → 제거")

    @staticmethod
    def _cleanup_expired_cooldowns(sell_cooldown: dict, add_buy_cooldown: dict, today: str) -> None:
        """오늘 날짜가 아닌 만료된 쿨다운 항목 제거. dict 무한 누적 방지."""
        expired_sell = [t for t, d in sell_cooldown.items() if d != today]
        for t in expired_sell:
            sell_cooldown.pop(t)
            logger.debug(f"🧹 {t} sell_cooldown 만료 항목 제거")

        def _buy_expired(cd) -> bool:
            if isinstance(cd, str):
                return cd != today
            days = int(getattr(cd, 'expire_days', 1) or 1)
            if days <= 1:
                return cd.date != today
            try:
                return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(cd.date, "%Y-%m-%d")).days >= days
            except (TypeError, ValueError):
                return True
        expired_buy = [t for t, cd in add_buy_cooldown.items() if _buy_expired(cd)]
        for t in expired_buy:
            add_buy_cooldown.pop(t)
            logger.debug(f"🧹 {t} add_buy_cooldown 만료 항목 제거")

    # ── Mode × Market × Regime 파라미터 조회 ────────────────────────────────────
    #
    # 키 컨벤션: STRATEGY_{MODE}_{MARKET}_{PARAM}_{REGIME}
    #   MODE   = TOP100 | WATCHLIST  (kr_strategy_mode / us_strategy_mode setting)
    #   MARKET = KR | US
    #   PARAM  = TAKE_PROFIT_PCT | STOP_LOSS_PCT | TRAILING_STOP_PCT
    #          | TIGHT_STOP_TRIGGER_PCT | TIGHT_STOP_PCT | STOP_LOSS_CONSECUTIVE_DAYS
    #   REGIME = BULL | NEUTRAL | BEAR | WEAK_BEAR  (TIGHT_*, CONSECUTIVE_DAYS는 omit)
    #
    # Fallback 체인 (위→아래):
    #   1) STRATEGY_{MODE}_{MARKET}_{PARAM}_{REGIME}
    #   2) STRATEGY_{MARKET}_{PARAM}_{REGIME}
    #   3) STRATEGY_{PARAM}_{REGIME}    (레거시)
    #   4) default

    @classmethod
    def _resolve_regime_key(cls, macro_data: MacroDataSnapshot = None) -> str:
        """레짐 → 키 suffix (BULL/NEUTRAL/BEAR/WEAK_BEAR) 통일 변환."""
        regime = (macro_data.market_regime.status if macro_data and macro_data.market_regime else "Neutral").upper()
        score = (macro_data.market_regime.regime_score if macro_data and macro_data.market_regime else -1)
        if regime == "BEAR" and score >= 36:
            return "WEAK_BEAR"
        return regime  # BULL / NEUTRAL / BEAR

    @staticmethod
    def _get_mode_for_market(market: str) -> str:
        """현재 시장(KR/US)의 strategy mode 조회. 기본 top100."""
        from repositories.settings_repo import SettingsRepo
        key = "kr_strategy_mode" if market.upper() == "KR" else "us_strategy_mode"
        return (SettingsRepo.get(key) or "top100").lower()

    @classmethod
    def _get_strategy_param(
        cls, param: str, ticker: str, regime: str = None, default: float = 0.0
    ) -> float:
        """Mode × Market × Regime fallback 체인으로 strategy param 조회."""
        market = "KR" if is_kr(ticker) else "US"
        mode = cls._get_mode_for_market(market).upper()
        param = param.upper()
        regime_u = regime.upper() if regime else None

        candidates = []
        if regime_u:
            candidates.append(f"STRATEGY_{mode}_{market}_{param}_{regime_u}")
            candidates.append(f"STRATEGY_{market}_{param}_{regime_u}")
            candidates.append(f"STRATEGY_{param}_{regime_u}")
        else:
            candidates.append(f"STRATEGY_{mode}_{market}_{param}")
            candidates.append(f"STRATEGY_{market}_{param}")
            candidates.append(f"STRATEGY_{param}")

        for key in candidates:
            val = SettingsService.get_setting(key)
            if val is None or val == "":
                continue
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
        return default

    @classmethod
    def _get_take_profit_pct(cls, ticker: str, macro_data: MacroDataSnapshot = None) -> float:
        regime = cls._resolve_regime_key(macro_data)
        default = {"BULL": 10.0, "WEAK_BEAR": 5.0, "BEAR": 3.0}.get(regime, 7.0)
        return cls._get_strategy_param("TAKE_PROFIT_PCT", ticker, regime, default=default)

    @classmethod
    def _get_stop_loss_pct(cls, ticker: str, macro_data: MacroDataSnapshot = None) -> float:
        regime = cls._resolve_regime_key(macro_data)
        default = {"WEAK_BEAR": -3.0, "BEAR": -5.0}.get(regime, -5.0)
        return cls._get_strategy_param("STOP_LOSS_PCT", ticker, regime, default=default)

    # ── Backward-compat thin wrappers (regime-only, no ticker) ────────────────
    # ExecutionConfig.{take_profit_pct, stop_loss_pct} 필드 호환을 위해 유지.
    # ticker-aware lookup이 도입된 _route_signal/_handle_trailing_stop 외 호출처에서만 사용.
    @classmethod
    def _get_take_profit_pct_by_regime(cls, macro_data: MacroDataSnapshot = None) -> float:
        regime = cls._resolve_regime_key(macro_data)
        key = f"STRATEGY_TAKE_PROFIT_PCT_{regime}"
        default = {"BULL": 10.0, "WEAK_BEAR": 5.0, "BEAR": 3.0}.get(regime, 7.0)
        return SettingsService.get_float(key, default)

    @classmethod
    def _get_stop_loss_pct_by_regime(cls, macro_data: MacroDataSnapshot = None) -> float:
        regime = cls._resolve_regime_key(macro_data)
        key = f"STRATEGY_STOP_LOSS_PCT_{regime}"
        default = {"WEAK_BEAR": -3.0, "BEAR": -5.0}.get(regime, -5.0)
        return SettingsService.get_float(key, default)

    @classmethod
    def _load_execution_config(cls, macro_data: MacroDataSnapshot = None) -> ExecutionConfig:
        """SettingsService에서 실행 설정값 일괄 조회 후 ExecutionConfig 반환.
        take_profit_pct/stop_loss_pct는 레짐 기준 fallback값 — 실제 분기는 _route_signal/_handle_trailing_stop에서 ticker별 _get_strategy_param 사용."""
        return ExecutionConfig(
            buy_max=SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 30),
            sell_min=SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 70),
            take_profit_pct=cls._get_take_profit_pct_by_regime(macro_data),
            stop_loss_pct=cls._get_stop_loss_pct_by_regime(macro_data),
            add_rsi_limit=SettingsService.get_float("STRATEGY_ADD_BUY_RSI_LIMIT", 60.0),
            add_score_limit=SettingsService.get_int("STRATEGY_ADD_BUY_SCORE_LIMIT", 55),
            exchange_rate=MacroService.get_exchange_rate(),
            today=datetime.now(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d'),
        )

    @classmethod
    def _sort_signals_by_priority(cls, signals: list[SignalSchema], split_orders: dict) -> list[SignalSchema]:
        """신호 우선순위 정렬. 신규 미보유(0) > 기존 보유(1) > split tranche 연속(2). 순수 함수."""
        def _priority(s: SignalSchema):
            t = s.ticker
            if not s.holding and t not in split_orders:
                return (0, s.score)   # 신규 후보는 점수 낮은(강한 BUY) 순 — B1 랭킹 캡과 결합
            if t in split_orders:
                return (2, s.score)
            return (1, s.score)
        return sorted(signals, key=_priority)

    @classmethod
    def _deduct_loop_cash(
        cls, ticker: str, spent_krw: float, spent_usd: float,
        cash_balance: float, usd_cash: float,
    ) -> tuple[float, float]:
        """루프 내 체결 후 현금 차감. Returns (updated_cash_balance, updated_usd_cash)."""
        if spent_krw > 0 and is_kr(ticker):
            cash_balance = max(0.0, cash_balance - spent_krw)
            logger.info(f"💰 Loop KRW cash updated: -{spent_krw:,.0f}KRW → remaining {cash_balance:,.0f}KRW")
        elif spent_usd > 0 and not is_kr(ticker):
            usd_cash = max(0.0, usd_cash - spent_usd)
            logger.info(f"💵 Loop USD cash updated: -${spent_usd:,.2f} → remaining ${usd_cash:,.2f}")
        return cash_balance, usd_cash

    @classmethod
    def _execute_collected_signals(
        cls, user_id: str, prepared_signals: list[SignalSchema], holdings: list[HoldingSchema],
        kr_total: float, us_total_krw: float, cash_balance: float,
        target_cash_kr: float, target_cash_us: float, macro_data: MacroDataSnapshot,
        user_state: UserState = None, usd_cash: float = 0.0,
    ) -> tuple:
        """Execute actual orders based on collected signals.
        Returns (trade_executed: bool, executed_tickers: set)."""
        cfg = cls._load_execution_config(macro_data)
        if user_state is None:
            user_state = UserState()
        sell_cooldown: dict = user_state.sell_cooldown
        add_buy_cooldown: dict = user_state.add_buy_cooldown
        split_orders: dict = user_state.split_orders
        sell_split_orders: dict = user_state.sell_split_orders
        trailing_high: dict = user_state.trailing_high
        trade_executed = False
        executed_tickers = set()

        cls._expire_split_orders(split_orders)
        cls._cleanup_expired_cooldowns(sell_cooldown, add_buy_cooldown, cfg.today)
        cls._clear_expired_panic_locks(user_state)
        
        # 미보유 종목의 과거 고점(trailing_high) 찌꺼기 일괄 정리
        active_tickers = {h.ticker for h in holdings}
        for t in list(trailing_high.keys()):
            if t not in active_tickers:
                trailing_high.pop(t, None)

        # 보유=0인 ticker의 유령 sell_split_orders 정리 (이미 청산됐는데 분할매도 상태만 남은 경우)
        for t in list(sell_split_orders.keys()):
            if t not in active_tickers:
                sell_split_orders.pop(t, None)
                logger.info(f"🧹 {t} sell_split_order 정리: 보유 0 (이미 청산됨)")

        prepared_signals = cls._sort_signals_by_priority(prepared_signals, split_orders)

        # B1: 루프당 신규 종목 매수 상한 (점수 하위 N개만). 임계 통과 종목이 절반이어도 무차별 매수 방지
        max_new_buys = SettingsService.get_int("STRATEGY_MAX_NEW_BUYS_PER_LOOP", 2)
        new_buys_done = 0
        for sig in prepared_signals:
            is_new_candidate = (not sig.holding) and (sig.ticker not in split_orders) and sig.score <= cfg.buy_max
            if is_new_candidate and max_new_buys > 0 and new_buys_done >= max_new_buys:
                logger.info(f"⏭️ {sig.ticker} 신규 매수 스킵: 루프당 상한 {max_new_buys}건 도달 (score={sig.score})")
                continue
            sig_executed, sig_ticker, sig_spent_krw, sig_spent_usd = cls._process_single_signal(
                sig, cfg, sell_cooldown, add_buy_cooldown,
                holdings, user_id, kr_total, us_total_krw, cash_balance,
                macro_data, target_cash_kr, target_cash_us,
                split_orders=split_orders, sell_split_orders=sell_split_orders,
                trailing_high=trailing_high, user_state=user_state, usd_cash=usd_cash,
            )
            if sig_executed and sig_ticker:
                executed_tickers.add(sig_ticker)
                if is_new_candidate:
                    new_buys_done += 1
            trade_executed = sig_executed or trade_executed
            cash_balance, usd_cash = cls._deduct_loop_cash(sig.ticker, sig_spent_krw, sig_spent_usd, cash_balance, usd_cash)

        unmon_executed, unmon_tickers = cls._check_unmonitored_holdings(
            prepared_signals, holdings, user_id, kr_total, us_total_krw, cash_balance,
            cfg, macro_data, target_cash_kr, target_cash_us, sell_cooldown, sell_split_orders,
            user_state=user_state,
        )
        trade_executed = unmon_executed or trade_executed
        executed_tickers |= unmon_tickers
        return trade_executed, executed_tickers

    # ── Asset Management Execution ─────────────────────────────────────────────

    @classmethod
    def execute_buy_budget(
        cls, user_id: str, budget_krw: float, budget_usd: float, signals: list[SignalSchema],
        user_state: UserState = None, gap_pct: float = 0.0,
        macro_data: MacroDataSnapshot = None,
    ) -> None:
        """Buy top-scored tickers within given budget (called by AssetManagementService).
        Iterates signals in score ascending order until budget is exhausted.
        gap_pct 클수록 cooldown 단축 + per-trade 비중 확대.
        macro_data 는 Frozen(VIX>50) 매수 가드 판정용 — 없으면 가드 미적용."""
        from services.market.macro_service import MacroService
        exchange_rate = MacroService.get_exchange_rate()
        kst = pytz.timezone("Asia/Seoul")
        now_dt = datetime.now(kst)
        today = now_dt.strftime("%Y-%m-%d")
        now_ts = now_dt.timestamp()
        add_buy_cooldown = user_state.add_buy_cooldown if user_state else {}
        sorted_signals = sorted(signals, key=lambda s: s.score)
        for sig in sorted_signals:
            if budget_krw <= 0 and budget_usd <= 0:
                break
            ticker = sig.ticker
            current_price = getattr(sig.state, 'current_price', 0)
            if current_price <= 0:
                continue
            if cls._is_buy_cooldown_active(ticker, today, current_price, add_buy_cooldown, gap_pct=gap_pct):
                logger.info(f"⏭️ [BuyBudget] {ticker} 쿨다운 활성 → 스킵")
                continue
            is_kr_ticker = is_kr(ticker)
            market_total = budget_krw if is_kr_ticker else budget_usd * exchange_rate
            cash_balance = budget_krw if is_kr_ticker else 0.0
            result = TradeExecutorService._execute_trade_v2(
                ticker=ticker, side="buy",
                reason=f"budget_buy [{', '.join(sig.reasons)}]",
                profit_pct=0.0, is_holding=bool(sig.holding), score=sig.score,
                current_price=current_price, market_total=market_total,
                cash_balance=cash_balance, exchange_rate=exchange_rate,
                user_id=user_id, holding=sig.holding, macro=macro_data, gap_pct=gap_pct,
                trigger_reason="budget_buy",
            )
            if result.executed:
                add_buy_cooldown[ticker] = BuyCooldownEntry(date=today, price=current_price, timestamp=now_ts)
                budget_krw = max(0.0, budget_krw - result.spent_krw)
                budget_usd = max(0.0, budget_usd - result.spent_usd)
                logger.info(f"[BuyBudget] {ticker} executed. KR budget left={budget_krw:,.0f} US budget left=${budget_usd:,.2f}")

    @classmethod
    def execute_sell_for_cash(
        cls, user_id: str, need_krw: float, need_usd: float, candidates: list[HoldingSchema],
        user_state: UserState = None,
    ) -> None:
        """Sell profitable holdings to meet cash target (called by AssetManagementService).
        Iterates candidates in profit-rate descending order until need is met."""
        from services.trading.portfolio_service import PortfolioService as _PS
        today = datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y-%m-%d")
        sell_cooldown = user_state.sell_cooldown if user_state else {}
        exchange_rate = MacroService.get_exchange_rate()
        # 총자산/현금 계산 (Slack 알림용)
        all_holdings = _PS.load_portfolio(user_id)
        cash_krw = _PS.load_cash(user_id)
        usd_cash = _PS.get_usd_cash_balance()
        kr_market = sum((h.current_price or 0) * h.quantity for h in all_holdings if is_kr(h.ticker))
        us_market_krw = sum((h.current_price or 0) * h.quantity * exchange_rate for h in all_holdings if not is_kr(h.ticker))
        kr_total = kr_market + cash_krw
        us_total_krw = us_market_krw + usd_cash * exchange_rate
        for holding in candidates:
            if need_krw <= 0 and need_usd <= 0:
                break
            ticker = holding.ticker
            if sell_cooldown.get(ticker) == today:
                logger.info(f"⏭️ [SellForCash] {ticker} 매도 쿨다운 활성 → 스킵")
                continue
            current_price = holding.current_price or 0
            if current_price <= 0:
                continue
            buy_price = float(holding.buy_price or 0)
            profit_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0.0
            
            if profit_pct < 2.0:
                logger.info(f"⏭️ [SellForCash] {ticker} 수익률({profit_pct:.2f}%) 2.0% 미만 → 스킵")
                continue

            is_kr_ticker = is_kr(ticker)
            market_total = kr_total if is_kr_ticker else us_total_krw
            cash_balance = cash_krw if is_kr_ticker else usd_cash * exchange_rate
            result = TradeExecutorService._execute_trade_v2(
                ticker=ticker, side="sell",
                reason="asset_management_cash_rebalance",
                profit_pct=profit_pct, is_holding=True, score=70,
                current_price=current_price, market_total=market_total,
                cash_balance=cash_balance, exchange_rate=exchange_rate,
                user_id=user_id, holding=holding,
                trigger_reason="asset_mgmt_sell",
            )
            if result.executed:
                sell_cooldown[ticker] = today
                need_krw = max(0.0, need_krw - result.spent_krw)
                need_usd = max(0.0, need_usd - result.spent_usd)
                logger.info(f"[SellForCash] {ticker} executed. KR need left={need_krw:,.0f} US need left=${need_usd:,.2f}")
