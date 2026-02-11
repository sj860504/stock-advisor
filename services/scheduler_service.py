import asyncio
import threading
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from services.data_service import DataService
from services.alert_service import AlertService
from services.portfolio_service import PortfolioService
from services.macro_service import MacroService
from services.trading_strategy_service import TradingStrategyService
from services.kis_ws_service import kis_ws_service
from services.market_data_service import MarketDataService
from utils.logger import get_logger

logger = get_logger("scheduler")

class SchedulerService:
    _scheduler = None
    _ws_loop = None # ?뱀냼耳?猷⑦봽 李몄“ ???

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            
            # 1. ?ㅼ?以??깅줉
            # 留ㅼ씪 ?ㅼ쟾 8??30遺??곸쐞 醫낅ぉ 媛뺤젣 媛깆떊
            cls._scheduler.add_job(lambda: cls.manage_subscriptions(force_refresh=True), 'cron', hour=8, minute=30)
            cls._scheduler.add_job(cls.run_trading_strategy, 'interval', minutes=1)
            cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', hours=1)
            
            # 留ㅼ씪 ?ㅼ쟾 9??10遺?由щ갭?곗떛 ?ㅽ뻾 (援?궡??媛쒖옣 吏곹썑)
            cls._scheduler.add_job(cls.run_rebalancing, 'cron', hour=9, minute=10)
            
            # 2. KIS ?뱀냼耳??쒕쾭 ?쒖옉 (蹂꾨룄 ?ㅻ젅??
            def start_ws():
                try:
                    logger.info("?㏊ Starting [start_ws] thread...")
                    cls._ws_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(cls._ws_loop)
                    
                    # 珥덇린 援щ룆 ?ㅽ뻾 (猷⑦봽 ?쒖옉 ???덉빟)
                    logger.info("?븩 Scheduling initial subscriptions...")
                    cls._ws_loop.call_soon(lambda: asyncio.create_task(cls.manage_subscriptions_async()))
                    
                    logger.info("??Entering [kis_ws_service.connect()] loop...")
                    cls._ws_loop.run_until_complete(kis_ws_service.connect())
                except Exception as e:
                    logger.error(f"??Critical Error in [start_ws] thread: {e}", exc_info=True)
            
            threading.Thread(target=start_ws, daemon=True).start()
            
            cls._scheduler.start()
            logger.info("?? Scheduler and Real-time WebSocket Service Started.")
            
            # 3. ?ъ슜?먯뿉寃??먮룞 留ㅻℓ ?쒖옉 ?щ? 臾몄쓽
            cls._send_start_inquiry()

    @classmethod
    def _send_start_inquiry(cls):
        """?щ옓?쇰줈 ?먮룞 留ㅻℓ ?쒖옉 ?щ?瑜?臾몄쓽?⑸땲??"""
        msg = (
            "?뵒 **?먮룞 留ㅻℓ ?붿쭊??以鍮꾨릺?덉뒿?덈떎.**\n"
            "?꾩옱 紐⑤뱺 遺꾩꽍 諛?留ㅻℓ ?꾨줈?몄뒪媛 **?湲?DISABLED)** ?곹깭?낅땲??\n\n"
            "?먮룞 留ㅻℓ瑜??쒖옉?섏떆寃좎뒿?덇퉴?\n"
            "- [?쒖옉?섍린](http://localhost:8000/api/trading/start)\n"
            "- [以묒??섍린](http://localhost:8000/api/trading/stop)\n\n"
            "*吏곸젒 留ㅻℓ瑜??먰븯?쒕㈃ ??留곹겕瑜??쒖꽦?뷀븯吏 留덉꽭??*"
        )
        AlertService.send_slack_alert(msg)

    @classmethod
    def manage_subscriptions(cls, force_refresh: bool = False):
        """?숆린 ?ㅼ?以꾨윭?먯꽌 ?몄텧?섎뒗 愿由?硫붿꽌??(鍮꾨룞湲?猷⑦봽???꾩엫)"""
        if cls._ws_loop and cls._ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(cls.manage_subscriptions_async(force_refresh=force_refresh), cls._ws_loop)
        else:
            logger.warning("?좑툘 WebSocket loop not running. Skipping subscription refresh.")

    @classmethod
    async def manage_subscriptions_async(cls, force_refresh: bool = False):
        """?ㅼ젣 鍮꾨룞湲?援щ룆 ?ㅽ뻾 濡쒖쭅 (罹먯떆 ?곸슜)"""
        logger.info(f"?봽 Refreshing Market Subscriptions (Top 100 + Portfolio, force={force_refresh})...")
        try:
            # 罹먯떛??援?궡/誘멸뎅 ?곸쐞 100???곗빱 異붿텧 (24?쒓컙 ?좏슚)
            tickers = DataService.get_top_tickers_cached(limit=100, force_refresh=force_refresh)
            kr_tickers = tickers.get("kr", [])
            us_tickers = tickers.get("us", [])
            
            # 蹂댁쑀 醫낅ぉ 異붽?
            portfolio = PortfolioService.load_portfolio('sean')
            holdings = [h['ticker'] for h in portfolio]
            
            # 援?궡 二쇱떇 援щ룆 (蹂댁쑀???ы븿)
            for ticker in set(kr_tickers + holdings):
                if len(ticker) == 6 and ticker.isdigit():
                    await kis_ws_service.subscribe(ticker, market="KRX")
            
            # 誘멸뎅 二쇱떇 援щ룆
            for ticker in us_tickers:
                if ticker.isalpha():
                    await kis_ws_service.subscribe(ticker, market="NAS")
            
            logger.info(f"??Subscriptions managed: KR={len(kr_tickers)}, US={len(us_tickers)}, Holdings={len(holdings)}")
        except Exception as e:
            logger.error(f"??Error in manage_subscriptions_async: {e}")

    @classmethod
    def run_trading_strategy(cls):
        """1遺꾨쭏???꾩껜 ?꾨왂 遺꾩꽍 諛??먮룞 留ㅻℓ ?ㅽ뻾"""
        logger.info("?뱢 Running 1-min Trading Strategy analysis...")
        try:
            TradingStrategyService.run_strategy(user_id='sean')
        except Exception as e:
            logger.error(f"??Error during strategy run: {e}")

    @classmethod
    def check_portfolio_hourly(cls):
        """?ㅼ떆媛??곗씠?곕? 湲곕컲?쇰줈 ?ы듃?대━???곹깭 由ы룷???앹꽦 諛??꾩넚"""
        logger.info("??Generating hourly portfolio report...")
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
                from services.report_service import ReportService
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("??Hourly report sent to Slack.")
        except Exception as e:
            logger.error(f"??Error in check_portfolio_hourly: {e}")

    @classmethod
    def run_rebalancing(cls):
        """蹂멸꺽?곸씤 鍮꾩쑉 湲곕컲 由щ갭?곗떛 ?ㅽ뻾"""
        logger.info("?뽳툘 Running daily Portfolio Rebalancing check...")
        try:
            PortfolioService.rebalance_portfolio("sean")
        except Exception as e:
            logger.error(f"??Error during rebalancing: {e}")
