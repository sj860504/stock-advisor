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

class MarketDataService:
    """
    실시간 시장 데이터 및 지표 관리 서비스
    - 종목별 TickerState 관리
    - 초기 데이터 로딩 (Warm-up)
    - 실시간 데이터 수신 및 지표 업데이트
    """
    
    _states: Dict[str, TickerState] = {}
    _warmup_semaphore = None # 동속성 제한 (TPS 준수)
    
    @classmethod
    def get_semaphore(cls):
        if cls._warmup_semaphore is None:
            import threading
            # VTS TPS가 보통 2이므로, 안전하게 1~2개로 제한
            cls._warmup_semaphore = threading.Semaphore(1)
        return cls._warmup_semaphore
    
    @classmethod
    def register_ticker(cls, ticker: str, name: str = ""):
        """종목 등록 및 초기화 (단일)"""
        cls.register_batch([ticker])

    @classmethod
    def register_batch(cls, tickers: list):
        """여러 종목을 일괄 등록 및 초기화 (DB 우선 로드 최적화)"""
        # 중복 제거 및 미등록 종목 선별
        new_tickers = [t for t in tickers if t not in cls._states]
        if not new_tickers:
            return
            
        logger.info(f"🆕 Batch registering {len(new_tickers)} tickers...")
        
        # 1. DB에서 최신 지표 일괄 조회
        from services.market.stock_meta_service import StockMetaService
        batch_fins = StockMetaService.get_batch_latest_financials(new_tickers)
        
        to_warmup = []
        
        for ticker in new_tickers:
            state = TickerState(ticker=ticker)
            cls._states[ticker] = state
            
            latest_fin = batch_fins.get(ticker)
            use_db_data = False
            
            if latest_fin:
                # 종목명 업데이트
                if latest_fin.name:
                    state.name = latest_fin.name
                
                # 24시간 이내 데이터면 신선하다고 판단
                if datetime.now() - latest_fin.base_date < timedelta(hours=24):
                    # EMA 딕셔너리 구성
                    emas = {}
                    for span in [5, 10, 20, 60, 120, 200]:
                        val = getattr(latest_fin, f"ema{span}", None)
                        if val is not None:
                            emas[span] = float(val)
                    
                    state.current_price = float(latest_fin.current_price or 0.0)
                    state.update_indicators(emas=emas, dcf=latest_fin.dcf_value, rsi=latest_fin.rsi)
                    
                    if state.ema.get(200):
                        state.target_buy_price = round(state.ema[200] * 1.01, 2)
                        state.target_sell_price = round(state.ema[200] * 1.15, 2)
                        
                    use_db_data = True
                    logger.debug(f"✅ Batch DB Load: {ticker}")

            if not use_db_data:
                to_warmup.append(ticker)
        
        # 2. 데이터가 없는 종목들만 별도 스레드에서 Warm-up 수행
        if to_warmup:
            import threading
            threading.Thread(target=cls._warm_up_batch, args=(to_warmup,), daemon=True).start()

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

            # 1. 종목명 및 기본 메타 정보 로드 (DB 조회이므로 세마포어 밖에서 수행)
            from services.market.stock_meta_service import StockMetaService
            meta = StockMetaService.get_stock_meta(ticker)
            if meta:
                state.name = meta.name_ko
            else:
                StockMetaService.initialize_default_meta(ticker)

            # 2. DB에서 최신 지표(EMA, RSI) 조회 (세마포어 밖에서 즉시 처리)
            latest_fin = StockMetaService.get_latest_financials(ticker)
            use_db_data = False
            
            if latest_fin:
                # 24시간 이내 데이터면 신선하다고 판단 (Warm-up 용도로 충분)
                if datetime.now() - latest_fin.base_date < timedelta(hours=24):
                    logger.info(f"📦 Loading indicators from DB for {ticker} (Date: {latest_fin.base_date})")
                    
                    # EMA 딕셔너리 구성
                    emas = {}
                    for span in [5, 10, 20, 60, 120, 200]:
                        val = getattr(latest_fin, f"ema{span}", None)
                        if val is not None:
                            emas[span] = float(val)
                    
                    state.current_price = float(latest_fin.current_price or 0.0)
                    state.update_indicators(emas=emas, dcf=latest_fin.dcf_value, rsi=latest_fin.rsi)
                    
                    # 목표가 계산 (EMA200 기준)
                    if state.ema.get(200):
                        state.target_buy_price = round(state.ema[200] * 1.01, 2)
                        state.target_sell_price = round(state.ema[200] * 1.15, 2)
                        
                    use_db_data = True
                    logger.info(f"✅ DB Load complete for {ticker} ({state.name}): Price={state.current_price}, RSI={state.rsi}")

            # 3. API 호출이 필요한 경우에만 세마포어 진입 및 1초 대기
            if not use_db_data:
                sem = cls.get_semaphore()
                with sem:
                    # A. KIS API에서 기초 재무 정보 가져오기
                    from services.kis.kis_service import KisService
                    from services.kis.fetch.kis_fetcher import KisFetcher
                    token = KisService.get_access_token()
                    
                    is_kr = len(ticker) == 6 and ticker.isdigit()
                    basic_info = {}
                    if is_kr:
                        basic_info = KisFetcher.fetch_domestic_price(token, ticker)
                    else:
                        basic_info = KisFetcher.fetch_overseas_detail(token, ticker)
                        if not basic_info:
                             basic_info = KisFetcher.fetch_overseas_price(token, ticker)
                    
                    # B. 일봉 데이터 가져오기 (지표 계산용 기초 시세)
                    df = DataService.get_price_history(ticker, days=300)
                    
                    if df.empty:
                        logger.warning(f"⚠️ No history data for {ticker}. Skipping analysis.")
                        import time
                        time.sleep(1.0) # 에러 시에도 최소 지연 유지
                        return

                    # 1단계: 기초 데이터를 먼저 DB에 저장
                    try:
                        partial_metrics = {
                            "name": state.name or basic_info.get('name'),
                            "current_price": float(basic_info.get('price', df.iloc[-1]['Close'])),
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
                    
                    indicators = IndicatorService.get_latest_indicators(df['Close'])
                    emas = indicators.get('ema', {})
                    rsi = indicators.get('rsi')
                    dcf_val = DcfService.calculate_dcf(ticker)
                    state.update_indicators(emas=emas, dcf=dcf_val, rsi=rsi)
                    
                    # 3단계: 최종 업데이트
                    try:
                        final_metrics = {
                            **partial_metrics,
                            "rsi": rsi,
                            "ema": emas,
                            "dcf_value": dcf_val
                        }
                        StockMetaService.save_financials(ticker, final_metrics)
                    except Exception as se:
                        logger.error(f"⚠️ Failed to save final analysis: {se}")
                    
                    # 4단계: 목표가 산출
                    ema200 = emas.get(200)
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
