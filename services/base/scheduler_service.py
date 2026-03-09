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

# WebSocket real-time subscription limit (KIS live/paper both capped at 40 tickers, 20 per market)
WS_HIGH_TIER_COUNT = 20
# Tier LOW polling interval (minutes)
LOW_TIER_POLL_MINUTES = 5


class SchedulerService:
    _scheduler = None
    _ws_loop = None
    _ws_thread = None  # Prevent duplicate threads

    @classmethod
    def _register_econ_vix_jobs(cls, _ET: object) -> None:
        """Register economic indicator and VIX spike detection jobs (ET timezone)."""
        cls._scheduler.add_job(cls._check_economic_releases, 'cron',
                               day_of_week='mon-fri', hour=8, minute=31, timezone=_ET, id='econ_0830')
        cls._scheduler.add_job(cls._check_economic_releases, 'cron',
                               day_of_week='mon-fri', hour=9, minute=16, timezone=_ET, id='econ_0915')
        cls._scheduler.add_job(cls._check_economic_releases, 'cron',
                               day_of_week='mon-fri', hour=10, minute=1, timezone=_ET, id='econ_1000')
        cls._scheduler.add_job(cls._check_vix_spike, 'cron',
                               day_of_week='mon-fri', hour='9-15', minute='0,30', timezone=_ET, id='vix_spike_check')

    @classmethod
    def _register_scheduled_jobs(cls) -> None:
        """Batch register APScheduler cron/interval jobs."""
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")

        cls._scheduler.add_job(lambda: DataService.sync_daily_market_data(limit=100), 'cron', hour=4, minute=0)
        cls._scheduler.add_job(lambda: cls.manage_subscriptions(force_refresh=True), 'cron', hour=8, minute=30)
        cls._scheduler.add_job(cls.run_trading_strategy, 'interval', minutes=1)
        cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', hours=1)
        cls._scheduler.add_job(cls.report_daily_trade_history, 'cron', hour=9, minute=0)
        cls._scheduler.add_job(cls.run_rebalancing, 'cron', hour=9, minute=10)
        cls._scheduler.add_job(cls.run_sector_rebalance, 'cron', day_of_week='mon', hour=9, minute=20,
                               id='weekly_sector_rebalance')
        cls._scheduler.add_job(cls._refresh_low_tier_prices, 'interval', minutes=LOW_TIER_POLL_MINUTES)
        cls._scheduler.add_job(cls.sync_portfolio_periodic, 'interval', minutes=10)
        cls._scheduler.add_job(cls.report_tick_trade_status, 'interval', minutes=10)
        cls._register_econ_vix_jobs(_ET)

    @classmethod
    def _start_websocket_thread(cls) -> None:
        """Start KIS WebSocket dedicated daemon thread. Skips if already running."""
        if cls._ws_thread and cls._ws_thread.is_alive():
            logger.info("⏭️ WebSocket thread already running. Skipping duplicate start.")
            return

        def _run() -> None:
            """Run WebSocket connection in a dedicated event loop."""
            try:
                logger.info("🧵 WebSocket dedicated thread starting...")
                cls._ws_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(cls._ws_loop)
                cls._ws_loop.call_soon(lambda: asyncio.create_task(cls.manage_subscriptions_async()))
                logger.info("🚀 Launching guaranteed WebSocket connection loop...")
                cls._ws_loop.run_until_complete(kis_ws_service.connect())
            except Exception as e:
                logger.error(f"❌ Critical Error in WebSocket thread: {e}", exc_info=True)

        cls._ws_thread = threading.Thread(target=_run, name="KIS-WS-Thread", daemon=True)
        cls._ws_thread.start()

    @classmethod
    def start_scheduler(cls) -> None:
        """Initialize and start scheduler and WebSocket services."""
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()

            # 1. Register scheduled jobs
            cls._register_scheduled_jobs()

            # 2. Start KIS WebSocket service (dedicated thread)
            cls._start_websocket_thread()

            cls._scheduler.start()
            logger.info("✅ Scheduler and Real-time WebSocket Service Started.")

            # 3. Initialize FRED latest observation dates (set baselines)
            try:
                cls._init_economic_baselines()
            except Exception as e:
                logger.warning(f"⚠️ Economic indicator baseline init failed (ignored): {e}")

            # 4. KIS balance sync on startup + Slack notification
            try:
                TradingStrategyService._restore_enabled_state()
                PortfolioService.sync_with_kis("sean")
                logger.info("✅ Portfolio synced with KIS on startup.")
                cls._send_start_inquiry()
            except Exception as e:
                logger.error(f"❌ Failed to sync portfolio with KIS on startup: {e}")

    @classmethod
    def _send_start_inquiry(cls) -> None:
        """Send Slack inquiry about starting auto-trading."""
        msg = (
            "🤖 **Auto-trading engine is ready.**\n"
            "All analysis and trading processes are currently **DISABLED**.\n\n"
            "Would you like to start auto-trading?\n"
            "- [Start](http://localhost:8000/api/trading/start)\n"
            "- [Stop](http://localhost:8000/api/trading/stop)\n\n"
            "*Do not activate the links above if you prefer manual trading.*"
        )
        AlertService.send_slack_alert(msg)

    @classmethod
    def manage_subscriptions(cls, force_refresh: bool = False) -> None:
        """Subscription management method called from synchronous scheduler."""
        if cls._ws_loop and cls._ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(cls.manage_subscriptions_async(force_refresh=force_refresh), cls._ws_loop)
        else:
            logger.warning("⚠️ WebSocket loop not running. Skipping subscription refresh.")

    @classmethod
    def _build_ticker_universe(cls) -> tuple:
        """Build full universe from KRX/US top 100 + portfolio holdings.

        Returns: (all_kr, all_us, kr_holdings, us_holdings, target_universe, holdings_raw)
        """
        def _norm_ticker(t: str) -> str:
            """Normalize ticker string (strip whitespace, uppercase, zero-pad KR 6 digits)."""
            t = str(t or "").strip().upper()
            if not t:
                return ""
            if t.isdigit() and len(t) < 6:
                t = t.zfill(6)
            return t

        kr_tickers = [_norm_ticker(t) for t in DataService.get_top_krx_tickers(limit=100)]
        us_tickers = [_norm_ticker(t) for t in DataService.get_top_us_tickers(limit=100)]
        portfolio = PortfolioService.load_portfolio('sean')
        holdings_raw = [
            _norm_ticker(h.get('ticker') if isinstance(h, dict) else getattr(h, "ticker", ""))
            for h in portfolio
        ]
        kr_holdings = {t for t in holdings_raw if t and is_kr(t) and len(t) == 6}
        us_holdings = {t for t in holdings_raw if t and t.isalpha()}
        all_kr = list(dict.fromkeys(
            [t for t in kr_tickers if t and is_kr(t) and len(t) == 6] + list(kr_holdings)
        ))
        all_us = list(dict.fromkeys(
            [t for t in us_tickers if t and t.isalpha()] + list(us_holdings)
        ))
        target_universe = set(all_kr + all_us)
        return all_kr, all_us, kr_holdings, us_holdings, target_universe, holdings_raw

    @classmethod
    def _classify_tiers(cls, all_kr: list, all_us: list, kr_holdings: set, us_holdings: set, target_universe: set) -> tuple:
        """HIGH/LOW tier classification and MarketDataService registration. Returns (kr_high_set, us_high_set, high_set, low_set).
        KIS WebSocket limit: strictly 20 tickers per market (holdings first, fill remaining slots with top market-cap).
        """
        # KR: holdings first (max 20), fill remaining slots with top non-holding tickers
        kr_h = list(kr_holdings)[:WS_HIGH_TIER_COUNT]
        kr_h += [t for t in all_kr if t not in kr_holdings][:WS_HIGH_TIER_COUNT - len(kr_h)]
        kr_high_set = set(kr_h)
        # US: same principle
        us_h = list(us_holdings)[:WS_HIGH_TIER_COUNT]
        us_h += [t for t in all_us if t not in us_holdings][:WS_HIGH_TIER_COUNT - len(us_h)]
        us_high_set = set(us_h)
        high_set = kr_high_set | us_high_set
        low_set = target_universe - high_set
        MarketDataService.set_tiers(high_set, low_set)
        logger.info(f"📊 Tier classification: KR HIGH {len(kr_high_set)}/20, US HIGH {len(us_high_set)}/20, LOW {len(low_set)}")
        return kr_high_set, us_high_set, high_set, low_set

    @classmethod
    async def _subscribe_kr_tickers(cls, all_kr: list, kr_high_set: set) -> None:
        """Subscribe KR HIGH tier tickers via WebSocket."""
        for ticker in all_kr:
            if ticker in kr_high_set and len(ticker) == 6 and is_kr(ticker):
                await kis_ws_service.subscribe(ticker, market="KRX")
                await asyncio.sleep(0.05)

    @classmethod
    async def _subscribe_us_tickers_async(cls, all_us: list, us_high_set: set) -> None:
        """Subscribe US HIGH tier tickers via WebSocket."""
        market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS", "NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}
        us_meta_map = {m.ticker: m for m in StockMetaService.get_stock_meta_bulk(all_us)} if all_us else {}
        for ticker in all_us:
            if ticker in us_high_set and ticker.isalpha():
                meta = us_meta_map.get(ticker)
                raw_market = (meta.api_market_code if meta and meta.api_market_code else "NAS").upper()
                ws_market = market_map_4to3.get(raw_market, "NAS")
                await kis_ws_service.subscribe(ticker, market=ws_market)
                await asyncio.sleep(0.05)

    @classmethod
    async def manage_subscriptions_async(cls, force_refresh: bool = False) -> None:
        """Refresh HIGH(WebSocket)/LOW(5min polling) tier subscriptions."""
        logger.info(f"🔄 Refreshing Market Subscriptions (Top 100 + Portfolio, force={force_refresh})...")
        try:
            all_kr, all_us, kr_holdings, us_holdings, target_universe, holdings_raw = cls._build_ticker_universe()
            kr_high_set, us_high_set, high_set, low_set = cls._classify_tiers(
                all_kr, all_us, kr_holdings, us_holdings, target_universe
            )
            MarketDataService.prune_states(target_universe)
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
            is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
            watch_kr = not is_us_open
            watch_us = not is_kr_open
            logger.info(
                f"📺 KR open={is_kr_open}, US open={is_us_open} | "
                f"HIGH {len(high_set)} tickers (WebSocket), LOW {len(low_set)} tickers (5min polling)"
            )
            MarketDataService.register_batch(all_kr + all_us)  # Register all for UI display
            if watch_kr:
                await cls._subscribe_kr_tickers(all_kr, kr_high_set)
            if watch_us:
                await cls._subscribe_us_tickers_async(all_us, us_high_set)
            logger.info(
                f"✅ Subscriptions: WS HIGH {len(high_set)} tickers, LOW poll {len(low_set)} tickers | "
                f"KR={len(all_kr)}, US={len(all_us)}, Holdings={len(holdings_raw)}"
            )
        except Exception as e:
            logger.error(f"❌ Error in manage_subscriptions_async: {e}")

    @classmethod
    def run_trading_strategy(cls) -> None:
        """Trading strategy analysis and auto-trade execution."""
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
    def _build_gainers_list(cls, portfolio: list, all_states: dict) -> list:
        """Build list of gainers from portfolio holdings."""
        gainers = []
        for holding in portfolio:
            ticker = holding["ticker"] if isinstance(holding, dict) else getattr(holding, "ticker", "")
            state = all_states.get(ticker)
            if state and state.change_rate > 0:
                gainers.append({
                    "ticker": ticker,
                    "name": holding.get("name", ticker) if isinstance(holding, dict) else getattr(holding, "name", ticker),
                    "price": state.current_price,
                    "change": state.change_rate,
                    "market": "Real-time"
                })
        return gainers

    @classmethod
    def check_portfolio_hourly(cls) -> None:
        """Hourly portfolio status check and notification."""
        logger.info("🕒 Generating hourly portfolio report...")
        try:
            PortfolioService.sync_with_kis('sean')
            macro = MacroService.get_macro_data()
            all_states = MarketDataService.get_all_states()
            portfolio = PortfolioService.load_portfolio('sean')
            gainers = cls._build_gainers_list(portfolio, all_states)

            from services.notification.report_service import ReportService
            if gainers:
                msg = ReportService.format_hourly_gainers(gainers, macro)
                AlertService.send_slack_alert(msg)
                logger.info("📤 Hourly gainers report sent to Slack.")

            summary = PortfolioService.get_last_balance_summary()
            cash = PortfolioService.load_cash('sean')
            portfolio_msg = ReportService.format_portfolio_report(portfolio, cash, all_states, summary)
            AlertService.send_slack_alert(portfolio_msg)
            logger.info("📤 Hourly portfolio report sent to Slack.")
        except Exception as e:
            logger.error(f"❌ Error in check_portfolio_hourly: {e}")

    @classmethod
    def report_daily_trade_history(cls) -> None:
        """Daily 9AM: report last 24 hours trade history to Slack."""
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
    def run_rebalancing(cls) -> None:
        """Execute portfolio rebalancing."""
        logger.info("⚖️ Running daily Portfolio Rebalancing check...")
        try:
            PortfolioService.rebalance_portfolio("sean")
        except Exception as e:
            logger.error(f"❌ Error during rebalancing: {e}")

    @classmethod
    def run_sector_rebalance(cls) -> None:
        """Weekly sector weight rebalancing (every Monday 9:20 KST).

        Against target weights (Tech 50% / Value 30% / Financial 20%):
        - Deviation < 5%  -> skip
        - Deviation 5~10% -> half rebalance
        - Deviation > 10% -> full rebalance
        """
        logger.info("🔄 Weekly sector rebalancing started (every Monday)...")
        try:
            result = TradingStrategyService.run_sector_rebalance(user_id="sean")
            logger.info(f"✅ Sector rebalancing complete: {len(result.get('sold',[]))} sells, {len(result.get('bought',[]))} buys")
        except Exception as e:
            logger.error(f"❌ Sector rebalancing error: {e}")

    @classmethod
    def _filter_active_low_tickers(cls, low_tickers: list, is_kr_open: bool, is_us_open: bool) -> list:
        """Filter Tier LOW tickers based on open markets."""
        active_tickers = []
        for t in low_tickers:
            is_kr_t = is_kr(t)
            if is_kr_t and is_kr_open:
                active_tickers.append(t)
            elif not is_kr_t and is_us_open:
                active_tickers.append(t)
        return active_tickers

    @classmethod
    def _poll_ticker_price(cls, ticker: str, token: object, us_meta_map: dict) -> bool:
        """Poll single ticker current price and update MarketDataService. Returns True on success."""
        from services.kis.fetch.kis_fetcher import KisFetcher
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
            return True
        return False

    @classmethod
    def _poll_active_tickers(cls, active_tickers: list) -> None:
        """Poll active_tickers list via KIS REST API to refresh prices."""
        from services.kis.kis_service import KisService
        token = KisService.get_access_token()
        us_tickers_in_low = [t for t in active_tickers if not is_kr(t)]
        us_meta_map = (
            {m.ticker: m for m in StockMetaService.get_stock_meta_bulk(us_tickers_in_low)}
            if us_tickers_in_low else {}
        )
        success, fail = 0, 0
        for ticker in active_tickers:
            try:
                if cls._poll_ticker_price(ticker, token, us_meta_map):
                    success += 1
            except Exception as e:
                logger.debug(f"LOW tier poll failed {ticker}: {e}")
                fail += 1
        logger.info(f"✅ Tier LOW price refresh complete: success {success}, fail {fail}")

    @classmethod
    def _refresh_low_tier_prices(cls) -> None:
        """Refresh Tier HIGH/LOW ticker prices every 5 minutes via KIS REST API polling.
        - LOW tier: REST polling is the sole price update source
        - HIGH tier: WebSocket real-time is the primary source; REST polling as fallback
        """
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        if not is_kr_open and not is_us_open:
            return
        high_tickers = MarketDataService.get_high_tier_tickers()
        low_tickers = MarketDataService.get_low_tier_tickers()
        all_poll_tickers = high_tickers + low_tickers
        if not all_poll_tickers:
            return
        active_tickers = cls._filter_active_low_tickers(all_poll_tickers, is_kr_open, is_us_open)
        if not active_tickers:
            return
        high_active = len([t for t in high_tickers if t in set(active_tickers)])
        logger.info(f"⏱️ Price refresh started: {len(active_tickers)} tickers (HIGH {high_active} + LOW {len(active_tickers)-high_active})")
        try:
            cls._poll_active_tickers(active_tickers)
        except Exception as e:
            logger.error(f"❌ _refresh_low_tier_prices error: {e}")

    @classmethod
    def sync_portfolio_periodic(cls) -> None:
        """Execute periodic portfolio DB sync every 10 minutes."""
        logger.info("🔄 Running periodic Portfolio DB sync with KIS...")
        try:
            PortfolioService.sync_with_kis("sean")
        except Exception as e:
            logger.error(f"❌ Error during portfolio sync: {e}")

    @classmethod
    def _extract_holding_financial_data(cls, holding: object, ticker: str) -> tuple:
        """Extract qty, buy_price, current_price from holding object. Corrected with real-time cache."""
        is_dict = isinstance(holding, dict)
        qty = float(holding.get("quantity", 0) or 0) if is_dict else float(getattr(holding, "quantity", 0) or 0)
        buy_price = float(holding.get("buy_price", 0) or 0) if is_dict else float(getattr(holding, "buy_price", 0) or 0)
        current_price = float(holding.get("current_price", 0) or 0) if is_dict else float(getattr(holding, "current_price", 0) or 0)
        if current_price <= 0:
            ticker_state = MarketDataService.get_state(ticker)
            if ticker_state and getattr(ticker_state, "current_price", 0) > 0:
                current_price = float(ticker_state.current_price)
        return qty, buy_price, current_price

    @classmethod
    def _get_tick_ticker(cls) -> str:
        """Validate tick trading settings and return ticker. Returns empty string if disabled/unset."""
        if SettingsService.get_int("STRATEGY_TICK_ENABLED", 0) != 1:
            logger.info("⏭️ Tick trade report skipped: STRATEGY_TICK_ENABLED=0")
            return ""
        ticker = (SettingsService.get_setting("STRATEGY_TICK_TICKER", "005930") or "").strip().upper()
        if not ticker:
            logger.info("⏭️ Tick trade report skipped: empty tick ticker")
        return ticker

    @classmethod
    def report_tick_trade_status(cls) -> None:
        """10-minute tick trade P&L status report."""
        try:
            logger.info("⏱️ Running 10-minute tick trade report...")
            ticker = cls._get_tick_ticker()
            if not ticker:
                return
            PortfolioService.sync_with_kis("sean")
            holdings = PortfolioService.load_portfolio("sean")
            holding = next(
                (h for h in holdings if (h.get("ticker") if isinstance(h, dict) else getattr(h, "ticker")) == ticker),
                None,
            )
            if not holding:
                logger.info(f"ℹ️ Tick trade report: no holding for {ticker}")
                AlertService.send_slack_alert(f"⏱️ [Tick Trade 10min Report] {ticker} no holdings")
                return
            qty, buy_price, current_price = cls._extract_holding_financial_data(holding, ticker)
            if qty <= 0 or buy_price <= 0 or current_price <= 0:
                logger.info(f"⏭️ Tick trade report skipped: invalid values (qty={qty}, buy={buy_price}, current={current_price})")
                return
            profit_amt = (current_price - buy_price) * qty
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            AlertService.send_slack_alert(
                f"⏱️ [Tick Trade 10min Report] {ticker} return {profit_pct:+.2f}%, profit {profit_amt:,.0f} KRW"
            )
            logger.info(f"✅ Tick trade report sent: {ticker} profit={profit_pct:+.2f}% amount={profit_amt:,.0f}")
        except Exception as e:
            logger.error(f"❌ Error during tick trade report: {e}")

    # ── Economic release detection ────────────────────────────────────────────────

    @classmethod
    def _init_economic_baselines(cls) -> None:
        """Save current latest observation date for each FRED series as baseline on server startup.
        Subsequent _check_economic_releases calls treat dates newer than this baseline as new releases.
        """
        from services.market.economic_calendar_service import EconomicCalendarService
        EconomicCalendarService.check_for_new_releases()  # Initialize (set baselines without change detection)
        logger.info("✅ Economic indicator FRED baseline initialization complete")

    @classmethod
    def _check_economic_releases(cls) -> None:
        """Run at US economic release times (8:31/9:16/10:01 ET).
        If FRED observation date is newer than previous baseline, treat as new release and recalculate macro.
        """
        from services.market.economic_calendar_service import EconomicCalendarService
        logger.info("🔍 Checking for new economic indicator releases...")
        try:
            new_releases = EconomicCalendarService.check_for_new_releases()
            if not new_releases:
                logger.info("ℹ️ No new economic indicator releases")
                return
            names = ", ".join(r["name"] for r in new_releases)
            series_ids = [r["series_id"] for r in new_releases]
            logger.info(f"🆕 {len(new_releases)} new releases detected: {names}")
            MacroService.refresh_on_release(names, series_ids)
        except Exception as e:
            logger.error(f"❌ _check_economic_releases error: {e}")

    # ── VIX spike detection ─────────────────────────────────────────────────
    # Last alert time (24h cooldown)
    _vix_alert_last: dict = {}   # {"spike": datetime, "warning": datetime}

    @classmethod
    def _fetch_vix_metrics(cls) -> tuple:
        """Return VIX current value and 5-day change rate via yfinance. Returns (None, None) if insufficient data."""
        import yfinance as yf
        vix_h = yf.Ticker("^VIX").history(period="10d")
        if len(vix_h) < 6:
            return None, None
        vix_cur = float(vix_h["Close"].iloc[-1])
        vix_5d_ago = float(vix_h["Close"].iloc[-6])
        vix_5d_chg = (vix_cur - vix_5d_ago) / vix_5d_ago * 100 if vix_5d_ago > 0 else 0
        return vix_cur, vix_5d_chg

    @classmethod
    def _send_emergency_alert(cls, vix_cur: float, vix_5d_chg: float, now: datetime) -> None:
        """Send VIX emergency alert (>35) and record state."""
        cls._vix_alert_last["emergency"] = now
        cls._vix_alert_last["spike"] = now
        MacroService.invalidate_cache()
        msg = (
            f"🚨 *VIX Emergency Alert* — VIX {vix_cur:.1f} (>35)\n"
            f"5-day change: {vix_5d_chg:+.1f}%\n"
            f"➡️ Urgent position review required. Regime cache invalidated."
        )
        AlertService.send_slack_alert(msg)
        logger.warning(f"🚨 VIX emergency: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")

    @classmethod
    def _send_spike_alert(cls, vix_cur: float, vix_5d_chg: float, now: datetime) -> None:
        """Send VIX spike alert (5-day +40%+ and VIX>22) and record state."""
        cls._vix_alert_last["spike"] = now
        MacroService.invalidate_cache()
        regime = MacroService.get_macro_data().get("market_regime")
        msg = (
            f"🔴 *VIX Spike Alert* — VIX {vix_cur:.1f}\n"
            f"5-day surge: *{vix_5d_chg:+.1f}%*\n"
            f"Market regime: {getattr(regime, 'status', '?')} ({getattr(regime, 'regime_score', '?')}/100)\n"
            f"➡️ Short-term volatility expansion. Be cautious with new buys."
        )
        AlertService.send_slack_alert(msg)
        logger.warning(f"🔴 VIX spike: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")

    @classmethod
    def _send_recovery_alert(cls, vix_cur: float, now: datetime) -> None:
        """Send VIX normalization (<18) alert and record state."""
        cls._vix_alert_last["recovery"] = now
        msg = (
            f"✅ *VIX Normalized* — VIX {vix_cur:.1f} (<18)\n"
            f"Volatility stabilized since last alert. Returning to normal operations."
        )
        AlertService.send_slack_alert(msg)
        logger.info(f"✅ VIX normalized: {vix_cur:.1f}")

    @classmethod
    def _check_vix_recovery(cls, vix_cur: float, now: datetime, cooldown_ok_fn: object) -> None:
        """Check VIX normalization (<18) condition and send alert if needed."""
        if not ("spike" in cls._vix_alert_last or "emergency" in cls._vix_alert_last):
            return
        last_alert = max(
            cls._vix_alert_last.get("spike", now - timedelta(days=999)),
            cls._vix_alert_last.get("emergency", now - timedelta(days=999)),
        )
        if (now - last_alert).total_seconds() > 3600 and cooldown_ok_fn("recovery", hours=48):
            cls._send_recovery_alert(vix_cur, now)

    @classmethod
    def _check_vix_spike(cls) -> None:
        """Detect VIX spike every 30 minutes during US market hours.

        Trigger conditions:
          - VIX 5-day change > +40% AND VIX > 22  -> alert
          - VIX > 35                                -> emergency
        Alert cooldown: same-level alerts are not re-sent within 24 hours.
        """
        try:
            vix_cur, vix_5d_chg = cls._fetch_vix_metrics()
            if vix_cur is None:
                return
            now = datetime.now()

            def _cooldown_ok(key: str, hours: int = 24) -> bool:
                """Check if the specified hours have elapsed since the last alert for the given key."""
                last = cls._vix_alert_last.get(key)
                return last is None or (now - last).total_seconds() > hours * 3600

            if vix_cur > 35 and _cooldown_ok("emergency"):
                cls._send_emergency_alert(vix_cur, vix_5d_chg, now)
            elif vix_5d_chg > 40 and vix_cur > 22 and _cooldown_ok("spike"):
                cls._send_spike_alert(vix_cur, vix_5d_chg, now)
            elif vix_cur < 18:
                cls._check_vix_recovery(vix_cur, now, _cooldown_ok)
            else:
                logger.debug(f"VIX normal: {vix_cur:.1f} (5d: {vix_5d_chg:+.1f}%)")
        except Exception as e:
            logger.error(f"❌ _check_vix_spike error: {e}")

    @classmethod
    def get_all_cached_prices(cls, limit: int = 1000) -> dict:
        """Return cached data for all monitored tickers.
        tier: 'high' = WebSocket real-time, 'low' = 5min polling
        limit: max number of tickers to return (default 1000)
        """
        all_states = MarketDataService.get_all_states()
        tiers = MarketDataService._tiers  # Avoid repeated calls inside loop
        result = {}
        for ticker, ticker_state in list(all_states.items())[:limit]:
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
                "tier": tiers.get(ticker, "low"),
                "last_updated": ticker_state.last_updated.isoformat() if ticker_state.last_updated else None,
            }
        return result

    start = start_scheduler  # backward compat alias
