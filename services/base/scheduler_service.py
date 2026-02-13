import asyncio
import threading
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from services.market.data_service import DataService
from services.notification.alert_service import AlertService
from services.trading.portfolio_service import PortfolioService
from services.market.macro_service import MacroService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.kis.kis_ws_service import kis_ws_service
from services.market.market_data_service import MarketDataService
from services.market.market_hour_service import MarketHourService
from services.config.settings_service import SettingsService
from utils.logger import get_logger

logger = get_logger("scheduler")

class SchedulerService:
    _scheduler = None
    _ws_loop = None

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            
            # 1. 스케줄 등록
            # 매일 새벽 4시 00분: 한/미 시총 100위 종목 시세 및 지표(RSI, EMA, DCF) 자동 수집 및 동기화
            cls._scheduler.add_job(lambda: DataService.sync_daily_market_data(limit=100), 'cron', hour=4, minute=0)
            
            # 매일 오전 8시 30분: 실시간 웹소켓 구독 종목 갱신
            cls._scheduler.add_job(lambda: cls.manage_subscriptions(force_refresh=True), 'cron', hour=8, minute=30)
            
            # 1분 단위 매매 전략 실행
            cls._scheduler.add_job(cls.run_trading_strategy, 'interval', minutes=1)
            
            # 1시간 단위 포트폴리오 현황 체크 및 알림
            cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', hours=1)
            
            # 매일 오전 9시 10분: 리밸런싱 실행 (국내장 개장 직후)
            cls._scheduler.add_job(cls.run_rebalancing, 'cron', hour=9, minute=10)
            
            # 10분 단위 포트폴리오 DB 동기화 (KIS 데이터 우선)
            cls._scheduler.add_job(cls.sync_portfolio_periodic, 'interval', minutes=10)
            
            # 10분 단위 틱매매 현황 리포트
            cls._scheduler.add_job(cls.report_tick_trade_status, 'interval', minutes=10)
            
            # 2. KIS WebSocket 서비스 시작 (완전 분리된 전용 스레드)
            def start_ws_thread():
                """웹소켓 전용 이벤트 루프를 생성하고 무한 연결 루프를 실행"""
                try:
                    logger.info("🧵 WebSocket dedicated thread starting...")
                    cls._ws_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(cls._ws_loop)
                    
                    # 1. 초기 구독 관리 태스크 등록
                    logger.info("📡 Scheduling initial market subscriptions...")
                    cls._ws_loop.call_soon(lambda: asyncio.create_task(cls.manage_subscriptions_async()))
                    
                    # 2. 웹소켓 무한 연결 루프 실행 (핸드쉐이크 보장 로직 포함)
                    logger.info("🚀 Launching guaranteed WebSocket connection loop...")
                    cls._ws_loop.run_until_complete(kis_ws_service.connect())
                except Exception as e:
                    logger.error(f"❌ Critical Error in WebSocket thread: {e}", exc_info=True)
            
            # 독립된 데몬 쓰레드로 실행
            ws_thread = threading.Thread(target=start_ws_thread, name="KIS-WS-Thread", daemon=True)
            ws_thread.start()
            
            cls._scheduler.start()
            logger.info("✅ Scheduler and Real-time WebSocket Service Started.")
            
            # 3. 자동 매매 시작 여부 문의
            cls._send_start_inquiry()

            # 4. 앱 기동 직후 KIS 잔고 동기화
            try:
                PortfolioService.sync_with_kis("sean")
                logger.info("✅ Portfolio synced with KIS on startup.")
            except Exception as e:
                logger.error(f"❌ Failed to sync portfolio with KIS on startup: {e}")

    @classmethod
    def _send_start_inquiry(cls):
        """슬랙으로 자동 매매 시작 여부를 문의합니다."""
        msg = (
            "🤖 **자동 매매 엔진이 준비되었습니다.**\n"
            "현재 모든 분석 및 매매 프로세스가 **대기(DISABLED)** 상태입니다.\n\n"
            "자동 매매를 시작하시겠습니까?\n"
            "- [시작하기](http://localhost:8000/api/trading/start)\n"
            "- [중지하기](http://localhost:8000/api/trading/stop)\n\n"
            "*직접 매매를 원하시면 위 링크를 활성화하지 마세요.*"
        )
        AlertService.send_slack_alert(msg)

    @classmethod
    def manage_subscriptions(cls, force_refresh: bool = False):
        """동기 스케줄러에서 호출하는 구독 관리 메소드"""
        if cls._ws_loop and cls._ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(cls.manage_subscriptions_async(force_refresh=force_refresh), cls._ws_loop)
        else:
            logger.warning("⚠️ WebSocket loop not running. Skipping subscription refresh.")

    @classmethod
    async def manage_subscriptions_async(cls, force_refresh: bool = False):
        """실제 비동기 구독 실행 로직"""
        logger.info(f"🔄 Refreshing Market Subscriptions (Top 100 + Portfolio, force={force_refresh})...")
        try:
            # 1. 대상 티커 모두 수집
            kr_tickers = DataService.get_top_krx_tickers(limit=100)
            us_tickers = DataService.get_top_us_tickers(limit=100)
            portfolio = PortfolioService.load_portfolio('sean')
            holdings = [h['ticker'] for h in portfolio]
            
            all_kr = list(set(kr_tickers + holdings))
            all_us = list(set(us_tickers))
            
            # 2. MarketDataService에 일괄 등록 (DB 일괄 조회 및 선별적 분석)
            MarketDataService.register_batch(all_kr + all_us)
            
            # 3. 실시간 웹소켓 구독 (분석과 병렬로 수행)
            # 국내 주식 구독
            for ticker in all_kr:
                if len(ticker) == 6 and ticker.isdigit():
                    await kis_ws_service.subscribe(ticker, market="KRX")
                    await asyncio.sleep(0.05) 
            
            # 미국 주식 구독
            for ticker in all_us:
                if ticker.isalpha():
                    await kis_ws_service.subscribe(ticker, market="NAS")
                    await asyncio.sleep(0.05) 
            
            logger.info(f"✅ Subscriptions managed: KR={len(kr_tickers)}, US={len(us_tickers)}, Holdings={len(holdings)}")
        except Exception as e:
            logger.error(f"❌ Error in manage_subscriptions_async: {e}")

    @classmethod
    def run_trading_strategy(cls):
        """매매 전략 분석 및 자동 매매 실행"""
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        if not MarketHourService.is_strategy_window_open(allow_extended=allow_extended, pre_open_lead_minutes=60):
            logger.info("⏸️ Market closed window. Skipping strategy run.")
            return

        logger.info("📊 Running Trading Strategy analysis...")
        try:
            TradingStrategyService.run_strategy(user_id='sean')
        except Exception as e:
            logger.error(f"❌ Error during strategy run: {e}")

    @classmethod
    def check_portfolio_hourly(cls):
        """시간당 포트폴리오 현황 체크 및 알림"""
        logger.info("🕒 Generating hourly portfolio report...")
        try:
            # 최신 잔고로 동기화 후 리포트 전송
            PortfolioService.sync_with_kis('sean')
            macro = MacroService.get_macro_data()
            all_states = MarketDataService.get_all_states()
            portfolio = PortfolioService.load_portfolio('sean')
            
            gainers = []
            for item in portfolio:
                ticker = item['ticker']
                state = all_states.get(ticker)
                if state and state.change_rate > 0:
                    gainers.append({
                        'ticker': ticker,
                        'name': item.get('name', ticker),
                        'price': state.current_price,
                        'change': state.change_rate,
                        'market': "Real-time"
                    })
            
            from services.notification.report_service import ReportService
            if gainers:
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("📤 Hourly gainers report sent to Slack.")

            # 포트폴리오 현황 리포트
            summary = PortfolioService.get_last_balance_summary()
            cash = float(summary.get("prvs_rcdl_excc_amt") or PortfolioService.load_cash('sean') or 0)
            portfolio_msg = ReportService.format_portfolio_report(portfolio, cash, all_states, summary)
            AlertService.send_slack_alert(portfolio_msg)
            logger.info("📤 Hourly portfolio report sent to Slack.")
        except Exception as e:
            logger.error(f"❌ Error in check_portfolio_hourly: {e}")

    @classmethod
    def run_rebalancing(cls):
        """포트폴리오 리밸런싱 실행"""
        logger.info("⚖️ Running daily Portfolio Rebalancing check...")
        try:
            PortfolioService.rebalance_portfolio("sean")
        except Exception as e:
            logger.error(f"❌ Error during rebalancing: {e}")

    @classmethod
    def sync_portfolio_periodic(cls):
        """10분 주기 포트폴리오 DB 동기화 실행"""
        logger.info("🔄 Running periodic Portfolio DB sync with KIS...")
        try:
            PortfolioService.sync_with_kis("sean")
        except Exception as e:
            logger.error(f"❌ Error during portfolio sync: {e}")

    @classmethod
    def report_tick_trade_status(cls):
        """10분 주기 틱매매 수익 현황 리포트"""
        try:
            if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1:
                return
            ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
            if not ticker:
                return

            PortfolioService.sync_with_kis("sean")
            holdings = PortfolioService.load_portfolio("sean")
            holding = next((h for h in holdings if h.get("ticker") == ticker), None)
            if not holding:
                AlertService.send_slack_alert(f"⏱️ [틱매매 10분 리포트] {ticker} 보유 수량 없음")
                return

            qty = float(holding.get("quantity", 0) or 0)
            buy_price = float(holding.get("buy_price", 0) or 0)
            current_price = float(holding.get("current_price", 0) or 0)
            if qty <= 0 or buy_price <= 0 or current_price <= 0:
                return

            profit_amt = (current_price - buy_price) * qty
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            AlertService.send_slack_alert(
                f"⏱️ [틱매매 10분 리포트] {ticker} 수익율 {profit_pct:+.2f}%, 수익금 {profit_amt:,.0f}원"
            )
        except Exception as e:
            logger.error(f"❌ Error during tick trade report: {e}")

    @classmethod
    def get_all_cached_prices(cls) -> dict:
        """라우터에서 요구하는 포맷으로 모든 실시간 캐시 데이터를 반환합니다."""
        all_states = MarketDataService.get_all_states()
        result = {}
        for ticker, state in all_states.items():
            result[ticker] = {
                "ticker": ticker,
                "name": state.name, # 종목명 추가
                "price": state.current_price,
                "rsi": state.rsi,
                "change": state.current_price - state.prev_close if state.prev_close > 0 else 0,
                "change_pct": state.change_rate,
                "fair_value_dcf": state.dcf_value,
                "target_buy_price": state.target_buy_price,   # 추가
                "target_sell_price": state.target_sell_price, # 추가
                "ema5": state.ema.get(5),
                "ema10": state.ema.get(10),
                "ema20": state.ema.get(20),
                "ema60": state.ema.get(60),
                "ema120": state.ema.get(120),
                "ema200": state.ema.get(200),
            }
        return result
