import time
import threading
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from models.ticker_state import TickerState
from services.analysis.indicator_service import IndicatorService
from services.analysis.dcf_service import DcfService
from services.market.data_service import DataService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("market_data_service")

# Max age (hours) to consider DB cache "fresh"
DB_FRESH_HOURS = 24
# Warm-up concurrency (KIS TPS compliance)
WARMUP_CONCURRENCY = 1

# Monitoring tiers
TIER_HIGH = "high"   # WebSocket real-time (top 20 tickers per market)
TIER_LOW  = "low"    # 5-min REST polling (remaining 80 tickers)

_EMA_SPANS = [5, 10, 20, 60, 120, 200]


class MarketDataService:
    """Real-time market data and per-ticker TickerState management."""

    _states: Dict[str, TickerState] = {}
    _tiers: Dict[str, str] = {}
    _warmup_semaphore = None

    # ── Internal utilities ─────────────────────────────────────────────────

    @classmethod
    def _get_semaphore(cls):
        if cls._warmup_semaphore is None:
            cls._warmup_semaphore = threading.Semaphore(WARMUP_CONCURRENCY)
        return cls._warmup_semaphore

    # Backward compatibility (kept for external callers of get_semaphore())
    get_semaphore = _get_semaphore

    @staticmethod
    def _normalize_kr_ticker(ticker: str) -> str:
        """Normalize KR ticker to 6-digit format."""
        t = str(ticker or "").strip()
        return t.zfill(6) if t.isdigit() and len(t) < 6 else t

    @staticmethod
    def _has_minimum_indicators(state: TickerState) -> bool:
        """Check if TickerState has minimum indicators required for strategy execution."""
        if not state or state.current_price <= 0:
            return False
        if state.rsi is None or float(state.rsi) <= 0:
            return False
        ema_val = state.ema.get(200) or state.ema.get(120) or state.ema.get(60)
        return bool(ema_val and float(ema_val) > 0)

    @classmethod
    def _load_indicators_from_db(cls, financials, state: TickerState) -> bool:
        """Apply DB financial data to TickerState.
        Returns True if data is fresh and meets minimum indicators, False otherwise.
        """
        if datetime.now() - financials.base_date >= timedelta(hours=DB_FRESH_HOURS):
            return False
        if financials.name:
            state.name = financials.name
        emas = {
            span: float(v)
            for span in _EMA_SPANS
            if (v := getattr(financials, f"ema{span}", None)) is not None
        }
        state.current_price = float(financials.current_price or 0.0)
        state.update_indicators(emas=emas, dcf=financials.dcf_value, rsi=financials.rsi)
        if state.ema.get(200):
            state.target_buy_price  = round(state.ema[200] * 1.01, 2)
            state.target_sell_price = round(state.ema[200] * 1.15, 2)
        return cls._has_minimum_indicators(state)

    @classmethod
    def _should_skip_by_market_hours(cls, ticker: str) -> bool:
        """Return True if ticker belongs to the opposite of currently open market (skip warm-up)."""
        from services.market.market_hour_service import MarketHourService
        from services.config.settings_service import SettingsService
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        is_kr_ticker = is_kr(ticker)
        if is_kr_ticker and is_us_open:
            logger.debug(f"⏭️ {ticker} KR ticker skipped (US market is open)")
            return True
        if not is_kr_ticker and is_kr_open:
            logger.debug(f"⏭️ {ticker} US ticker skipped (KR market is open)")
            return True
        return False

    @classmethod
    def _fetch_basic_price(cls, ticker: str) -> dict:
        """Fetch current price and basic financial info via KIS REST API."""
        from services.kis.kis_service import KisService
        from services.kis.fetch.kis_fetcher import KisFetcher
        from services.market.stock_meta_service import StockMetaService
        token = KisService.get_access_token()
        api_ticker = cls._normalize_kr_ticker(ticker) if is_kr(ticker) else ticker
        if is_kr(ticker):
            return KisFetcher.fetch_domestic_price(token, api_ticker)
        meta_row = StockMetaService.get_stock_meta(ticker)
        meta = {"api_market_code": getattr(meta_row, "api_market_code", "NAS")}
        info = KisFetcher.fetch_overseas_detail(token, ticker, meta=meta)
        return info or KisFetcher.fetch_overseas_price(token, ticker, meta=meta)

    @classmethod
    def _build_partial_metrics(cls, state: TickerState, basic_info: dict, df: pd.DataFrame) -> dict:
        """Build phase-1 metrics dict from KIS basic info + last daily candle close."""
        last_close  = float(df.iloc[-1]["Close"])
        basic_price = float(basic_info.get("price") or 0.0)
        return {
            "name":         state.name or basic_info.get("name"),
            "current_price": basic_price if basic_price > 0 else last_close,
            "market_cap":   basic_info.get("market_cap"),
            "per":          basic_info.get("per"),
            "pbr":          basic_info.get("pbr"),
            "eps":          basic_info.get("eps"),
            "bps":          basic_info.get("bps"),
            "high52":       basic_info.get("high52"),
            "low52":        basic_info.get("low52"),
            "volume":       basic_info.get("volume"),
            "amount":       basic_info.get("amount"),
            "base_date":    datetime.now(),
        }

    @staticmethod
    def _update_target_prices_from_snapshot(state: TickerState, snapshot) -> None:
        """Set EMA200-based target buy/sell prices on state."""
        ema200 = snapshot.ema.get(200) if snapshot else None
        if ema200:
            state.target_buy_price  = round(ema200 * 1.01, 2)
            state.target_sell_price = round(ema200 * 1.15, 2)

    @classmethod
    def _warmup_save_basic(cls, ticker: str, state: TickerState, basic_info: dict, df) -> dict:
        """Phase 1: Build and save basic metrics to DB. Returns partial_metrics dict."""
        from services.market.stock_meta_service import StockMetaService
        partial_metrics = cls._build_partial_metrics(state, basic_info, df)
        try:
            StockMetaService.save_financials(ticker, partial_metrics)
        except Exception as e:
            logger.error(f"⚠️ Failed to save base data for {ticker}: {e}")
        return partial_metrics

    @classmethod
    def _warmup_compute_indicators(cls, ticker: str, state: TickerState, df, partial_metrics: dict):
        """Phase 2: Compute indicators + DCF, apply to state. Returns (snapshot, rsi, dcf_val)."""
        state.prev_close    = float(df.iloc[-2]["Close"]) if len(df) > 1 else float(df.iloc[-1]["Close"])
        state.current_price = partial_metrics["current_price"]
        snapshot = IndicatorService.compute_latest_indicators_snapshot(df["Close"])
        rsi      = snapshot.rsi if snapshot else None
        dcf_val  = DcfService.calculate_dcf(ticker)
        state.update_indicators(emas=snapshot.ema if snapshot else {}, dcf=dcf_val, rsi=rsi)
        return snapshot, rsi, dcf_val

    @classmethod
    def _warmup_save_final(cls, ticker: str, partial_metrics: dict, snapshot, dcf_val: float) -> None:
        """Phase 3: Merge and save final metrics (indicators + DCF) to DB."""
        from services.market.stock_meta_service import StockMetaService
        try:
            StockMetaService.save_financials(ticker, {
                **partial_metrics,
                **(snapshot.to_metrics_dict() if snapshot else {}),
                "dcf_value": dcf_val,
            })
        except Exception as e:
            logger.error(f"⚠️ Failed to save final metrics for {ticker}: {e}")

    @classmethod
    def _full_api_warmup(cls, ticker: str, state: TickerState):
        """Full warm-up via KIS API calls. Must be called after acquiring semaphore."""
        api_ticker = cls._normalize_kr_ticker(ticker) if is_kr(ticker) else ticker
        basic_info = cls._fetch_basic_price(ticker)
        df         = DataService.get_price_history(api_ticker, days=300)

        if df.empty:
            logger.warning(f"⚠️ No history data for {api_ticker}. Skipping warm-up.")
            time.sleep(1.0)
            return

        partial_metrics          = cls._warmup_save_basic(ticker, state, basic_info, df)
        snapshot, rsi, dcf_val   = cls._warmup_compute_indicators(ticker, state, df, partial_metrics)
        cls._warmup_save_final(ticker, partial_metrics, snapshot, dcf_val)
        cls._update_target_prices_from_snapshot(state, snapshot)
        logger.info(
            f"✅ Full warm-up: {ticker} ({state.name}) "
            f"Price={state.current_price}, RSI={rsi}, DCF={dcf_val}, TargetBuy={state.target_buy_price}"
        )
        time.sleep(1.0)  # TPS compliance

    # ── Registration / Warm-up ─────────────────────────────────────────────

    @classmethod
    def register_ticker(cls, ticker: str, name: str = ""):
        """Register single ticker (delegates to register_batch)."""
        cls.register_batch([ticker])

    @classmethod
    def register_batch(cls, tickers: list):
        """Batch register tickers (DB-first load, background warm-up for incomplete tickers)."""
        normalized   = [cls._normalize_kr_ticker(t) for t in tickers if t]
        new_tickers  = [t for t in normalized if t and t not in cls._states]
        if not new_tickers:
            return

        from services.market.market_hour_service import MarketHourService
        from services.config.settings_service import SettingsService
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        analyze_kr = not is_us_open
        analyze_us = not is_kr_open

        filtered = [
            t for t in new_tickers
            if (is_kr(t) and analyze_kr) or (not is_kr(t) and analyze_us)
        ]
        if not filtered:
            logger.info(f"⏭️ All new tickers excluded by market-hours filter (KR={is_kr_open}, US={is_us_open})")
            return

        logger.info(f"🆕 Batch registering {len(filtered)} tickers (KR={is_kr_open}, US={is_us_open})...")

        from services.market.stock_meta_service import StockMetaService
        financials_map         = StockMetaService.get_batch_latest_financials(new_tickers)
        tickers_needing_warmup = cls._register_tickers_from_db(new_tickers, financials_map, analyze_kr, analyze_us)

        logger.info(
            f"🆕 Batch registered {len(new_tickers)} tickers "
            f"(warm-up {len(tickers_needing_warmup)}, KR={is_kr_open}, US={is_us_open})"
        )
        if tickers_needing_warmup:
            threading.Thread(target=cls._warm_up_batch, args=(tickers_needing_warmup,), daemon=True).start()

    @classmethod
    def _register_tickers_from_db(
        cls, new_tickers: list, financials_map: dict, analyze_kr: bool, analyze_us: bool
    ) -> list:
        """Register each ticker in states and return list needing warm-up."""
        from repositories.watchlist_repo import WatchlistRepo
        tickers_needing_warmup = []
        for ticker in new_tickers:
            state = TickerState(ticker=ticker)
            cls._states[ticker] = state
            financials = financials_map.get(ticker)
            if financials and cls._load_indicators_from_db(financials, state):
                logger.debug(f"✅ Batch DB load: {ticker}")
            else:
                is_active = (is_kr(ticker) and analyze_kr) or (not is_kr(ticker) and analyze_us)
                if is_active:
                    if financials:
                        logger.info(f"🔄 DB data incomplete for {ticker}, scheduling warm-up.")
                    tickers_needing_warmup.append(ticker)
            
            # Watchlist 편집값(자체 목표가/메모) 덮어쓰기
            wl_item = WatchlistRepo.get_item("sean", ticker)
            if wl_item:
                if wl_item.target_buy_price is not None:
                    state.target_buy_price = wl_item.target_buy_price
                if wl_item.target_sell_price is not None:
                    state.target_sell_price = wl_item.target_sell_price
        return tickers_needing_warmup

    @classmethod
    def _warm_up_batch(cls, tickers: list):
        """Warm-up multiple tickers sequentially (TPS compliance)."""
        for ticker in tickers:
            cls._warm_up_data(ticker)

    @classmethod
    def _warm_up_data(cls, ticker: str, _force: bool = False):
        """Single ticker warm-up orchestrator."""
        try:
            state = cls._states.get(ticker)
            if not state:
                return
            if not _force and cls._should_skip_by_market_hours(ticker):
                return

            from services.market.stock_meta_service import StockMetaService
            meta = StockMetaService.get_stock_meta(ticker)
            if meta:
                state.name = meta.name_ko
            else:
                StockMetaService.initialize_default_meta(ticker)

            financials = StockMetaService.get_latest_financials(ticker)
            if financials and cls._load_indicators_from_db(financials, state):
                logger.info(f"✅ DB load: {ticker} ({state.name}) Price={state.current_price}, RSI={state.rsi}")
                return

            logger.info(f"🔄 DB incomplete for {ticker}. Starting full API warm-up...")
            with cls._get_semaphore():
                cls._full_api_warmup(ticker, state)

            # Watchlist 편집값(자체 목표가/메모) 덮어쓰기 (warmup 타겟가격 오버라이드)
            from repositories.watchlist_repo import WatchlistRepo
            wl_item = WatchlistRepo.get_item("sean", ticker)
            if wl_item:
                if wl_item.target_buy_price is not None:
                    state.target_buy_price = wl_item.target_buy_price
                if wl_item.target_sell_price is not None:
                    state.target_sell_price = wl_item.target_sell_price

        except Exception as e:
            logger.error(f"❌ Warm-up failed for {ticker}: {e}", exc_info=True)

    # ── Real-time data ingestion ───────────────────────────────────────────

    @classmethod
    def on_realtime_data(cls, ticker: str, data: dict):
        """Called on WebSocket real-time data reception."""
        from datetime import datetime
        if ticker not in cls._states:
            cls.register_ticker(ticker)
        state = cls._states[ticker]
        state.current_price = float(data.get("price", state.current_price))
        state.open_price    = float(data.get("open",  state.open_price))
        state.high_price    = float(data.get("high",  state.high_price))
        state.low_price     = float(data.get("low",   state.low_price))
        state.change_rate   = float(data.get("rate",  state.change_rate))
        state.volume        = int(data.get("volume",  state.volume))
        state.last_updated  = datetime.now()
        state.recalculate_indicators()

    @classmethod
    def update_price_from_sync(cls, ticker: str, price: float, change_rate: float = None):
        """Update current price from portfolio sync/REST polling (no EMA recalculation)."""
        from datetime import datetime
        state = cls._states.get(ticker)
        if state and price > 0:
            state.current_price = price
            state.last_updated  = datetime.now()
            if change_rate is not None:
                state.change_rate = change_rate

    # ── State queries ─────────────────────────────────────────────────────

    @classmethod
    def get_state(cls, ticker: str) -> Optional[TickerState]:
        return cls._states.get(ticker)

    @classmethod
    def get_all_states(cls) -> Dict[str, TickerState]:
        return cls._states

    @classmethod
    def prune_states(cls, keep_tickers: set):
        """Remove non-universe ticker states from cache."""
        stale = [t for t in cls._states if t not in keep_tickers]
        for ticker in stale:
            cls._states.pop(ticker, None)
        if stale:
            logger.info(f"🧹 Pruned {len(stale)} stale states (kept {len(keep_tickers)}).")

    # ── Tier management ───────────────────────────────────────────────────

    @classmethod
    def set_tiers(cls, high_tickers: set, low_tickers: set):
        for t in high_tickers:
            cls._tiers[t] = TIER_HIGH
        for t in low_tickers:
            cls._tiers[t] = TIER_LOW

    @classmethod
    def get_tier(cls, ticker: str) -> str:
        return cls._tiers.get(ticker, TIER_LOW)

    @classmethod
    def get_low_tier_tickers(cls) -> List[str]:
        return [t for t, tier in cls._tiers.items() if tier == TIER_LOW]

    @classmethod
    def get_high_tier_tickers(cls) -> List[str]:
        return [t for t, tier in cls._tiers.items() if tier == TIER_HIGH]

    # ── View / signal generation ───────────────────────────────────────────

    @classmethod
    def build_trading_signals(cls, data: dict) -> dict:
        """Classify oversold/overbought/undervalued/EMA200 signals from cached data."""
        oversold, overbought, undervalued, ema200_support = [], [], [], []
        for ticker, info in data.items():
            rsi    = info.get("rsi")
            price  = info.get("price")
            dcf    = info.get("fair_value_dcf")
            ema200 = info.get("ema200")
            if rsi is not None and rsi < 30:
                oversold.append({"ticker": ticker, "rsi": rsi, "price": price, "signal": "BUY"})
            if rsi is not None and rsi > 70:
                overbought.append({"ticker": ticker, "rsi": rsi, "price": price, "signal": "SELL"})
            if dcf and price and price < dcf * 0.8:
                upside = ((dcf - price) / price) * 100
                undervalued.append({
                    "ticker": ticker, "price": price,
                    "dcf": round(dcf, 2), "upside_pct": round(upside, 1), "signal": "BUY",
                })
            if ema200 and price and abs(price - ema200) / ema200 < 0.02:
                ema200_support.append({
                    "ticker": ticker, "price": price,
                    "ema200": round(ema200, 2), "signal": "WATCH",
                })
        return {
            "oversold": oversold, "overbought": overbought,
            "undervalued": undervalued, "ema200_support": ema200_support,
        }

    @classmethod
    def build_watch_item(cls, ticker: str, state: TickerState) -> dict:
        """Build WatchItem dict from TickerState."""
        change = state.current_price - state.prev_close if state.prev_close > 0 else 0
        return {
            "ticker":      ticker,
            "price":       state.current_price,
            "change":      change,
            "change_rate": state.change_rate,
            "volume":      float(state.volume),
            "rsi":         state.rsi,
            "ma20":        state.ema.get(20) if state.ema else None,
        }

    @classmethod
    def get_watch_list(cls) -> list:
        """Watched ticker list (alphabetical order)."""
        result = [cls.build_watch_item(t, s) for t, s in cls._states.items()]
        result.sort(key=lambda x: x["ticker"])
        return result

    @classmethod
    def get_price_snapshots(cls) -> dict:
        """SSE 스트리밍용 경량 가격 스냅샷. 점수 계산 없이 price/_states만 반환."""
        result = {}
        for ticker, state in cls._states.items():
            if state.current_price > 0:
                result[ticker] = {
                    "price":      state.current_price,
                    "change_pct": state.change_rate,
                    "change":     state.current_price - state.prev_close if state.prev_close > 0 else 0,
                    "rsi":        state.rsi,
                    "last_updated": state.last_updated.isoformat() if state.last_updated else None,
                }
        return result
