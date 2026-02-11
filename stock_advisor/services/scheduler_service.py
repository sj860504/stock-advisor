import asyncio
import threading
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from stock_advisor.services.data_service import DataService
from stock_advisor.services.alert_service import AlertService
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.macro_service import MacroService
from stock_advisor.services.trading_strategy_service import TradingStrategyService
from stock_advisor.services.kis_ws_service import kis_ws_service
from stock_advisor.services.market_data_service import MarketDataService
from stock_advisor.utils.logger import get_logger

logger = get_logger("scheduler")

class SchedulerService:
    _scheduler = None
    _ws_loop = None # 웹소켓 루프 참조 저장

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            
            # 1. 스케줄 등록
            # 매일 오전 8시 30분 상위 종목 강제 갱신
            cls._scheduler.add_job(lambda: cls.manage_subscriptions(force_refresh=True), 'cron', hour=8, minute=30)
            cls._scheduler.add_job(cls.run_trading_strategy, 'interval', minutes=1)
            cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', hours=1)
            
            # 매일 오전 9시 10분 리밸런싱 실행 (국내장 개장 직후)
            cls._scheduler.add_job(cls.run_rebalancing, 'cron', hour=9, minute=10)
            
            # 2. KIS 웹소켓 서버 시작 (별도 스레드)
            def start_ws():
                try:
                    logger.info("🧵 Starting [start_ws] thread...")
                    cls._ws_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(cls._ws_loop)
                    
                    # 초기 구독 실행 (루프 시작 전 예약)
                    logger.info("🕒 Scheduling initial subscriptions...")
                    cls._ws_loop.call_soon(lambda: asyncio.create_task(cls.manage_subscriptions_async()))
                    
                    logger.info("⚡ Entering [kis_ws_service.connect()] loop...")
                    cls._ws_loop.run_until_complete(kis_ws_service.connect())
                except Exception as e:
                    logger.error(f"❌ Critical Error in [start_ws] thread: {e}", exc_info=True)
            
            threading.Thread(target=start_ws, daemon=True).start()
            
            cls._scheduler.start()
            logger.info("🚀 Scheduler and Real-time WebSocket Service Started.")
            
            # 3. 사용자에게 자동 매매 시작 여부 문의
            cls._send_start_inquiry()

    @classmethod
    def _send_start_inquiry(cls):
        """슬랙으로 자동 매매 시작 여부를 문의합니다."""
        msg = (
            "🔔 **자동 매매 엔진이 준비되었습니다.**\n"
            "현재 모든 분석 및 매매 프로세스가 **대기(DISABLED)** 상태입니다.\n\n"
            "자동 매매를 시작하시겠습니까?\n"
            "- [시작하기](http://localhost:8000/api/trading/start)\n"
            "- [중지하기](http://localhost:8000/api/trading/stop)\n\n"
            "*직접 매매를 원하시면 위 링크를 활성화하지 마세요.*"
        )
        AlertService.send_slack_alert(msg)

    @classmethod
    def manage_subscriptions(cls, force_refresh: bool = False):
        """동기 스케줄러에서 호출되는 관리 메서드 (비동기 루프에 위임)"""
        if cls._ws_loop and cls._ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(cls.manage_subscriptions_async(force_refresh=force_refresh), cls._ws_loop)
        else:
            logger.warning("⚠️ WebSocket loop not running. Skipping subscription refresh.")

    @classmethod
    async def manage_subscriptions_async(cls, force_refresh: bool = False):
        """실제 비동기 구독 실행 로직 (캐시 적용)"""
        logger.info(f"🔄 Refreshing Market Subscriptions (Top 100 + Portfolio, force={force_refresh})...")
        try:
            # 캐싱된 국내/미국 상위 100위 티커 추출 (24시간 유효)
            tickers = DataService.get_top_tickers_cached(limit=100, force_refresh=force_refresh)
            kr_tickers = tickers.get("kr", [])
            us_tickers = tickers.get("us", [])
            
            # 보유 종목 추가
            portfolio = PortfolioService.load_portfolio('sean')
            holdings = [h['ticker'] for h in portfolio]
            
            # 국내 주식 구독 (보유량 포함)
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
        """1분마다 전체 전략 분석 및 자동 매매 실행"""
        logger.info("📈 Running 1-min Trading Strategy analysis...")
        try:
            TradingStrategyService.run_strategy(user_id='sean')
        except Exception as e:
            logger.error(f"❌ Error during strategy run: {e}")

    @classmethod
    def check_portfolio_hourly(cls):
        """실시간 데이터를 기반으로 포트폴리오 상태 리포트 생성 및 전송"""
        logger.info("⏰ Generating hourly portfolio report...")
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
                from stock_advisor.services.report_service import ReportService
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("✅ Hourly report sent to Slack.")
        except Exception as e:
            logger.error(f"❌ Error in check_portfolio_hourly: {e}")

    @classmethod
    def run_rebalancing(cls):
        """본격적인 비율 기반 리밸런싱 실행"""
        logger.info("⚖️ Running daily Portfolio Rebalancing check...")
        try:
            PortfolioService.rebalance_portfolio("sean")
        except Exception as e:
            logger.error(f"❌ Error during rebalancing: {e}")
