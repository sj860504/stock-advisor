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

# 캐시 TTL (초)
TOP10_CACHE_TTL_SEC = 6 * 60 * 60


class TradingStrategyService:
    """
    사용자의 투자 전략에 따른 매매 시그널 판단 및 실행 서비스
    (오케스트레이터 - 실제 로직은 하위 서비스로 위임)
    """
    _enabled = False

    # ── 활성화 상태 관리 ─────────────────────────────────────────────────────

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
        """앱 시작 시 저장된 enabled 상태를 복원합니다. JSON → DB 1회 마이그레이션 포함."""
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
        """strategy_state.json → DB 1회 마이그레이션. JSON 파일이 없으면 스킵."""
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
                logger.info(f"✅ strategy_state 마이그레이션 완료: user={key}")
            if "_enabled" in old and SettingsService.get_setting("STRATEGY_ENABLED", None) is None:
                SettingsService.set_setting("STRATEGY_ENABLED", "true" if old["_enabled"] else "false")
            overrides = old.get("_global", {}).get("top_weight_overrides")
            if overrides and SettingsService.get_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", None) is None:
                SettingsService.set_setting("STRATEGY_TOP_WEIGHT_OVERRIDES", json.dumps(overrides, ensure_ascii=False))
            if migrated:
                bak = json_path + ".migrated"
                os.rename(json_path, bak)
                logger.info(f"📦 마이그레이션 완료. JSON 백업: {bak}")
        except Exception as e:
            logger.warning(f"⚠️ strategy_state JSON 마이그레이션 실패: {e}")

    # ── 상태 저장/로드 ────────────────────────────────────────────────────────

    @classmethod
    def _load_state(cls, user_id: str = "sean") -> dict:
        """DB에서 user_id 전략 상태 로드."""
        from repositories.strategy_state_repo import StrategyStateRepo
        user_state = StrategyStateRepo.load(user_id)
        return {user_id: user_state} if user_state else {user_id: {"panic_locks": {}, "sell_cooldown": {}, "add_buy_cooldown": {}, "tick_trade": {}, "split_orders": {}}}

    @classmethod
    def _save_state(cls, state: dict) -> None:
        """state dict에서 user별 상태를 DB에 저장."""
        from repositories.strategy_state_repo import StrategyStateRepo
        for user_id, user_state in state.items():
            if isinstance(user_state, dict):
                StrategyStateRepo.save(user_id, user_state)

    # ── 공개 API 위임 래퍼 ───────────────────────────────────────────────────

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, market_cash_ratio: float = None, market_total_krw: float = 0.0) -> tuple:
        """개별 종목 투자 점수 계산 (SignalService 위임)"""
        return SignalService.calculate_score(ticker, state, holding, macro, user_state, cash_balance, market_cash_ratio, market_total_krw)

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, cash_balance: float, exchange_rate: float, market_total_krw: float = 0.0) -> dict:
        """외부에서 개별 종목 분석 결과를 받을 수 있도록 공개된 인터페이스 (SignalService 위임)"""
        return SignalService.analyze_ticker(ticker, state, holding, macro, user_state, cash_balance, exchange_rate, market_total_krw)

    @classmethod
    def get_sector_rebalance_status(cls, user_id: str = "sean") -> dict:
        """섹터 비중 현황 및 리밸런싱 필요 종목 반환 (SectorRebalancerService 위임)"""
        return SectorRebalancerService.get_sector_rebalance_status(user_id)

    @classmethod
    def run_sector_rebalance(cls, user_id: str = "sean") -> dict:
        """주 1회 섹터 그룹 비중 리밸런싱 (SectorRebalancerService 위임)"""
        return SectorRebalancerService.run_sector_rebalance(user_id)

    @classmethod
    def get_top_weight_overrides(cls) -> dict:
        """티커별 사용자 가중치 오버라이드 조회 (TradeExecutorService 위임)"""
        return TradeExecutorService.get_top_weight_overrides()

    @classmethod
    def set_top_weight_overrides(cls, overrides: dict) -> dict:
        """티커별 사용자 가중치 오버라이드 저장 (TradeExecutorService 위임)"""
        return TradeExecutorService.set_top_weight_overrides(overrides)

    # ── 자산 계산 ─────────────────────────────────────────────────────────────

    @classmethod
    def _log_intramarket_cash_ratio(cls, holdings: list, cash_balance: float, usd_cash: float, exchange_rate: float, target_cash_kr: float, target_cash_us: float) -> None:
        """각 시장별 현금 비중을 로그로 출력 (경고만, 자동 매도 없음)"""
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
            f"📊 [포트폴리오 비중] "
            f"🇰🇷 주식 {kr_stock_ratio:.1%} / 현금 {kr_cash_ratio:.1%} (목표 현금 {target_cash_kr:.1%}) | "
            f"🇺🇸 주식 {us_stock_ratio:.1%} / 현금 {us_cash_ratio:.1%} (목표 현금 {target_cash_us:.1%})"
        )
        if kr_cash_ratio < target_cash_kr - 0.05 and kr_total > 0:
            logger.warning(f"⚠️ KR 현금 부족 ({kr_cash_ratio:.1%} < 목표 {target_cash_kr:.1%}). 익절 후 현금 확보 권장.")
        if us_cash_ratio < target_cash_us - 0.05 and us_total_usd > 0:
            logger.warning(f"⚠️ US 현금 부족 ({us_cash_ratio:.1%} < 목표 {target_cash_us:.1%}). 익절 후 현금 확보 권장.")

    # ── 틱 매매 ──────────────────────────────────────────────────────────────

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
        """틱매매 매도 조건 확인 및 실행"""
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
        """틱매매 매수(초기/추가) 조건 확인 및 실행"""
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
        """tick_trade 상태를 반환하되 날짜가 바뀌면 초기화."""
        trade_state = user_state.get(
            "tick_trade",
            {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}
        )
        if trade_state.get("date") != today:
            trade_state = {"date": today, "second_done": False, "last_sell_price": None, "price_window": []}
        return trade_state

    @classmethod
    def _update_price_window(cls, trade_state: dict, current_price: float) -> tuple:
        """1시간 가격 윈도우를 업데이트하고 (price_window, low_1h) 반환."""
        now_ts = datetime.now().timestamp()
        pw = [p for p in trade_state.get("price_window", []) if p[0] >= now_ts - 3600]
        pw.append([now_ts, float(current_price)])
        trade_state["price_window"] = pw
        low_1h = min((p[1] for p in pw), default=float(current_price))
        return pw, low_1h

    @classmethod
    def _execute_tick_eod_sell(cls, ticker: str, holding: dict, holdings: list, current_price: float, trade_state: dict, user_state: dict, tick_state: dict) -> bool:
        """장 마감 직전 틱매매 EOD 전량 매도. 성공 시 True 반환."""
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
        """틱매매 장중 매도/매수 조건 실행. Returns executed."""
        tranche = min(cash_balance, max(0.0, market_total * SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.2))) / 2
        buy_price = float(holding.get("buy_price", 1)) if holding and float(holding.get("buy_price", 1)) > 0 else 1.0
        pnl_pct = (getattr(state, 'current_price', 0) - buy_price) / buy_price * 100 if holding else 0
        executed = cls._evaluate_tick_sell_conditions(ticker, holding, state, pnl_pct, SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0), SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0), trade_state) if holding else False
        if not executed and tranche > 0:
            executed = cls._evaluate_tick_buy_conditions(ticker, tranche, state, holding, pnl_pct, SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0), trade_state, low_1h, SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0))
        return executed

    @classmethod
    def _run_tick_trade(cls, user_id: str, holdings: list, kr_total: float, us_total_krw: float, cash_balance: float) -> bool:
        """하루 1종목 틱매매 (진입/청산/유지)"""
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

    # ── 유니버스 관리 ─────────────────────────────────────────────────────────

    @classmethod
    def _update_target_universe(cls, user_id: str) -> set:
        """Top 100 변경 감지 및 유니버스 정리"""
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
        logger.info(f"🧹 Top 100 변경 감지: 현재 유니버스 {len(target_universe)}개 (KR={len(all_kr)}, US={len(all_us)})")
        return target_universe

    # ── 포트폴리오 리포트 ─────────────────────────────────────────────────────

    @classmethod
    def _send_portfolio_report(cls, user_id: str, before_snapshot: dict) -> None:
        """매매 전후 잔고를 비교하여 변동이 있으면 포트폴리오 리포트 전송"""
        try:
            from services.notification.report_service import ReportService
            PortfolioService.sync_with_kis(user_id)
            latest_holdings = PortfolioService.load_portfolio(user_id)
            summary = PortfolioService.get_last_balance_summary()
            latest_cash = PortfolioService.load_cash(user_id)

            after_snapshot = {h["ticker"]: h.get("quantity", 0) for h in latest_holdings}
            if before_snapshot == after_snapshot:
                logger.info("ℹ️ 체결 변경 없음. 포트폴리오 리포트 전송 스킵.")
                return

            states = MarketDataService.get_all_states()
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
            is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
            if is_kr_open and not is_us_open:
                report_holdings = filter_kr(latest_holdings)
            elif is_us_open and not is_kr_open:
                report_holdings = filter_us(latest_holdings)
            else:
                report_holdings = latest_holdings
            msg = ReportService.format_portfolio_report(report_holdings, latest_cash, states, summary)
            AlertService.send_slack_alert(msg)
        except Exception as e:
            logger.warning(f"⚠️ 포트폴리오 리포트 전송 실패: {e}")

    # ── sell_all_and_rebuy ────────────────────────────────────────────────────

    @classmethod
    def _build_sell_rebuy_result(cls, success_count: int, fail_count: int, failed_tickers: list, strategy_error: str = None) -> dict:
        """sell_all_and_rebuy 결과 dict를 빌드해 반환."""
        if strategy_error is None:
            return {
                "status": "success",
                "message": f"전량 매도 및 전략 재매수 완료 (매도 성공: {success_count}, 실패: {fail_count})",
                "sold": success_count,
                "failed": fail_count,
                "failed_tickers": failed_tickers or None,
            }
        return {
            "status": "partial",
            "message": f"매도 완료 (성공: {success_count}, 실패: {fail_count}), 전략 실행 실패",
            "sold": success_count,
            "failed": fail_count,
            "failed_tickers": failed_tickers or None,
            "strategy_error": strategy_error,
        }

    @classmethod
    def sell_all_and_rebuy(cls, user_id: str = "sean") -> dict:
        """보유 종목 전량 매도 후 전략대로 재매수."""
        logger.info("🔄 보유 종목 전량 매도 후 전략 재매수 시작")
        holdings = PortfolioService.sync_with_kis(user_id)
        if not holdings:
            return {"status": "success", "message": "보유 종목이 없습니다.", "sold": 0, "failed": 0}
        logger.info(f"📊 보유 종목 {len(holdings)}개 확인")
        success_count, fail_count, failed_tickers = OrderService.execute_mass_sell(holdings)
        PortfolioService.sync_with_kis(user_id)
        try:
            cls.run_strategy(user_id)
            logger.info("✅ 전략 실행 완료")
            return cls._build_sell_rebuy_result(success_count, fail_count, failed_tickers)
        except Exception as e:
            logger.error(f"❌ 전략 실행 중 오류: {e}")
            return cls._build_sell_rebuy_result(success_count, fail_count, failed_tickers, str(e))

    # ── 전략 실행 ─────────────────────────────────────────────────────────────

    @classmethod
    def _init_strategy_user_state(cls, state: dict, user_id: str) -> dict:
        """state 딕셔너리에서 user_id 섹션을 초기화(또는 복원)하여 반환."""
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
        """시그널 수집 + 집행 + 틱매매를 수행하고 매매 실행 여부 반환."""
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
        """전체 전략 실행 루프"""
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
        logger.info("✅ 전략 실행 및 매매 판단 완료.")
        if trade_executed:
            cls._send_portfolio_report(user_id, before_snapshot)

    # ── 대기 목록 / 기회 조회 ────────────────────────────────────────────────

    @classmethod
    def _build_waiting_list_entry(cls, ticker: str, ticker_state, score: int, reasons: list) -> dict:
        """대기 목록 개별 항목 dict 생성."""
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
        """매매 대기 목록 조회 (BUY/SELL 시그널 종목)"""
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
        """스크립트 호환성을 위한 get_waiting_list 별칭"""
        return cls.get_waiting_list(user_id)

    # ── 수동 매도 ────────────────────────────────────────────────────────────

    @classmethod
    def execute_sell(cls, ticker: str, quantity: int = 0, user_id: str = "sean") -> dict:
        """수동 매도 실행"""
        holdings = PortfolioService.sync_with_kis(user_id)
        holding = next((h for h in holdings if h['ticker'] == ticker), None)

        if not holding:
            return {"status": "failed", "msg": "보유 주식이 아닙니다."}

        max_qty = (getattr(holding, "quantity", None) if not isinstance(holding, dict) else holding.get("quantity", None))
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty

        logger.info(f"manual sell execution: {ticker} {quantity} qty")

        order_result = KisService.send_order(ticker, quantity, 0, "sell")
        if order_result.get("status") == "success":
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=holding.get('current_price', 0),
                result_msg="Manual Sell Execution",
                strategy_name="manual"
            )
        return order_result
