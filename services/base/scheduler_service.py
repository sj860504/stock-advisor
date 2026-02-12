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
            
            # 2. KIS WebSocket 서비스 시작 (별도 스레드)
            def start_ws():
                try:
                    logger.info("🔌 Starting [start_ws] thread...")
                    cls._ws_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(cls._ws_loop)
                    
                    # 초기 구독 실행
                    logger.info("📡 Scheduling initial subscriptions...")
                    cls._ws_loop.call_soon(lambda: asyncio.create_task(cls.manage_subscriptions_async()))
                    
                    logger.info("🚀 Entering [kis_ws_service.connect()] loop...")
                    cls._ws_loop.run_until_complete(kis_ws_service.connect())
                except Exception as e:
                    logger.error(f"❌ Critical Error in [start_ws] thread: {e}", exc_info=True)
            
            threading.Thread(target=start_ws, daemon=True).start()
            
            cls._scheduler.start()
            logger.info("✅ Scheduler and Real-time WebSocket Service Started.")
            
            # 3. 자동 매매 시작 여부 문의
            cls._send_start_inquiry()

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
            # 시총 상위 100위 티커 추출 (DataService 활용)
            kr_tickers = DataService.get_top_krx_tickers(limit=100)
            us_tickers = DataService.get_top_us_tickers(limit=100)
            
            # 보유 종목 추가
            portfolio = PortfolioService.load_portfolio('sean')
            holdings = [h['ticker'] for h in portfolio]
            
            # 국내 주식 구독
            for ticker in set(kr_tickers + holdings):
                if len(ticker) == 6 and ticker.isdigit():
                    await kis_ws_service.subscribe(ticker, market="KRX")
            
            # 미국 주식 구독
            for ticker in us_tickers:
                if ticker.isalpha():
                    await kis_ws_service.subscribe(ticker, market="NAS")
            
            logger.info(f"✅ Subscriptions managed: KR={len(kr_tickers)}, US={len(us_tickers)}, Holdings={len(holdings)}")
        except Exception as e:
            logger.error(f"❌ Error in manage_subscriptions_async: {e}")

    @classmethod
    def run_trading_strategy(cls):
        """매매 전략 분석 및 자동 매매 실행"""
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
            
            if gainers:
                from services.notification.report_service import ReportService
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("📤 Hourly report sent to Slack.")
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
