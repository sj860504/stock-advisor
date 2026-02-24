import pandas as pd
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from models.ticker_state import TickerState
from services.analysis.indicator_service import IndicatorService
from services.analysis.dcf_service import DcfService
from services.market.data_service import DataService

from utils.logger import get_logger

logger = get_logger("market_data_service")

# DB 캐시를 "신선"하다고 볼 수 있는 시간(시간)
DB_FRESH_HOURS = 24
# Warm-up 동시 실행 수 (KIS TPS 준수)
WARMUP_CONCURRENCY = 1


class MarketDataService:
    """실시간 시장 데이터 및 종목별 TickerState 관리. Warm-up·실시간 수신·지표 업데이트."""

    _states: Dict[str, TickerState] = {}
    _warmup_semaphore = None

    @classmethod
    def get_semaphore(cls):
        if cls._warmup_semaphore is None:
            import threading
            cls._warmup_semaphore = threading.Semaphore(WARMUP_CONCURRENCY)
        return cls._warmup_semaphore
    
    @classmethod
    def _has_minimum_indicators(cls, state: TickerState) -> bool:
        """DB 캐시 데이터가 분석에 바로 사용 가능한 최소 조건인지 확인"""
        if not state or state.current_price <= 0:
            return False
        if state.rsi is None or float(state.rsi) <= 0:
            return False
        ema_val = state.ema.get(200) or state.ema.get(120) or state.ema.get(60)
        return bool(ema_val and float(ema_val) > 0)

    @classmethod
    def register_ticker(cls, ticker: str, name: str = ""):
        """종목 등록 및 초기화 (단일)"""
        cls.register_batch([ticker])

    @classmethod
    def _normalize_kr_ticker(cls, ticker: str) -> str:
        """한국 종목코드는 6자리로 통일 (KIS API 요구사항)."""
        if not ticker or not str(ticker).strip():
            return ticker
        t = str(ticker).strip()
        if t.isdigit() and len(t) < 6:
            return t.zfill(6)
        return t

    @classmethod
    def register_batch(cls, tickers: list):
        """여러 종목을 일괄 등록 및 초기화 (DB 우선 로드 최적화)"""
        normalized = [cls._normalize_kr_ticker(t) for t in tickers if t]
        new_tickers = [t for t in normalized if t and t not in cls._states]
        if not new_tickers:
            return

        # 시장 개장 여부에 따라 분석 대상 필터링
        from services.market.market_hour_service import MarketHourService
        from services.config.settings_service import SettingsService
        allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
        is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
        is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
        
        # 한국 시장 개장 중이면 미국 종목 제외, 미국 시장 개장 중이면 한국 종목 제외
        analyze_kr = not is_us_open  # 미국 시장이 닫혔을 때만 한국 종목 분석
        analyze_us = not is_kr_open  # 한국 시장이 닫혔을 때만 미국 종목 분석
        
        # 필터링된 종목만 등록
        filtered_tickers = []
        for ticker in new_tickers:
            is_kr_ticker = ticker.isdigit()
            if is_kr_ticker and not analyze_kr:
                logger.debug(f"⏭️ {ticker} 한국 종목 등록 스킵 (미국 시장 개장 중)")
                continue
            if not is_kr_ticker and not analyze_us:
                logger.debug(f"⏭️ {ticker} 미국 종목 등록 스킵 (한국 시장 개장 중)")
                continue
            filtered_tickers.append(ticker)
        
        if not filtered_tickers:
            logger.info(f"⏭️ 모든 종목이 시장 개장 필터링으로 제외됨 (KR개장={is_kr_open}, US개장={is_us_open})")
            return
            
        logger.info(f"🆕 Batch registering {len(filtered_tickers)} tickers (filtered from {len(new_tickers)}, KR개장={is_kr_open}, US개장={is_us_open})...")
        
        from services.market.stock_meta_service import StockMetaService
        latest_financials_by_ticker = StockMetaService.get_batch_latest_financials(filtered_tickers)
        tickers_needing_warmup = []

        for ticker in filtered_tickers:
            state = TickerState(ticker=ticker)
            cls._states[ticker] = state
            latest_financials = latest_financials_by_ticker.get(ticker)
            use_db_data = False
            if latest_financials:
                if latest_financials.name:
                    state.name = latest_financials.name
                if datetime.now() - latest_financials.base_date < timedelta(hours=DB_FRESH_HOURS):
                    emas = {}
                    for span in [5, 10, 20, 60, 120, 200]:
                        val = getattr(latest_financials, f"ema{span}", None)
                        if val is not None:
                            emas[span] = float(val)
                    state.current_price = float(latest_financials.current_price or 0.0)
                    state.update_indicators(emas=emas, dcf=latest_financials.dcf_value, rsi=latest_financials.rsi)
                    if state.ema.get(200):
                        state.target_buy_price = round(state.ema[200] * 1.01, 2)
                        state.target_sell_price = round(state.ema[200] * 1.15, 2)
                    if cls._has_minimum_indicators(state):
                        use_db_data = True
                        logger.debug(f"✅ Batch DB Load: {ticker}")
                    else:
                        logger.info(
                            f"🔄 Incomplete DB indicators for {ticker}. "
                            f"Warm-up required (price={state.current_price}, rsi={state.rsi})."
                        )
            if not use_db_data:
                tickers_needing_warmup.append(ticker)

        if tickers_needing_warmup:
            import threading
            threading.Thread(target=cls._warm_up_batch, args=(tickers_needing_warmup,), daemon=True).start()

    @classmethod
    def _warm_up_batch(cls, tickers: list):
        """여러 종목에 대해 순차적으로 Warm-up 수행 (TPS 준수)"""
        for ticker in tickers:
            cls._warm_up_data(ticker)

    @classmethod
    def _warm_up_data(cls, ticker: str):
        """과거 데이터 로딩 및 초기 지표 계산 (최적화)"""
        try:
            state = cls._states.get(ticker)
            if not state: return

            # 0. 시장 개장 여부에 따라 분석 스킵
            from services.market.market_hour_service import MarketHourService
            from services.config.settings_service import SettingsService
            allow_extended = SettingsService.get_int("STRATEGY_ALLOW_EXTENDED_HOURS", 1) == 1
            is_kr_ticker = ticker.isdigit()
            is_kr_open = MarketHourService.is_kr_market_open(allow_extended=allow_extended)
            is_us_open = MarketHourService.is_us_market_open(allow_extended=allow_extended)
            
            # 한국 시장 개장 중이면 미국 종목 분석 스킵, 미국 시장 개장 중이면 한국 종목 분석 스킵
            if is_kr_ticker and is_us_open:
                logger.debug(f"⏭️ {ticker} 한국 종목 분석 스킵 (미국 시장 개장 중)")
                return
            if not is_kr_ticker and is_kr_open:
                logger.debug(f"⏭️ {ticker} 미국 종목 분석 스킵 (한국 시장 개장 중)")
                return

            # 1. 종목명 및 기본 메타 정보 로드 (DB 조회이므로 세마포어 밖에서 수행)
            from services.market.stock_meta_service import StockMetaService
            meta = StockMetaService.get_stock_meta(ticker)
            if meta:
                state.name = meta.name_ko
            else:
                StockMetaService.initialize_default_meta(ticker)

            latest_financials = StockMetaService.get_latest_financials(ticker)
            use_db_data = False
            if latest_financials:
                if datetime.now() - latest_financials.base_date < timedelta(hours=DB_FRESH_HOURS):
                    logger.info(f"📦 Loading indicators from DB for {ticker} (Date: {latest_financials.base_date})")
                    emas = {}
                    for span in [5, 10, 20, 60, 120, 200]:
                        val = getattr(latest_financials, f"ema{span}", None)
                        if val is not None:
                            emas[span] = float(val)
                    state.current_price = float(latest_financials.current_price or 0.0)
                    state.update_indicators(emas=emas, dcf=latest_financials.dcf_value, rsi=latest_financials.rsi)
                    
                    # 목표가 계산 (EMA200 기준)
                    if state.ema.get(200):
                        state.target_buy_price = round(state.ema[200] * 1.01, 2)
                        state.target_sell_price = round(state.ema[200] * 1.15, 2)
                        
                    if cls._has_minimum_indicators(state):
                        use_db_data = True
                        logger.info(f"✅ DB Load complete for {ticker} ({state.name}): Price={state.current_price}, RSI={state.rsi}")
                    else:
                        logger.info(
                            f"🔄 DB data incomplete for {ticker} ({state.name}). "
                            f"Re-running warm-up (price={state.current_price}, RSI={state.rsi})."
                        )

            # 3. API 호출이 필요한 경우에만 세마포어 진입 및 1초 대기
            if not use_db_data:
                sem = cls.get_semaphore()
                with sem:
                    from services.kis.kis_service import KisService
                    from services.kis.fetch.kis_fetcher import KisFetcher
                    token = KisService.get_access_token()
                    api_ticker = cls._normalize_kr_ticker(ticker) if ticker.isdigit() else ticker
                    is_kr = len(api_ticker) == 6 and api_ticker.isdigit()
                    basic_info = {}
                    if is_kr:
                        basic_info = KisFetcher.fetch_domestic_price(token, api_ticker)
                    else:
                        # 해외 종목은 거래소 메타(NAS/NYS/AMS)를 함께 전달해야 가격 매핑 정확도가 올라간다.
                        meta_row = StockMetaService.get_stock_meta(ticker)
                        meta = {"api_market_code": getattr(meta_row, "api_market_code", "NAS")}
                        basic_info = KisFetcher.fetch_overseas_detail(token, ticker, meta=meta)
                        if not basic_info:
                             basic_info = KisFetcher.fetch_overseas_price(token, ticker, meta=meta)
                    
                    # B. 일봉 데이터 가져오기 (한국 종목은 6자리 코드로 요청)
                    df = DataService.get_price_history(api_ticker, days=300)
                    if df.empty:
                        logger.warning(f"⚠️ No history data for {api_ticker}. Skipping analysis.")
                        import time
                        time.sleep(1.0) # 에러 시에도 최소 지연 유지
                        return

                    # 1단계: 기초 데이터를 먼저 DB에 저장
                    try:
                        last_close = float(df.iloc[-1]["Close"])
                        basic_price = float(basic_info.get("price") or 0.0)
                        mapped_price = basic_price if basic_price > 0 else last_close
                        partial_metrics = {
                            "name": state.name or basic_info.get('name'),
                            "current_price": mapped_price,
                            "market_cap": basic_info.get("market_cap"),
                            "per": basic_info.get("per"),
                            "pbr": basic_info.get("pbr"),
                            "eps": basic_info.get("eps"),
                            "bps": basic_info.get("bps"),
                            "high52": basic_info.get("high52"),
                            "low52": basic_info.get("low52"),
                            "volume": basic_info.get("volume"),
                            "amount": basic_info.get("amount"),
                            "base_date": datetime.now()
                        }
                        StockMetaService.save_financials(ticker, partial_metrics)
                    except Exception as e:
                        logger.error(f"⚠️ Failed to save base data: {e}")

                    # 2단계: 분석 수행
                    last_row = df.iloc[-1]
                    state.prev_close = float(df.iloc[-2]['Close']) if len(df) > 1 else float(last_row['Close'])
                    state.current_price = partial_metrics["current_price"]
                    
                    indicators_snapshot = IndicatorService.compute_latest_indicators_snapshot(df["Close"])
                    emas = indicators_snapshot.ema if indicators_snapshot else {}
                    rsi = indicators_snapshot.rsi if indicators_snapshot else None
                    dcf_val = DcfService.calculate_dcf(ticker)
                    state.update_indicators(emas=emas, dcf=dcf_val, rsi=rsi)
                    # 3단계: 최종 업데이트 (DB 저장용 지표는 스냅샷에서 변환)
                    try:
                        indicators_for_db = indicators_snapshot.to_metrics_dict() if indicators_snapshot else {}
                        final_metrics = {
                            **partial_metrics,
                            **indicators_for_db,
                            "dcf_value": dcf_val,
                        }
                        StockMetaService.save_financials(ticker, final_metrics)
                    except Exception as se:
                        logger.error(f"⚠️ Failed to save final analysis: {se}")
                    
                    # 4단계: 목표가 산출
                    ema200 = (indicators_snapshot.ema.get(200) if indicators_snapshot else None)
                    if ema200:
                        state.target_buy_price = round(ema200 * 1.01, 2)
                        state.target_sell_price = round(ema200 * 1.15, 2)
                    
                    logger.info(f"✅ Full Warm-up complete for {ticker} ({state.name}): Price={state.current_price}, RSI={rsi}, DCF={dcf_val}, TargetBuy={state.target_buy_price}")
                    
                    # API 호출 후 1초 대기 (TPS 준수)
                    import time
                    time.sleep(1.0)
            
        except Exception as e:
            logger.error(f"❌ Failed to warm up {ticker}: {e}", exc_info=True)

    @classmethod
    def on_realtime_data(cls, ticker: str, data: dict):
        """
        WebSocket 실시간 데이터 수신 시 호출
        data: 전처리된 딕셔너리 (price, open, high, low, rate, etc.)
        """
        if ticker not in cls._states:
            cls.register_ticker(ticker)
            
        state = cls._states[ticker]
        
        # 1. 기본 시세 업데이트
        state.current_price = float(data.get('price', state.current_price))
        state.open_price = float(data.get('open', state.open_price))
        state.high_price = float(data.get('high', state.high_price))
        state.low_price = float(data.get('low', state.low_price))
        state.change_rate = float(data.get('rate', state.change_rate))
        state.volume = int(data.get('volume', state.volume))
        
        # 2. 실시간 지표 재계산 (특성상)
        state.recalculate_indicators()
        
        # logger.debug(f"⚡ Update {ticker}: {state.current_price} ({state.change_rate}%)")

    @classmethod
    def get_state(cls, ticker: str) -> Optional[TickerState]:
        return cls._states.get(ticker)

    @classmethod
    def get_all_states(cls) -> Dict[str, TickerState]:
        return cls._states

    @classmethod
    def prune_states(cls, keep_tickers: set):
        """대상 유니버스 외 종목 상태를 캐시에서 제거"""
        if not keep_tickers:
            return
        stale = [t for t in cls._states.keys() if t not in keep_tickers]
        for ticker in stale:
            cls._states.pop(ticker, None)
        if stale:
            logger.info(f"🧹 Pruned {len(stale)} stale ticker states (kept {len(keep_tickers)}).")
