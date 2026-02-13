import json
import os
from config import Config
from typing import Optional
from datetime import datetime, timedelta
import pytz
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.market_data_service import MarketDataService # 추가
from services.market.market_hour_service import MarketHourService
from services.market.data_service import DataService
from services.kis.kis_service import KisService
from services.market.stock_meta_service import StockMetaService
from services.notification.alert_service import AlertService
from services.config.settings_service import SettingsService
from services.trading.order_service import OrderService
from utils.logger import get_logger

logger = get_logger("strategy_service")

class TradingStrategyService:
    """
    사용자의 투자 전략에 따른 매매 시그널 판단 및 실행 서비스
    """
    _state_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'strategy_state.json')
    _enabled = False # 기본값: 비활성화 (사용자 승인 필요)
    _top10_cache = {"timestamp": 0, "tickers": set()}

    # 전략 설정 상수 (SettingsService 연동을 위해 클래스 변수 제거 또는 프로퍼티화)
    # 여기서는 메서드 내에서 호출하도록 변경

    # 가중치 설정
    WEIGHTS = {
        'RSI_OVERSOLD': 20, 'RSI_OVERBOUGHT': -15,
        'DIP_BUY_5PCT': 15, 'SURGE_SELL_5PCT': -15,
        'SUPPORT_EMA': 10, 'RESISTANCE_EMA': -10,
        'ADD_POSITION_LOSS': 10, 'GOLDEN_CROSS_DROP': -15,
        'PANIC_MARKET_BUY': 30, 'PROFIT_TAKE_TARGET': -30,
        'BULL_MARKET_SECTOR': 15, 'CASH_PENALTY': -15,
        # DCF 기반 가치평가 가중치
        'DCF_UNDERVALUE_HIGH': 25,   # DCF 대비 20% 이상 저평가
        'DCF_UNDERVALUE_MID': 15,    # DCF 대비 10~20% 저평가
        'DCF_UNDERVALUE_LOW': 10,    # DCF 대비 5~10% 저평가
        'DCF_FAIR_VALUE': 5,         # DCF ±5% (적정가)
        'DCF_OVERVALUE_LOW': -10,    # DCF 대비 5~15% 고평가
        'DCF_OVERVALUE_HIGH': -20,   # DCF 대비 15% 이상 고평가
    }

    @classmethod
    def set_enabled(cls, enabled: bool):
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        cls._enabled = enabled
        logger.info(f"⚙️ Trading Strategy Engine {'ENABLED' if enabled else 'DISABLED'}")

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._enabled
    
    @classmethod
    def _load_state(cls):
        if os.path.exists(cls._state_path):
            with open(cls._state_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    @classmethod
    def _save_state(cls, state):
        with open(cls._state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    @classmethod
    def _get_ticker_market(cls, ticker: str) -> str:
        return "KR" if ticker.isdigit() else "US"

    @classmethod
    def _get_ticker_sector(cls, ticker: str, holding: Optional[dict] = None) -> str:
        if holding and holding.get("sector"):
            return holding["sector"]
        meta = StockMetaService.get_stock_meta(ticker)
        return meta.sector if meta and meta.sector else "Others"

    @classmethod
    def _get_holding_value(cls, holding: dict) -> float:
        price = holding.get("current_price") or holding.get("buy_price") or 0
        if price <= 0:
            state = MarketDataService.get_state(holding.get("ticker", ""))
            if state and state.current_price:
                price = state.current_price
        return max(0.0, float(price)) * float(holding.get("quantity", 0))

    @classmethod
    def _is_panic_market(cls, macro: dict) -> bool:
        vix = macro.get("vix", 20.0)
        fng = macro.get("fear_greed", 50)
        return vix >= 25 or fng <= 30

    @classmethod
    def _passes_allocation_limits(
        cls,
        ticker: str,
        add_value: float,
        holdings: list,
        total_assets: float,
        cash_balance: float,
        holding: Optional[dict] = None
    ) -> tuple:
        """시장/섹터 비중 제한 검사"""
        if total_assets <= 0:
            return True, []

        market = cls._get_ticker_market(ticker)
        sector = cls._get_ticker_sector(ticker, holding)

        market_values = {"KR": 0.0, "US": 0.0}
        sector_values = {}

        for h in holdings:
            if h.get("quantity", 0) <= 0:
                continue
            value = cls._get_holding_value(h)
            if value <= 0:
                continue
            mkt = cls._get_ticker_market(h["ticker"])
            sec = cls._get_ticker_sector(h["ticker"], h)
            market_values[mkt] = market_values.get(mkt, 0.0) + value
            sector_values[sec] = sector_values.get(sec, 0.0) + value

        # 추가 매수 반영
        market_values[market] = market_values.get(market, 0.0) + add_value
        sector_values[sector] = sector_values.get(sector, 0.0) + add_value

        target_market_kr = SettingsService.get_float("STRATEGY_TARGET_MARKET_RATIO_KR", 0.3)
        target_market_us = SettingsService.get_float("STRATEGY_TARGET_MARKET_RATIO_US", 0.4)
        max_sector = SettingsService.get_float("STRATEGY_MAX_SECTOR_RATIO", 0.3)

        reasons = []
        if market == "KR" and target_market_kr > 0:
            ratio = market_values["KR"] / total_assets
            if ratio > target_market_kr:
                reasons.append(f"시장비중초과(KR {ratio:.2%} > {target_market_kr:.2%})")
        if market == "US" and target_market_us > 0:
            ratio = market_values["US"] / total_assets
            if ratio > target_market_us:
                reasons.append(f"시장비중초과(US {ratio:.2%} > {target_market_us:.2%})")
        if max_sector > 0:
            ratio = sector_values.get(sector, 0.0) / total_assets
            if ratio > max_sector:
                reasons.append(f"섹터비중초과({sector} {ratio:.2%} > {max_sector:.2%})")

        return len(reasons) == 0, reasons

    @classmethod
    def _get_global_state(cls) -> dict:
        state = cls._load_state()
        if "_global" not in state:
            state["_global"] = {}
        return state

    @classmethod
    def get_top_weight_overrides(cls) -> dict:
        """티커별 사용자 가중치 오버라이드 조회"""
        state = cls._get_global_state()
        global_state = state.get("_global", {})
        return global_state.get("top_weight_overrides", {})

    @classmethod
    def set_top_weight_overrides(cls, overrides: dict) -> dict:
        """티커별 사용자 가중치 오버라이드 저장"""
        state = cls._get_global_state()
        state["_global"]["top_weight_overrides"] = overrides or {}
        cls._save_state(state)
        return state["_global"]["top_weight_overrides"]

    @classmethod
    def _get_top10_market_cap_tickers(cls) -> set:
        """미국/한국 시가총액 상위 10개 티커 캐시 반환"""
        now = datetime.now().timestamp()
        if now - cls._top10_cache["timestamp"] < 6 * 60 * 60:
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

    @classmethod
    def _is_near_market_close(cls, ticker: str, minutes: int = 5) -> bool:
        if ticker.isdigit():
            tz = pytz.timezone("Asia/Seoul")
            now = datetime.now(tz)
            close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
            return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        end_h, end_m = (20, 0) if allow_extended else (16, 0)
        close_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        return now.weekday() < 5 and (close_time - timedelta(minutes=minutes)) <= now <= close_time

    @classmethod
    def _run_tick_trade(cls, user_id: str, holdings: list, total_assets: float, cash_balance: float) -> bool:
        """
        하루 1종목 틱매매
        - 초기 진입: 최근 1시간 최저가 기준
        - 재진입: 직전 매도 체결가 대비 -1%
        - 청산: +1% 익절 / -5% 손절 / 장마감 전 전량 현금화
        - 추가매수: 평균단가 대비 -3% 시 1회
        장마감 전 전량 현금화
        """
        if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1:
            return False

        ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
        if not ticker:
            return False

        MarketDataService.register_ticker(ticker)
        state = MarketDataService.get_state(ticker)
        if not state or state.current_price <= 0:
            return False

        # 시장 시간 체크
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if ticker.isdigit():
            if not MarketHourService.is_kr_market_open():
                return False
        else:
            if not MarketHourService.is_us_market_open(allow_extended=allow_extended):
                return False

        tick_state = cls._load_state()
        if user_id not in tick_state:
            tick_state[user_id] = {}
        user_state = tick_state[user_id]
        now_ts = datetime.now().timestamp()
        today_key = datetime.now().strftime("%Y-%m-%d")
        trade_state = user_state.get("tick_trade", {"date": today_key, "second_done": False, "last_sell_price": None, "price_window": []})
        if trade_state.get("date") != today_key:
            trade_state = {"date": today_key, "second_done": False, "last_sell_price": None, "price_window": []}

        # 1시간 가격 윈도우 관리
        price_window = trade_state.get("price_window", [])
        price_window.append([now_ts, float(state.current_price)])
        one_hour_ago = now_ts - 3600
        price_window = [p for p in price_window if p[0] >= one_hour_ago]
        trade_state["price_window"] = price_window
        low_1h = min((p[1] for p in price_window), default=float(state.current_price))

        holding = next((h for h in holdings if h["ticker"] == ticker), None)

        # 장마감 전 전량 현금화
        close_min = SettingsService.get_int("STRATEGY_TICK_CLOSE_MINUTES", 5)
        if holding and cls._is_near_market_close(ticker, close_min):
            qty = int(holding.get("quantity", 0))
            if qty > 0:
                res = KisService.send_order(ticker, qty, 0, "sell")
                if res.get("status") == "success":
                    OrderService.record_trade(ticker, "sell", qty, state.current_price, "Tick EOD Close", "tick_strategy")
                    AlertService.send_slack_alert(
                        f"🔴 **[SELL] {ticker}, {holding.get('name','')}, 점수: 0, 가격: {state.current_price:,.2f}, "
                        f"등락률: {state.change_rate:.2f}%, 수량: {qty}주, 수익율: 0.00%"
                    )
                    trade_state["second_done"] = False
                    trade_state["last_sell_price"] = float(state.current_price)
                    user_state["tick_trade"] = trade_state
                    tick_state[user_id] = user_state
                    cls._save_state(tick_state)
                    return True
            return False

        entry_pct = SettingsService.get_float("STRATEGY_TICK_ENTRY_PCT", -1.0)
        add_pct = SettingsService.get_float("STRATEGY_TICK_ADD_PCT", -3.0)
        tp_pct = SettingsService.get_float("STRATEGY_TICK_TAKE_PROFIT_PCT", 1.0)
        sl_pct = SettingsService.get_float("STRATEGY_TICK_STOP_LOSS_PCT", -5.0)
        cash_ratio = SettingsService.get_float("STRATEGY_TICK_CASH_RATIO", 0.2)

        # 여유 현금 2분할
        budget = max(0.0, total_assets * cash_ratio)
        tranche = min(cash_balance, budget) / 2 if budget > 0 else 0
        if tranche <= 0:
            return False

        executed = False
        qty = 0

        # 보유 중: +1% 익절 / -5% 손절 / -3% 추가매수
        if holding:
            buy_price = float(holding.get("buy_price", 0) or 0)
            hold_qty = int(holding.get("quantity", 0) or 0)
            if buy_price > 0 and hold_qty > 0:
                pnl_pct = (state.current_price - buy_price) / buy_price * 100
                if pnl_pct >= tp_pct or pnl_pct <= sl_pct:
                    res = KisService.send_order(ticker, hold_qty, 0, "sell")
                    if res.get("status") == "success":
                        reason = "Tick TP" if pnl_pct >= tp_pct else "Tick SL"
                        OrderService.record_trade(ticker, "sell", hold_qty, state.current_price, reason, "tick_strategy")
                        AlertService.send_slack_alert(
                            f"🔴 **[SELL] {ticker}, {holding.get('name','')}, 점수: 0, 가격: {state.current_price:,.2f}, "
                            f"등락률: {state.change_rate:.2f}%, 수량: {hold_qty}주, 수익율: {pnl_pct:.2f}%"
                        )
                        trade_state["second_done"] = False
                        trade_state["last_sell_price"] = float(state.current_price)
                        executed = True
                elif pnl_pct <= add_pct and not trade_state.get("second_done"):
                    qty = int(tranche // state.current_price) if state.current_price > 0 else 0
                    if qty > 0:
                        res = KisService.send_order(ticker, qty, 0, "buy")
                        if res.get("status") == "success":
                            OrderService.record_trade(ticker, "buy", qty, state.current_price, "Tick Add2", "tick_strategy")
                            AlertService.send_slack_alert(
                                f"🔵 **[BUY] {ticker}, {holding.get('name','')}, 점수: 0, 가격: {state.current_price:,.2f}, "
                                f"등락률: {state.change_rate:.2f}%, 수량: {qty}주"
                            )
                            trade_state["second_done"] = True
                            executed = True
        else:
            # 미보유:
            # 1) 직전 매도 체결가가 있으면 해당 가격 대비 -1% 재진입
            # 2) 없으면 최근 1시간 최저가 근처에서 초기 진입
            last_sell = trade_state.get("last_sell_price")
            reentry_price = float(last_sell) * (1 + entry_pct / 100.0) if last_sell else None
            entry_triggered = False
            if reentry_price is not None:
                entry_triggered = float(state.current_price) <= reentry_price
            else:
                entry_triggered = float(state.current_price) <= low_1h * 1.001

            if entry_triggered:
                qty = int(tranche // state.current_price) if state.current_price > 0 else 0
                if qty > 0:
                    res = KisService.send_order(ticker, qty, 0, "buy")
                    if res.get("status") == "success":
                        reason = "Tick ReEntry -1%" if reentry_price is not None else "Tick Entry (1h low)"
                        OrderService.record_trade(ticker, "buy", qty, state.current_price, reason, "tick_strategy")
                        AlertService.send_slack_alert(
                            f"🔵 **[BUY] {ticker}, {getattr(state,'name','')}, 점수: 0, 가격: {state.current_price:,.2f}, "
                            f"등락률: {state.change_rate:.2f}%, 수량: {qty}주"
                        )
                        trade_state["second_done"] = False
                        executed = True

        user_state["tick_trade"] = trade_state
        tick_state[user_id] = user_state
        cls._save_state(tick_state)
        return executed

    @classmethod
    def run_strategy(cls, user_id: str = "sean"):
        """전체 전략 실행 루프 (정령화된 버전)"""
        if not cls.is_enabled():
            logger.debug(f"⏳ Trading Strategy is currently DISABLED. Skipping analysis.")
            return

        logger.info(f"🚀 Running Trading Strategy for {user_id}...")
        
        # 1. 기초 데이터 확보 (KIS 잔고, 매크로, 환율)
        holdings = PortfolioService.sync_with_kis(user_id)
        macro_data = MacroService.get_macro_data()
        exchange_rate = MacroService.get_exchange_rate()
        
        state = cls._load_state()
        if user_id not in state: state[user_id] = {}
        user_state = state[user_id]
        if 'panic_locks' not in user_state: user_state['panic_locks'] = {}
        
        # 총 자산 및 현금 계산
        cash_balance = PortfolioService.load_cash(user_id)
        total_market_value = sum(h['current_price'] * h['quantity'] for h in holdings)
        total_assets = total_market_value + cash_balance
        
        # 2. [Phase 1] 데이터 준비 상태 확인 및 점수 수집
        all_states = MarketDataService.get_all_states()
        # WebSocket 업데이트와 동시 접근 시 dict 크기 변경 예외를 막기 위해 스냅샷 순회
        all_state_items = list(all_states.items())
        prepared_signals = []
        
        for ticker, ticker_state in all_state_items:
            # 사용자가 강조한 데이터 우선 원칙 적용
            if not ticker_state.is_ready:
                logger.debug(f"⏳ {ticker} is not ready (missing data or warm-up in progress). Skipping.")
                continue
            
            holding = next((h for h in holdings if h['ticker'] == ticker), None)
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance)
            
            prepared_signals.append({
                "ticker": ticker,
                "state": ticker_state,
                "holding": holding,
                "score": score,
                "reasons": reasons
            })
            
        logger.info(f"📊 Signal collection complete. {len(prepared_signals)} stocks are ready for trading decision.")
        
        # 3. [Phase 2] 준비된 시그널 일괄 처리 및 매매 집행
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        before_snapshot = {h["ticker"]: h.get("quantity", 0) for h in holdings}
        
        trade_executed = False
        for sig in prepared_signals:
            ticker = sig['ticker']
            ticker_state = sig['state']
            holding = sig['holding']
            score = sig['score']
            reasons = sig['reasons']
            
            reason_str = ", ".join(reasons)
            logger.info(f"🔍 Evaluated {ticker}: Score={score}, RSI={ticker_state.rsi:.1f}, Reasons=[{reason_str}]")
            
            # 실제 매매 호출
            profit_pct = 0.0
            if holding:
                buy_price = holding['buy_price']
                profit_pct = (ticker_state.current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

            if score >= buy_threshold:
                executed = cls._execute_trade_v2(
                    ticker,
                    "buy",
                    f"점수 {score} [{reason_str}]",
                    profit_pct,
                    holding is not None,
                    score,
                    ticker_state.current_price,
                    total_assets,
                    cash_balance,
                    exchange_rate,
                    holdings=holdings,
                    user_id=user_id,
                    holding=holding,
                    macro=macro_data
                )
                trade_executed = trade_executed or bool(executed)
            elif score <= sell_threshold:
                if holding:
                    executed = cls._execute_trade_v2(
                        ticker,
                        "sell",
                        f"점수 {score} [{reason_str}]",
                        profit_pct,
                        True,
                        score,
                        ticker_state.current_price,
                        total_assets,
                        cash_balance,
                        exchange_rate,
                        holdings=holdings,
                        user_id=user_id,
                        holding=holding,
                        macro=macro_data
                    )
                    trade_executed = trade_executed or bool(executed)

        # 4. 별도 틱매매 프로세스 실행 (하루 1종목)
        try:
            tick_executed = cls._run_tick_trade(user_id, holdings, total_assets, cash_balance)
            trade_executed = trade_executed or bool(tick_executed)
        except Exception as e:
            logger.warning(f"⚠️ Tick trading process error: {e}")
            
        cls._save_state(state)
        logger.info("✅ 전략 실행 및 매매 판단 완료.")

        # 매매가 실제로 실행된 경우에만 즉시 포트폴리오 리포트 전송
        if trade_executed:
            try:
                from services.notification.report_service import ReportService
                # 최신 잔고 동기화 후 리포트 전송
                PortfolioService.sync_with_kis(user_id)
                latest_holdings = PortfolioService.load_portfolio(user_id)
                summary = PortfolioService.get_last_balance_summary()
                latest_cash = float(summary.get("prvs_rcdl_excc_amt") or PortfolioService.load_cash(user_id) or 0)
                after_snapshot = {h["ticker"]: h.get("quantity", 0) for h in latest_holdings}
                if before_snapshot == after_snapshot:
                    logger.info("ℹ️ 체결 변경 없음. 전략 종료 즉시 포트폴리오 리포트 전송 스킵.")
                    return
                states = MarketDataService.get_all_states()
                msg = ReportService.format_portfolio_report(latest_holdings, latest_cash, states, summary)
                AlertService.send_slack_alert(msg)
            except Exception as e:
                logger.warning(f"⚠️ 포트폴리오 리포트 전송 실패: {e}")

    @classmethod
    def get_waiting_list(cls, user_id: str = "sean"):
        """매매 대기 목록 조회 (BUY/SELL 시그널 종목)"""
        all_states = MarketDataService.get_all_states()
        all_state_items = list(all_states.items())
        holdings = PortfolioService.load_portfolio(user_id) # load_inventory -> load_portfolio 오타 수정
        macro_data = MacroService.get_macro_data()
        
        # 설정값 로드
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        
        waiting_list = []
        
        # 임시 상태 로드
        state = cls._load_state()
        user_state = state.get(user_id, {})
        
        # 자산 가치 대략 추정 (점수 계산에 필요)
        # 정확한 계산을 위해서는 PortfolioService.sync_with_kis가 필요하지만, 조회용이므로 DB값 사용
        # total_assets, cash_balance = ... (생략하고 0으로 처리하거나 기본값 사용)
        total_assets = 10000000 # 임시
        cash_balance = 5000000  # 임시
        
        for ticker, ticker_state in all_state_items:
            holding = next((h for h in holdings if h['ticker'] == ticker), None)
            
            # 점수 계산 (단순화된 버전 또는 전체 로직 사용)
            score, reasons = cls.calculate_score(ticker, ticker_state, holding, macro_data, user_state, total_assets, cash_balance)
            
            if score >= buy_threshold or score <= sell_threshold:
                action = "BUY" if score >= buy_threshold else "SELL"
                waiting_list.append({
                    "ticker": ticker,
                    "name": ticker_state.ticker, # 이름 정보가 state에 있다면 사용
                    "current_price": ticker_state.current_price,
                    "score": score,
                    "action": action,
                    "reasons": reasons,
                    "rsi": ticker_state.rsi
                })
                
        return sorted(waiting_list, key=lambda x: x['score'], reverse=True)

    @classmethod
    def get_opportunities(cls, user_id: str = "sean"):
        """스크립트 호환성을 위한 get_waiting_list 별칭"""
        return cls.get_waiting_list(user_id)

    @classmethod
    def execute_sell(cls, ticker: str, quantity: int = 0, user_id: str = "sean"):
        """수동 매도 실행"""
        # 보유 수량 확인
        holdings = PortfolioService.sync_with_kis(user_id)
        holding = next((h for h in holdings if h['ticker'] == ticker), None)
        
        if not holding:
            return {"status": "failed", "msg": "보유 주식이 아닙니다."}
            
        max_qty = holding['quantity']
        if quantity <= 0 or quantity > max_qty:
            quantity = max_qty # 전량 매도
            
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        logger.info(f"manual sell execution: {ticker} {quantity} qty")
        
        # 실제 주문
        res = KisService.send_order(ticker, quantity, 0, "sell")
        
        if res['status'] == 'success':
            # 매매 내역 저장
            OrderService.record_trade(
                ticker=ticker,
                order_type="sell",
                quantity=quantity,
                price=holding.get('current_price', 0), # 현재가
                result_msg="Manual Sell Execution",
                strategy_name="manual"
            )
            
        return res

    @classmethod
    def analyze_ticker(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float) -> dict:
        """외부에서 개별 종목 분석 결과를 받을 수 있도록 공개된 인터페이스"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)

        recommendation = "WAIT"
        if score >= buy_threshold:
            recommendation = "BUY"
        elif score <= sell_threshold:
            recommendation = "SELL"
            
        return {
            "ticker": ticker,
            "score": score,
            "recommendation": recommendation,
            "reasons": reasons,
            "current_price": state.current_price,
            "rsi": state.rsi
        }

    @classmethod
    def calculate_score(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float) -> tuple:
        """개별 종목의 투자 점수 계산 (로직 분리)"""
        curr_price = state.current_price
        if curr_price <= 0: return 0, ["가격정보없음"]

        profit_pct = 0.0
        if holding:
            buy_price = holding['buy_price']
            profit_pct = (curr_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
        panic_locks = user_state.get('panic_locks', {})
        regime = macro.get('market_regime', {}).get('status', 'Unknown').upper()

        target_cash_ratio = SettingsService.get_float("STRATEGY_TARGET_CASH_RATIO", 0.4)
        base_score = SettingsService.get_int("STRATEGY_BASE_SCORE", 50)
        oversold_rsi = SettingsService.get_float("STRATEGY_OVERSOLD_RSI", 30.0)
        overbought_rsi = SettingsService.get_float("STRATEGY_OVERBOUGHT_RSI", 70.0)
        dip_buy_pct = SettingsService.get_float("STRATEGY_DIP_BUY_PCT", -5.0)
        take_profit_pct = SettingsService.get_float("STRATEGY_TAKE_PROFIT_PCT", 5.0)
        stop_loss_pct = SettingsService.get_float("STRATEGY_STOP_LOSS_PCT", -10.0)

        if ticker in panic_locks:
            return (100, ["3일룰회복대기"]) if state.rsi < oversold_rsi else (0, ["패닉락구간"])

        # 점수 계산
        score = base_score
        reasons = []

        # [A] 기술적 지표
        # RSI 연속 점수 (50 기준, 더 극단적일수록 높은 가중치)
        rsi = state.rsi
        if rsi <= 30:
            # 0~30: +20 ~ +10 (극과매도)
            rsi_score = 20 - (rsi / 30) * 10
            score += int(rsi_score)
            reasons.append(f"RSI극과매도({rsi:.1f},+{int(rsi_score)})")
        elif rsi < 50:
            # 30~50: +10 ~ 0 (과매도)
            rsi_score = 10 - ((rsi - 30) / 20) * 10
            if rsi_score >= 5:
                score += int(rsi_score)
                reasons.append(f"RSI과매도({rsi:.1f},+{int(rsi_score)})")
        elif rsi <= 70:
            # 50~70: 0 ~ -10 (과매수)
            rsi_score = -((rsi - 50) / 20) * 10
            if rsi_score <= -5:
                score += int(rsi_score)
                reasons.append(f"RSI과매수({rsi:.1f},{int(rsi_score)})")
        else:
            # 70~100: -10 ~ -20 (극과매수)
            rsi_score = -10 - ((rsi - 70) / 30) * 10
            score += int(rsi_score)
            reasons.append(f"RSI극과매수({rsi:.1f},{int(rsi_score)})")

        change_rate = getattr(state, 'change_rate', 0)
        if change_rate <= dip_buy_pct: 
            score += cls.WEIGHTS['DIP_BUY_5PCT']
            reasons.append(f"급락({change_rate:.1f}%)")
        elif change_rate >= 5.0: 
            score += cls.WEIGHTS['SURGE_SELL_5PCT']
            reasons.append(f"급등({change_rate:.1f}%)")

        # DCF 기반 가치평가
        if state.dcf_value and state.dcf_value > 0:
            undervalue_pct = (state.dcf_value - curr_price) / curr_price * 100
            
            if undervalue_pct >= 20:
                score += cls.WEIGHTS['DCF_UNDERVALUE_HIGH']
                reasons.append(f"DCF고저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= 10:
                score += cls.WEIGHTS['DCF_UNDERVALUE_MID']
                reasons.append(f"DCF중저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= 5:
                score += cls.WEIGHTS['DCF_UNDERVALUE_LOW']
                reasons.append(f"DCF저평가({undervalue_pct:.1f}%)")
            elif undervalue_pct >= -5:
                score += cls.WEIGHTS['DCF_FAIR_VALUE']
                reasons.append("DCF적정가")
            elif undervalue_pct >= -15:
                score += cls.WEIGHTS['DCF_OVERVALUE_LOW']
                reasons.append(f"DCF고평가({-undervalue_pct:.1f}%)")
            else:
                score += cls.WEIGHTS['DCF_OVERVALUE_HIGH']
                reasons.append(f"DCF고고평가({-undervalue_pct:.1f}%)")
        
        ema200 = state.ema.get(200) if state.ema else None
        if ema200 and ema200 > 0 and (ema200 * 1.00 <= curr_price <= ema200 * 1.02):
            score += cls.WEIGHTS['SUPPORT_EMA']; reasons.append("EMA200지지")

        # [B] 포트폴리오
        if holding:
            if profit_pct >= take_profit_pct: 
                score += cls.WEIGHTS['PROFIT_TAKE_TARGET']; reasons.append(f"익절권({profit_pct:.1f}%)")
            elif profit_pct <= -5.0 and profit_pct > stop_loss_pct: 
                score += cls.WEIGHTS['ADD_POSITION_LOSS']; reasons.append(f"추매권({profit_pct:.1f}%)")
            elif profit_pct <= stop_loss_pct:
                score = 0; reasons.append("손절도달")

        # [C] 시장/거시
        macro_score = macro.get('economic_indicators', {}).get('summary', {}).get('total_score', 0)
        vix = macro.get('vix', 20.0)
        fng = macro.get('fear_greed', 50)
        
        if vix >= 25 or fng <= 30: # 공포 단계 강화
            score += cls.WEIGHTS['PANIC_MARKET_BUY']; reasons.append("극도의공포(매수기회)")
        elif vix <= 15 or fng >= 70:
            score += cls.WEIGHTS['PROFIT_TAKE_TARGET'] // 2; reasons.append("시장과열(분할익절)")
        
        if regime == 'BULL':
            score += cls.WEIGHTS['BULL_MARKET_SECTOR']; reasons.append("상승장어드밴티지")
        elif regime == 'BEAR':
            score -= 10; reasons.append("하락장리스크관리")

        # [D] 목표가 도달 (실시간 워칭 기반)
        target_buy = getattr(state, 'target_buy_price', 0)
        target_sell = getattr(state, 'target_sell_price', 0)
        
        if target_buy > 0 and curr_price <= target_buy:
            score += 30; reasons.append(f"목표진입가도달(${target_buy})")
        
        if target_sell > 0 and curr_price >= target_sell:
            score -= 30; reasons.append(f"목표매도가도달(${target_sell})")

        # [E] 시가총액 상위 10개 가중치
        top10_bonus = SettingsService.get_int("STRATEGY_TOP10_BONUS", 10)
        if top10_bonus and ticker in cls._get_top10_market_cap_tickers():
            score += top10_bonus
            reasons.append(f"시총상위10(+{top10_bonus})")

        # [F] 사용자 지정 가중치 오버라이드
        overrides = cls.get_top_weight_overrides()
        if ticker in overrides:
            custom_bonus = int(overrides[ticker])
            if custom_bonus != 0:
                score += custom_bonus
                reasons.append(f"가중치사용자설정({custom_bonus:+d})")

        if cash_ratio < target_cash_ratio and score > 50:
            score += cls.WEIGHTS['CASH_PENALTY']; reasons.append("현금부족")

        return max(0, min(100, score)), reasons

    @classmethod
    def _analyze_stock_v3(cls, ticker: str, state, holding: Optional[dict], macro: dict, user_state: dict, total_assets: float, cash_balance: float, exchange_rate: float, user_id: str = "sean"):
        """기존 내부 분석 루프 (리팩토링된 calculate_score 활용)"""
        score, reasons = cls.calculate_score(ticker, state, holding, macro, user_state, total_assets, cash_balance)
        
        profit_pct = 0.0
        if holding:
            buy_price = holding['buy_price']
            profit_pct = (state.current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

        reason_str = ", ".join(reasons)
        
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)
        sell_threshold = SettingsService.get_int("STRATEGY_SELL_THRESHOLD", 25)
        
        if score >= buy_threshold:
            cls._execute_trade_v2(
                ticker,
                "buy",
                f"점수 {score} [{reason_str}]",
                profit_pct,
                holding is not None,
                score,
                state.current_price,
                total_assets,
                cash_balance,
                exchange_rate,
                holdings=PortfolioService.load_portfolio(user_id),
                user_id=user_id,
                holding=holding,
                macro=macro
            )
        elif score <= sell_threshold:
            if holding:
                cls._execute_trade_v2(
                    ticker,
                    "sell",
                    f"점수 {score} [{reason_str}]",
                    profit_pct,
                    True,
                    score,
                    state.current_price,
                    total_assets,
                    cash_balance,
                    exchange_rate,
                    holdings=PortfolioService.load_portfolio(user_id),
                    user_id=user_id,
                    holding=holding,
                    macro=macro
                )

    @classmethod
    def _execute_trade_v2(
        cls,
        ticker: str,
        side: str,
        reason: str,
        profit_pct: float,
        is_holding: bool,
        score: int,
        current_price: float,
        total_assets: float,
        cash_balance: float,
        exchange_rate: float,
        holdings: Optional[list] = None,
        user_id: str = "sean",
        holding: Optional[dict] = None,
        macro: Optional[dict] = None
    ) -> bool:
        """개선된 분할 매매 실행 (한글화)"""
        from utils.logger import get_logger
        logger = get_logger("strategy_service")
        
        logger.info(f"📢 시그널 [{side.upper()}] {ticker} - 사유: {reason}")
        
        split_count = SettingsService.get_int("STRATEGY_SPLIT_COUNT", 3)
        per_trade_ratio = SettingsService.get_float("STRATEGY_PER_TRADE_RATIO", 0.05)
        buy_threshold = SettingsService.get_int("STRATEGY_BUY_THRESHOLD", 75)

        split_denominator = split_count
        
        trade_qty = 0
        executed = False
        if side == 'buy':
            # 시장 운영 시간 체크
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            if ticker.isdigit():
                if not MarketHourService.is_kr_market_open():
                    logger.info(f"⏭️ {ticker} 한국시장 비개장. 매수 스킵.")
                    return False
            else:
                if not MarketHourService.is_us_market_open(allow_extended=allow_extended):
                    logger.info(f"⏭️ {ticker} 미국시장 비개장. 매수 스킵.")
                    return False
            # 이미 보유 중이면 추가매수 조건 충족 시에만 진행
            if is_holding:
                add_position_below = SettingsService.get_float("STRATEGY_ADD_POSITION_BELOW", -5.0)
                if profit_pct > add_position_below:
                    logger.info(
                        f"⏭️ {ticker} 추가매수 조건 미충족 (수익률 {profit_pct:.2f}% > {add_position_below}%). 매수 스킵."
                    )
                    return

            # 1. 현금 비중 유지 (폭락장 제외)
            target_cash_ratio = SettingsService.get_float("STRATEGY_TARGET_CASH_RATIO", 0.3)
            cash_ratio = cash_balance / total_assets if total_assets > 0 else 0
            is_panic = cls._is_panic_market(macro or {})
            if cash_ratio <= target_cash_ratio and not is_panic:
                logger.info(
                    f"⏭️ {ticker} 현금비중 유지 (현금 {cash_ratio:.2%} <= {target_cash_ratio:.2%}). 매수 스킵."
                )
                return False

            # 2. 투자 강도 결정
            multiplier = 1.0
            if score >= 90: multiplier = 2.0
            elif score >= 80: multiplier = 1.5
            
            # 3. 목표 투자 금액 (KRW)
            target_invest_krw = total_assets * per_trade_ratio * multiplier
            
            # 4. 이번 회차 분할 매수 금액
            one_time_invest_krw = target_invest_krw / split_denominator
            
            # 5. 가용 현금 체크
            actual_invest_krw = min(one_time_invest_krw, cash_balance)
            
            # 환율 적용 (숫자가 아니면 미국 주식으로 간주)
            is_us = not ticker.isdigit()
            final_price = current_price * exchange_rate if is_us else current_price
            
            # 수량 계산
            quantity = int(actual_invest_krw // final_price)
            
            # [소액 자산 보정] 수량이 0주이나 확실한 신호(점수 75+)이고 현금이 있다면 최소 1주 매수
            if quantity == 0 and score >= buy_threshold and cash_balance >= final_price:
                logger.info(f"💡 소액 자산 보정: 최소 수량(1주) 확보를 위해 비중 상향 조정 집행")
                quantity = 1
                
            est_krw = quantity * final_price
            
            if quantity > 0:
                # 시장/섹터 비중 제한 확인 (매수/추가매수 모두 적용)
                if holdings is None:
                    holdings = PortfolioService.load_portfolio(user_id)
                ok, reasons = cls._passes_allocation_limits(
                    ticker=ticker,
                    add_value=est_krw,
                    holdings=holdings,
                    total_assets=total_assets,
                    cash_balance=cash_balance,
                    holding=holding
                )
                if not ok:
                    logger.info(f"⏭️ {ticker} 비중 제한으로 매수 스킵: {', '.join(reasons)}")
                    return False

                trade_qty = quantity
                logger.info(f"⚖️ {ticker} {split_denominator}분할 매수 중 1회차 집행 예정 ({quantity}주)")
                
                # 주문 실행
                res = KisService.send_order(ticker, quantity, 0, "buy")
                
                if res['status'] == 'success':
                    # 매매 내역 저장
                    OrderService.record_trade(ticker, "buy", quantity, final_price, "Strategy execution", "v3_strategy")
                    executed = True
                else:
                    logger.error(f"주문 실패: {res}")
            else:
                logger.warning(f"⚠️ {ticker} 잔고 부족으로 매수 불가 (필요: {final_price:,.0f}원)")
                return False

        elif side == 'sell':
            # 시장 운영 시간 체크
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            if ticker.isdigit():
                if not MarketHourService.is_kr_market_open():
                    logger.info(f"⏭️ {ticker} 한국시장 비개장. 매도 스킵.")
                    return False
            else:
                if not MarketHourService.is_us_market_open(allow_extended=allow_extended):
                    logger.info(f"⏭️ {ticker} 미국시장 비개장. 매도 스킵.")
                    return False
            # 보유 수량 확인 (PortfolioService 활용)
            portfolio = holdings or PortfolioService.load_portfolio(user_id)
            holding = next((h for h in portfolio if h['ticker'] == ticker), None)
            if not holding:
                return False
            
            holding_qty = holding['quantity']
            sell_qty = 0
            split_msg = ""
            
            if score <= 10: 
                sell_qty = holding_qty # 전량 매도
                split_msg = "전량 매도 (손절/위험)"
            else:
                sell_qty = max(1, int(holding_qty / split_denominator)) # 1/3 매도
                split_msg = "1/3 분할 매도 (익절)"
            
            trade_qty = sell_qty
            logger.info(f"⚖️ {ticker} {split_msg} 집행 예정 ({sell_qty}주)")
            
            # 주문 실행
            res = KisService.send_order(ticker, sell_qty, 0, "sell")
            
            if res['status'] == 'success':
                OrderService.record_trade(ticker, "sell", sell_qty, current_price, split_msg, "v3_strategy")
                executed = True
            else:
                logger.error(f"주문 실패: {res}")

        # 슬랙 알림
        meta = StockMetaService.get_stock_meta(ticker)
        name = ""
        if holding and holding.get("name"):
            name = holding.get("name")
        elif meta:
            name = meta.name_ko or meta.name_en or ""
        state = MarketDataService.get_state(ticker)
        change_rate = state.change_rate if state and state.change_rate is not None else 0.0

        emoji = "🔵" if side == "buy" else "🔴"
        msg = (
            f"{emoji} **[{side.upper()}] {ticker}, {name}, "
            f"점수: {score}, 가격: {current_price:,.2f}, "
            f"등락률: {change_rate:.2f}%, 수량: {trade_qty}주"
        )
        if side == "sell":
            msg += f", 수익율: {profit_pct:.2f}%"
        if executed:
            AlertService.send_slack_alert(msg)
        return executed
