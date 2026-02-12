import pandas as pd
import logging
from typing import Dict, Optional
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
    
    @classmethod
    def register_ticker(cls, ticker: str):
        """종목 등록 및 초기화"""
        if ticker in cls._states:
            return
            
        logger.info(f"🆕 Registering ticker: {ticker}")
        cls._states[ticker] = TickerState(ticker=ticker)
        
        # 별도 스레드에서 Warm-up 수행 (이벤트 루프 차단 방지)
        import threading
        threading.Thread(target=cls._warm_up_data, args=(ticker,), daemon=True).start()

    @classmethod
    def _warm_up_data(cls, ticker: str):
        """과거 데이터 로딩 및 초기 지표 계산"""
        try:
            # 1. 일봉 데이터 가져오기 (DataService 사용)
            df = DataService.get_price_history(ticker, days=300)
            if df.empty:
                logger.warning(f"⚠️ No history data for {ticker}")
                return

            # 2. 기본 정보 세팅
            last_row = df.iloc[-1]
            state = cls._states[ticker]
            state.prev_close = float(df.iloc[-2]['Close']) if len(df) > 1 else float(last_row['Close'])
            state.current_price = float(last_row['Close'])
            
            # 3. 지표 초기 계산
            indicators = IndicatorService.get_latest_indicators(df['Close'])
            emas = indicators.get('ema', {})
            rsi = indicators.get('rsi')
            
            state.update_indicators(emas=emas, dcf=0.0, rsi=rsi)
            
            logger.info(f"✅ Warm-up complete for {ticker}: Price={state.current_price}, EMA100={emas.get(100)}, RSI={rsi}")
            
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
