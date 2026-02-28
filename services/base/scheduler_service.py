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
from services.market.stock_meta_service import StockMetaService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("scheduler")

# WebSocket 실시간 구독 상한 (KIS 실전/모의 모두 40종목 제한, 시장별 20씩 배분)
WS_HIGH_TIER_COUNT = 20
# Tier LOW 폴링 주기 (분)
LOW_TIER_POLL_MINUTES = 5


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
            
            # 매일 오전 9시 00분: 전일 매매 히스토리 Slack 보고
            cls._scheduler.add_job(cls.report_daily_trade_history, 'cron', hour=9, minute=0)

            # 매일 오전 9시 10분: 리밸런싱 실행 (국내장 개장 직후)
            cls._scheduler.add_job(cls.run_rebalancing, 'cron', hour=9, minute=10)

            # 매주 월요일 오전 9시 20분: 섹터 비중 리밸런싱 (Tech 50% / Value 30% / Fin 20%)
            # 편차 < 5% 자동 스킵, 5~10% 절반 실행, >10% 전체 실행
            cls._scheduler.add_job(cls.run_sector_rebalance, 'cron',
                                   day_of_week='mon', hour=9, minute=20,
                                   id='weekly_sector_rebalance')
            
            # 5분 단위 Tier LOW 종목 가격 폴링 (WebSocket 미구독 종목)
            cls._scheduler.add_job(cls._refresh_low_tier_prices, 'interval', minutes=LOW_TIER_POLL_MINUTES)

            # 10분 단위 포트폴리오 DB 동기화 (KIS 데이터 우선)
            cls._scheduler.add_job(cls.sync_portfolio_periodic, 'interval', minutes=10)
            
            # 10분 단위 틱매매 현황 리포트
            cls._scheduler.add_job(cls.report_tick_trade_status, 'interval', minutes=10)

            # 미국 경제지표 주요 발표 시각에 신규 발표 감지 & regime 갱신
            # APScheduler timezone 파라미터로 ET(미동부) 기준 cron 등록
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")
            # 08:31 ET (월~금): CPI/PPI/NFP/실업수당 등 대부분 지표 (8:30 발표)
            cls._scheduler.add_job(
                cls._check_economic_releases, 'cron',
                day_of_week='mon-fri', hour=8, minute=31,
                timezone=_ET, id='econ_0830',
            )
            # 09:16 ET (월~금): 산업생산/설비가동률 (9:15 발표)
            cls._scheduler.add_job(
                cls._check_economic_releases, 'cron',
                day_of_week='mon-fri', hour=9, minute=16,
                timezone=_ET, id='econ_0915',
            )
            # 10:01 ET (월~금): 소비자신뢰지수-미시간 (10:00 발표)
            cls._scheduler.add_job(
                cls._check_economic_releases, 'cron',
                day_of_week='mon-fri', hour=10, minute=1,
                timezone=_ET, id='econ_1000',
            )

            # 미국장 시간 중 30분마다 VIX 스파이크 감지 (ET 09:30~16:00)
            cls._scheduler.add_job(
                cls._check_vix_spike, 'cron',
                day_of_week='mon-fri', hour='9-15', minute='0,30',
                timezone=_ET, id='vix_spike_check',
            )

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

            # 3. 서버 기동 직후: FRED 최신 관측일 초기화 (기준점 설정)
            try:
                cls._init_economic_baselines()
            except Exception as e:
                logger.warning(f"⚠️ 경제지표 기준점 초기화 실패 (무시): {e}")

            # 4. 자동 매매 시작 여부 문의

            # 5. 앱 기동 직후 KIS 잔고 동기화
            try:
                # 이전 세션의 전략 활성화 상태 복원 (재시작 시 자동 재개)
                TradingStrategyService._restore_enabled_state()
                PortfolioService.sync_with_kis("sean")
                logger.info("✅ Portfolio synced with KIS on startup.")
                cls._send_start_inquiry()

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
        """실제 비동기 구독 실행 로직.

        Tier HIGH (시장별 상위 WS_HIGH_TIER_COUNT + 보유 종목): WebSocket 실시간 구독
        Tier LOW  (나머지 80종목): MarketDataService 등록만 (5분 폴링으로 가격 갱신)
        """
        logger.info(f"🔄 Refreshing Market Subscriptions (Top 100 + Portfolio, force={force_refresh})...")
        try:
            def _norm_ticker(t):
                t = str(t or "").strip().upper()
                if not t:
                    return ""
                if t.isdigit() and len(t) < 6:
                    t = t.zfill(6)
                return t

            # ── 1. 전체 유니버스 수집 ─────────────────────────────────────
            kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
            us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
            portfolio = PortfolioService.load_portfolio('sean')
            holdings_raw = [
                _norm_ticker(h.get('ticker') if isinstance(h, dict) else getattr(h, "ticker", ""))
                for h in portfolio
            ]
            kr_holdings = {t for t in holdings_raw if t and is_kr(t) and len(t) == 6}
            us_holdings = {t for t in holdings_raw if t and t.isalpha()}

            all_kr = list(dict.fromkeys(  # 순서 유지하면서 중복 제거 (시총 순위 보존)
                [t for t in kr_tickers if t and is_kr(t) and len(t) == 6]
                + list(kr_holdings)
            ))
            all_us = list(dict.fromkeys(
                [t for t in us_tickers if t and t.isalpha()]
                + list(us_holdings)
            ))
            target_universe = set(all_kr + all_us)

            # ── 2. 티어 분류 ──────────────────────────────────────────────
            # 보유 종목은 항상 HIGH (실시간 필요)
            kr_high = list(kr_holdings) + [t for t in all_kr if t not in kr_holdings][:WS_HIGH_TIER_COUNT]
            us_high = list(us_holdings) + [t for t in all_us if t not in us_holdings][:WS_HIGH_TIER_COUNT]
            # 중복 제거 후 최대 WS_HIGH_TIER_COUNT * 2 유지 (KR 20 + US 20)
            kr_high_set = set(kr_high[:WS_HIGH_TIER_COUNT + len(kr_holdings)])
            us_high_set = set(us_high[:WS_HIGH_TIER_COUNT + len(us_holdings)])
            high_set = kr_high_set | us_high_set
            low_set = target_universe - high_set
            MarketDataService.set_tiers(high_set, low_set)

            # ── 3. 기존 캐시 정리 후 전체 일괄 등록 ──────────────────────
            MarketDataService.prune_states(target_universe)

            from services.market.market_hour_service import MarketHourService
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
            is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
            watch_kr = not is_us_open
            watch_us = not is_kr_open

            logger.info(
                f"📺 KR 개장={is_kr_open}, US 개장={is_us_open} | "
                f"HIGH {len(high_set)}종목 (WebSocket), LOW {len(low_set)}종목 (5분 폴링)"
            )

            tickers_to_register = []
            if watch_kr:
                tickers_to_register.extend(all_kr)
            if watch_us:
                tickers_to_register.extend(all_us)
            MarketDataService.register_batch(tickers_to_register)

            # ── 4. WebSocket 구독: HIGH 티어만 ────────────────────────────
            if watch_kr:
                for ticker in all_kr:
                    if ticker in kr_high_set and len(ticker) == 6 and is_kr(ticker):
                        await kis_ws_service.subscribe(ticker, market="KRX")
                        await asyncio.sleep(0.05)

            if watch_us:
                market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS", "NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}
                us_meta_map = {m.ticker: m for m in StockMetaService.get_stock_meta_bulk(all_us)} if all_us else {}
                for ticker in all_us:
                    if ticker in us_high_set and ticker.isalpha():
                        meta = us_meta_map.get(ticker)
                        raw_market = (meta.api_market_code if meta and meta.api_market_code else "NAS").upper()
                        ws_market = market_map_4to3.get(raw_market, "NAS")
                        await kis_ws_service.subscribe(ticker, market=ws_market)
                        await asyncio.sleep(0.05)

            logger.info(
                f"✅ Subscriptions: WS HIGH {len(high_set)}종목, LOW poll {len(low_set)}종목 | "
                f"KR={len(all_kr)}, US={len(all_us)}, Holdings={len(holdings_raw)}"
            )
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
            for holding in portfolio:
                ticker = holding["ticker"]
                state = all_states.get(ticker)
                if state and state.change_rate > 0:
                    gainers.append({
                        "ticker": ticker,
                        "name": getattr(holding, "name", ticker) if not isinstance(holding, dict) else holding.get("name", ticker),
                        "price": state.current_price,
                        "change": state.change_rate,
                        "market": "Real-time"
                    })
            
            from services.notification.report_service import ReportService
            if gainers:
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("📤 Hourly gainers report sent to Slack.")

            # 포트폴리오 현황 리포트
            summary = PortfolioService.get_last_balance_summary()
            cash = PortfolioService.load_cash('sean')
            portfolio_msg = ReportService.format_portfolio_report(portfolio, cash, all_states, summary)
            AlertService.send_slack_alert(portfolio_msg)
            logger.info("📤 Hourly portfolio report sent to Slack.")
        except Exception as e:
            logger.error(f"❌ Error in check_portfolio_hourly: {e}")

    @classmethod
    def report_daily_trade_history(cls):
        """매일 오전 9시: 전 24시간 매매 히스토리를 Slack으로 보고합니다."""
        from services.trading.order_service import OrderService
        from services.notification.report_service import ReportService

        logger.info("📋 Generating daily trade history report...")
        try:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(hours=24)
            trades = OrderService.get_trade_history_by_date_range(start_dt, end_dt)
            msg = ReportService.format_daily_trade_history(trades, start_dt, end_dt)
            AlertService.send_slack_alert(msg)
            logger.info(f"📤 Daily trade history report sent: {len(trades)} trades.")
        except Exception as e:
            logger.error(f"❌ Error in daily trade history report: {e}")

    @classmethod
    def run_rebalancing(cls):
        """포트폴리오 리밸런싱 실행"""
        logger.info("⚖️ Running daily Portfolio Rebalancing check...")
        try:
            PortfolioService.rebalance_portfolio("sean")
        except Exception as e:
            logger.error(f"❌ Error during rebalancing: {e}")

    @classmethod
    def run_sector_rebalance(cls):
        """주간 섹터 비중 리밸런싱 (매주 월요일 9:20 KST).

        Tech 50% / Value 30% / Financial 20% 목표 비중 대비:
        - 편차 < 5%  → 스킵
        - 편차 5~10% → 절반 리밸런싱
        - 편차 > 10% → 전체 리밸런싱
        """
        logger.info("🔄 주간 섹터 리밸런싱 시작 (매주 월요일)...")
        try:
            result = TradingStrategyService.run_sector_rebalance(user_id="sean")
            logger.info(f"✅ 섹터 리밸런싱 완료: 매도 {len(result.get('sold',[]))}건, 매수 {len(result.get('bought',[]))}건")
        except Exception as e:
            logger.error(f"❌ 섹터 리밸런싱 오류: {e}")

    @classmethod
    def _refresh_low_tier_prices(cls):
        """Tier LOW 종목 현재가를 5분 주기로 KIS REST API 폴링하여 갱신합니다.
        현재 개장된 시장의 종목만 갱신 (KR 또는 US, 동시 개장 시 해당 시장만).
        """
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        if not is_kr_open and not is_us_open:
            return

        low_tickers = MarketDataService.get_low_tier_tickers()
        if not low_tickers:
            return

        # 개장 시장 기준으로 대상 필터링
        active_tickers = []
        for t in low_tickers:
            is_kr_t = is_kr(t)
            if is_kr_t and is_kr_open and not is_us_open:
                active_tickers.append(t)
            elif not is_kr_t and is_us_open and not is_kr_open:
                active_tickers.append(t)
            elif is_kr_t and is_kr_open:
                active_tickers.append(t)
            elif not is_kr_t and is_us_open:
                active_tickers.append(t)

        if not active_tickers:
            return

        logger.info(f"⏱️ Tier LOW 가격 갱신 시작: {len(active_tickers)}종목")
        try:
            from services.kis.kis_service import KisService
            from services.kis.fetch.kis_fetcher import KisFetcher
            token = KisService.get_access_token()
            us_meta_map = {}
            us_tickers_in_low = [t for t in active_tickers if not is_kr(t)]
            if us_tickers_in_low:
                us_meta_map = {
                    m.ticker: m for m in StockMetaService.get_stock_meta_bulk(us_tickers_in_low)
                }

            success, fail = 0, 0
            for ticker in active_tickers:
                try:
                    if is_kr(ticker):
                        info = KisFetcher.fetch_domestic_price(token, ticker)
                    else:
                        meta_row = us_meta_map.get(ticker)
                        meta = {"api_market_code": getattr(meta_row, "api_market_code", "NAS")} if meta_row else {}
                        info = KisFetcher.fetch_overseas_price(token, ticker, meta=meta)
                    price = float(info.get("price") or 0)
                    change_rate = float(info.get("rate") or info.get("change_rate") or 0)
                    if price > 0:
                        MarketDataService.update_price_from_sync(ticker, price, change_rate)
                        success += 1
                except Exception as e:
                    logger.debug(f"LOW tier poll 실패 {ticker}: {e}")
                    fail += 1

            logger.info(f"✅ Tier LOW 가격 갱신 완료: 성공 {success}, 실패 {fail}")
        except Exception as e:
            logger.error(f"❌ _refresh_low_tier_prices 오류: {e}")

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
            logger.info("⏱️ Running 10-minute tick trade report...")
            if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1:
                logger.info("⏭️ Tick trade report skipped: STRATEGY_TICK_ENABLED=0")
                return
            ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
            if not ticker:
                logger.info("⏭️ Tick trade report skipped: empty tick ticker")
                return

            PortfolioService.sync_with_kis("sean")
            holdings = PortfolioService.load_portfolio("sean")
            holding = next((h for h in holdings if (getattr(h, "ticker") if not isinstance(h, dict) else h.get("ticker")) == ticker), None)
            if not holding:
                logger.info(f"ℹ️ Tick trade report: no holding for {ticker}")
                AlertService.send_slack_alert(f"⏱️ [틱매매 10분 리포트] {ticker} 보유 수량 없음")
                return

            qty = float(getattr(holding, "quantity", 0) or 0) if not isinstance(holding, dict) else float(holding.get("quantity", 0) or 0)
            buy_price = float(getattr(holding, "buy_price", 0) or 0) if not isinstance(holding, dict) else float(holding.get("buy_price", 0) or 0)
            current_price = float(getattr(holding, "current_price", 0) or 0) if not isinstance(holding, dict) else float(holding.get("current_price", 0) or 0)
            # DB 동기화 직후 current_price가 비어있는 경우 실시간 캐시에서 보정
            if current_price <= 0:
                ticker_state = MarketDataService.get_state(ticker)
                if ticker_state and getattr(ticker_state, "current_price", 0) > 0:
                    current_price = float(ticker_state.current_price)

            if qty <= 0 or buy_price <= 0 or current_price <= 0:
                logger.info(
                    f"⏭️ Tick trade report skipped: invalid values "
                    f"(qty={qty}, buy={buy_price}, current={current_price})"
                )
                return

            profit_amt = (current_price - buy_price) * qty
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            AlertService.send_slack_alert(
                f"⏱️ [틱매매 10분 리포트] {ticker} 수익율 {profit_pct:+.2f}%, 수익금 {profit_amt:,.0f}원"
            )
            logger.info(
                f"✅ Tick trade report sent: {ticker} profit={profit_pct:+.2f}% amount={profit_amt:,.0f}"
            )
        except Exception as e:
            logger.error(f"❌ Error during tick trade report: {e}")

    # ── 경제지표 발표 감지 ────────────────────────────────────────────────

    @classmethod
    def _init_economic_baselines(cls):
        """서버 기동 시 각 FRED 시리즈의 현재 최신 관측일을 기준점으로 저장.
        이후 _check_economic_releases 에서 이 기준보다 새로운 날짜가 나오면 신규 발표로 판단.
        """
        from services.market.economic_calendar_service import EconomicCalendarService
        EconomicCalendarService.check_for_new_releases()  # 초기화 (변경 감지 없이 기준점 세팅)
        logger.info("✅ 경제지표 FRED 기준점(baseline) 초기화 완료")

    @classmethod
    def _check_economic_releases(cls):
        """미국 경제지표 발표 시각(8:31/9:16/10:01 ET)에 실행.
        FRED 관측일이 이전 기준보다 최신이면 신규 발표로 판단하여 macro 재계산.
        """
        from services.market.economic_calendar_service import EconomicCalendarService
        logger.info("🔍 경제지표 신규 발표 확인 중...")
        try:
            new_releases = EconomicCalendarService.check_for_new_releases()
            if not new_releases:
                logger.info("ℹ️ 신규 경제지표 발표 없음")
                return
            names = ", ".join(r["name"] for r in new_releases)
            series_ids = [r["series_id"] for r in new_releases]
            logger.info(f"🆕 신규 발표 {len(new_releases)}개 감지: {names}")
            MacroService.refresh_on_release(names, series_ids)
        except Exception as e:
            logger.error(f"❌ _check_economic_releases 오류: {e}")

    # ── VIX 스파이크 감지 ─────────────────────────────────────────────────
    # 마지막 알림 시각 (24h 쿨다운용)
    _vix_alert_last: dict = {}   # {"spike": datetime, "warning": datetime}

    @classmethod
    def _check_vix_spike(cls):
        """미국장 중 30분 간격으로 VIX 급등 스파이크 감지.

        발동 조건:
          - VIX 5거래일 변화율 > +40% AND VIX > 22  → 경보(🔴)
          - VIX > 35                                  → 비상(🚨)
        알림 쿨다운: 동일 레벨 알림은 24시간 이내 재발송 안 함.
        """
        try:
            import yfinance as yf
            vix_h = yf.Ticker("^VIX").history(period="10d")
            if len(vix_h) < 6:
                return
            vix_cur  = float(vix_h["Close"].iloc[-1])
            vix_5d_ago = float(vix_h["Close"].iloc[-6])   # 5거래일 전
            vix_5d_chg = (vix_cur - vix_5d_ago) / vix_5d_ago * 100 if vix_5d_ago > 0 else 0
            now = datetime.now()

            def _cooldown_ok(key: str, hours: int = 24) -> bool:
                last = cls._vix_alert_last.get(key)
                return last is None or (now - last).total_seconds() > hours * 3600

            # ── 비상: VIX > 35 ────────────────────────────────────────────
            if vix_cur > 35 and _cooldown_ok("emergency"):
                cls._vix_alert_last["emergency"] = now
                cls._vix_alert_last["spike"] = now  # spike 쿨다운도 리셋
                MacroService.invalidate_cache()       # regime 즉시 재계산 트리거
                msg = (
                    f"🚨 *VIX 비상경보* — VIX {vix_cur:.1f} (>35)\n"
                    f"5거래일 변화: {vix_5d_chg:+.1f}%\n"
                    f"➡️ 포지션 긴급 점검 필요. Regime 캐시 초기화됨."
                )
                AlertService.send_slack_alert(msg)
                logger.warning(f"🚨 VIX 비상: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")

            # ── 경보: VIX 5일 급등 +40% 이상 + VIX > 22 ──────────────────
            elif vix_5d_chg > 40 and vix_cur > 22 and _cooldown_ok("spike"):
                cls._vix_alert_last["spike"] = now
                MacroService.invalidate_cache()
                regime_data = MacroService.get_macro_data()
                regime = regime_data.get("market_regime", {})
                msg = (
                    f"🔴 *VIX 급등 경보* — VIX {vix_cur:.1f}\n"
                    f"5거래일 급등: *{vix_5d_chg:+.1f}%*\n"
                    f"시장 국면: {regime.get('status','?')} ({regime.get('regime_score','?')}/100)\n"
                    f"➡️ 단기 변동성 확대. 신규 매수 신중."
                )
                AlertService.send_slack_alert(msg)
                logger.warning(f"🔴 VIX 급등: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")

            # ── 정상화: VIX < 18 (이전에 경보 발동된 경우만) ────────────────
            elif vix_cur < 18 and ("spike" in cls._vix_alert_last or "emergency" in cls._vix_alert_last):
                last_alert = max(
                    cls._vix_alert_last.get("spike", now - timedelta(days=999)),
                    cls._vix_alert_last.get("emergency", now - timedelta(days=999)),
                )
                if (now - last_alert).total_seconds() > 3600 and _cooldown_ok("recovery", hours=48):
                    cls._vix_alert_last["recovery"] = now
                    msg = (
                        f"✅ *VIX 정상화* — VIX {vix_cur:.1f} (<18)\n"
                        f"이전 경보 이후 변동성 안정. 정상 운용 복귀."
                    )
                    AlertService.send_slack_alert(msg)
                    logger.info(f"✅ VIX 정상화: {vix_cur:.1f}")
            else:
                logger.debug(f"VIX 정상: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")

        except Exception as e:
            logger.error(f"❌ _check_vix_spike 오류: {e}")

    @classmethod
    def get_all_cached_prices(cls) -> dict:
        """모니터링 중인 전체 종목 캐시 데이터 반환.
        tier: 'high' = WebSocket 실시간, 'low' = 5분 폴링
        """
        all_states = MarketDataService.get_all_states()
        result = {}
        for ticker, ticker_state in all_states.items():
            result[ticker] = {
                "ticker": ticker,
                "name": ticker_state.name,
                "price": ticker_state.current_price,
                "rsi": ticker_state.rsi,
                "change": ticker_state.current_price - ticker_state.prev_close if ticker_state.prev_close > 0 else 0,
                "change_pct": ticker_state.change_rate,
                "fair_value_dcf": ticker_state.dcf_value,
                "target_buy_price": ticker_state.target_buy_price,
                "target_sell_price": ticker_state.target_sell_price,
                "ema5": ticker_state.ema.get(5),
                "ema10": ticker_state.ema.get(10),
                "ema20": ticker_state.ema.get(20),
                "ema60": ticker_state.ema.get(60),
                "ema120": ticker_state.ema.get(120),
                "ema200": ticker_state.ema.get(200),
                "tier": MarketDataService.get_tier(ticker),
            }
        return result
