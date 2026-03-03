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

# DB 캐시를 "신선"하다고 볼 수 있는 시간(시간)
DB_FRESH_HOURS = 24
# Warm-up 동시 실행 수 (KIS TPS 준수)
WARMUP_CONCURRENCY = 1

# 모니터링 티어
TIER_HIGH = "high"   # WebSocket 실시간 (시장별 상위 20종목)
TIER_LOW  = "low"    # 5분 주기 REST 폴링 (나머지 80종목)

_EMA_SPANS = [5, 10, 20, 60, 120, 200]


class MarketDataService:
    """실시간 시장 데이터 및 종목별 TickerState 관리."""

    _states: Dict[str, TickerState] = {}
    _tiers: Dict[str, str] = {}
    _warmup_semaphore = None

    # ── 내부 유틸 ──────────────────────────────────────────────────────────

    @classmethod
    def _get_semaphore(cls):
        if cls._warmup_semaphore is None:
            cls._warmup_semaphore = threading.Semaphore(WARMUP_CONCURRENCY)
        return cls._warmup_semaphore

    # 하위 호환 (외부에서 get_semaphore() 호출하는 곳이 있으면 유지)
    get_semaphore = _get_semaphore

    @staticmethod
    def _normalize_kr_ticker(ticker: str) -> str:
        """한국 종목코드를 6자리로 정규화."""
        t = str(ticker or "").strip()
        return t.zfill(6) if t.isdigit() and len(t) < 6 else t

    @staticmethod
    def _has_minimum_indicators(state: TickerState) -> bool:
        """TickerState가 전략 실행에 필요한 최소 지표를 갖추고 있는지 확인."""
        if not state or state.current_price <= 0:
            return False
        if state.rsi is None or float(state.rsi) <= 0:
            return False
        ema_val = state.ema.get(200) or state.ema.get(120) or state.ema.get(60)
        return bool(ema_val and float(ema_val) > 0)

    @classmethod
    def _load_indicators_from_db(cls, financials, state: TickerState) -> bool:
        """DB 재무 데이터를 TickerState에 적용.
        최신 데이터이고 최소 지표를 충족하면 True, 아니면 False.
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
        """현재 개장된 시장과 반대 시장 종목이면 True (warm-up 스킵 신호)."""
        from services.market.market_hour_service import MarketHourService
        from services.config.settings_service import SettingsService
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        is_kr_ticker = is_kr(ticker)
        if is_kr_ticker and is_us_open:
            logger.debug(f"⏭️ {ticker} 한국 종목 스킵 (미국 시장 개장 중)")
            return True
        if not is_kr_ticker and is_kr_open:
            logger.debug(f"⏭️ {ticker} 미국 종목 스킵 (한국 시장 개장 중)")
            return True
        return False

    @classmethod
    def _fetch_basic_price(cls, ticker: str) -> dict:
        """KIS REST API로 현재가 및 기초 재무 정보를 조회합니다."""
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
        """KIS 기초 정보 + 일봉 마지막 종가로 1단계 지표 dict를 생성합니다."""
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

    @classmethod
    def _full_api_warmup(cls, ticker: str, state: TickerState):
        """KIS API 호출 기반 full warm-up. 세마포어 진입 후 호출해야 합니다."""
        from services.market.stock_meta_service import StockMetaService

        api_ticker  = cls._normalize_kr_ticker(ticker) if is_kr(ticker) else ticker
        basic_info  = cls._fetch_basic_price(ticker)
        df          = DataService.get_price_history(api_ticker, days=300)

        if df.empty:
            logger.warning(f"⚠️ No history data for {api_ticker}. Skipping warm-up.")
            time.sleep(1.0)
            return

        # 1단계: 기초 데이터 저장
        partial_metrics = cls._build_partial_metrics(state, basic_info, df)
        try:
            StockMetaService.save_financials(ticker, partial_metrics)
        except Exception as e:
            logger.error(f"⚠️ Failed to save base data for {ticker}: {e}")

        # 2단계: 지표 계산 및 state 반영
        state.prev_close    = float(df.iloc[-2]["Close"]) if len(df) > 1 else float(df.iloc[-1]["Close"])
        state.current_price = partial_metrics["current_price"]

        snapshot = IndicatorService.compute_latest_indicators_snapshot(df["Close"])
        emas     = snapshot.ema if snapshot else {}
        rsi      = snapshot.rsi if snapshot else None
        dcf_val  = DcfService.calculate_dcf(ticker)
        state.update_indicators(emas=emas, dcf=dcf_val, rsi=rsi)

        # 3단계: 최종 지표 DB 저장
        try:
            final_metrics = {
                **partial_metrics,
                **(snapshot.to_metrics_dict() if snapshot else {}),
                "dcf_value": dcf_val,
            }
            StockMetaService.save_financials(ticker, final_metrics)
        except Exception as e:
            logger.error(f"⚠️ Failed to save final metrics for {ticker}: {e}")

        # 4단계: EMA200 기반 목표가 산출
        ema200 = snapshot.ema.get(200) if snapshot else None
        if ema200:
            state.target_buy_price  = round(ema200 * 1.01, 2)
            state.target_sell_price = round(ema200 * 1.15, 2)

        logger.info(
            f"✅ Full warm-up: {ticker} ({state.name}) "
            f"Price={state.current_price}, RSI={rsi}, DCF={dcf_val}, TargetBuy={state.target_buy_price}"
        )
        time.sleep(1.0)  # TPS 준수

    # ── 등록 / Warm-up ──────────────────────────────────────────────────────

    @classmethod
    def register_ticker(cls, ticker: str, name: str = ""):
        """단일 종목 등록 (register_batch 위임)."""
        cls.register_batch([ticker])

    @classmethod
    def register_batch(cls, tickers: list):
        """여러 종목을 일괄 등록 (DB 우선 로드, 부족한 종목은 백그라운드 warm-up)."""
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
            logger.info(f"⏭️ 모든 신규 종목이 시장 개장 필터로 제외됨 (KR={is_kr_open}, US={is_us_open})")
            return

        logger.info(f"🆕 Batch registering {len(filtered)} tickers (KR={is_kr_open}, US={is_us_open})...")

        from services.market.stock_meta_service import StockMetaService
        # new_tickers 전체 DB 로드 (비활성 시장 종목도 UI 표시용으로 등록)
        financials_map = StockMetaService.get_batch_latest_financials(new_tickers)
        tickers_needing_warmup = []

        for ticker in new_tickers:
            state = TickerState(ticker=ticker)
            cls._states[ticker] = state
            financials = financials_map.get(ticker)
            if financials and cls._load_indicators_from_db(financials, state):
                logger.debug(f"✅ Batch DB load: {ticker}")
            else:
                # warm-up은 현재 활성 시장 종목만 (API 부하 방지)
                is_active = (is_kr(ticker) and analyze_kr) or (not is_kr(ticker) and analyze_us)
                if is_active:
                    if financials:
                        logger.info(f"🔄 DB data incomplete for {ticker}, scheduling warm-up.")
                    tickers_needing_warmup.append(ticker)

        logger.info(
            f"🆕 Batch registered {len(new_tickers)} tickers "
            f"(warm-up {len(tickers_needing_warmup)}, KR={is_kr_open}, US={is_us_open})"
        )
        if tickers_needing_warmup:
            threading.Thread(
                target=cls._warm_up_batch,
                args=(tickers_needing_warmup,),
                daemon=True,
            ).start()

    @classmethod
    def _warm_up_batch(cls, tickers: list):
        """여러 종목을 순차적으로 warm-up (TPS 준수)."""
        for ticker in tickers:
            cls._warm_up_data(ticker)

    @classmethod
    def _warm_up_data(cls, ticker: str):
        """단일 종목 warm-up 오케스트레이터."""
        try:
            state = cls._states.get(ticker)
            if not state:
                return
            if cls._should_skip_by_market_hours(ticker):
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

        except Exception as e:
            logger.error(f"❌ Warm-up failed for {ticker}: {e}", exc_info=True)

    # ── 실시간 데이터 수신 ────────────────────────────────────────────────

    @classmethod
    def on_realtime_data(cls, ticker: str, data: dict):
        """WebSocket 실시간 데이터 수신 시 호출."""
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
        """포트폴리오 동기화·REST 폴링 시 현재가만 갱신 (EMA 재계산 없음)."""
        from datetime import datetime
        state = cls._states.get(ticker)
        if state and price > 0:
            state.current_price = price
            state.last_updated  = datetime.now()
            if change_rate is not None:
                state.change_rate = change_rate

    # ── 상태 조회 ─────────────────────────────────────────────────────────

    @classmethod
    def get_state(cls, ticker: str) -> Optional[TickerState]:
        return cls._states.get(ticker)

    @classmethod
    def get_all_states(cls) -> Dict[str, TickerState]:
        return cls._states

    @classmethod
    def prune_states(cls, keep_tickers: set):
        """유니버스 외 종목 상태를 캐시에서 제거."""
        stale = [t for t in cls._states if t not in keep_tickers]
        for ticker in stale:
            cls._states.pop(ticker, None)
        if stale:
            logger.info(f"🧹 Pruned {len(stale)} stale states (kept {len(keep_tickers)}).")

    # ── 티어 관리 ─────────────────────────────────────────────────────────

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

    # ── 뷰 / 신호 생성 ───────────────────────────────────────────────────

    @classmethod
    def build_trading_signals(cls, data: dict) -> dict:
        """캐시 데이터에서 과매도/과매수/저평가/EMA200 신호를 분류합니다."""
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
        """TickerState에서 WatchItem dict를 생성합니다."""
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
        """감시 중인 종목 목록 (ticker 알파벳순)."""
        result = [cls.build_watch_item(t, s) for t, s in cls._states.items()]
        result.sort(key=lambda x: x["ticker"])
        return result
