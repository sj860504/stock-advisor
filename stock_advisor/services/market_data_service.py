import pandas as pd
import logging
from typing import Dict, Optional
from stock_advisor.models.ticker_state import TickerState
from stock_advisor.services.indicator_service import IndicatorService
from stock_advisor.services.dcf_service import DcfService
from stock_advisor.services.data_service import DataService # FinanceDataReader 기반

logger = logging.getLogger("market_data_service")

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
        
        # 비동기로 초기 데이터 로딩하면 좋지만, 간단하게 동기로 처리 (서버 시작 시점)
        cls._warm_up_data(ticker)

    @classmethod
    def _warm_up_data(cls, ticker: str):
        """과거 데이터 로딩 및 초기 지표 계산"""
        try:
            # 1. 일봉 데이터 가져오기 (DataService 활용)
            df = DataService.get_price_history(ticker, days=300) # 200일 EMA 위해 넉넉히
            if df.empty:
                logger.warning(f"⚠️ No history data for {ticker}")
                return

            # 2. 전일 종가 등 기본 정보 세팅
            last_row = df.iloc[-1]
            state = cls._states[ticker]
            state.prev_close = float(df.iloc[-2]['Close']) if len(df) > 1 else float(last_row['Close'])
            state.current_price = float(last_row['Close']) # 초기값은 최근 종가
            
            # 3. EMA 초기 계산
            emas = IndicatorService.get_latest_indicators(df['Close']) # {ema5: ..., ema20: ...}
            
            # 4. DCF 계산 (DcfService 활용)
            # 재무 데이터가 필요하므로 DcfService 내부에서 처리
            try:
                dcf_value = DcfService.calculate_dcf(ticker)
            except:
                dcf_value = 0.0
                
            state.update_indicators(emas=emas, dcf=dcf_value)
            
            logger.info(f"✅ Warm-up complete for {ticker}: Price={state.current_price}, EMA20={emas.get('ema20')}, DCF={dcf_value}")
            
        except Exception as e:
            logger.error(f"❌ Failed to warm up {ticker}: {e}")

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
        
        # 프리장/정규장 구분은 시간으로 체크하거나 데이터 플래그 확인 필요
        # 여기서는 단순히 업데이트만 수행
        
        # 2. 실시간 EMA 재계산 (약식)
        # 완벽한 EMA 재계산은 전체 시계열이 필요하지만, 여기서는 '현재가'가 '오늘의 종가'라고 가정하고
        # 직전 EMA 값과 알파를 이용해 업데이트할 수 있음.
        # EMA_today = (Price_today * alpha) + (EMA_yesterday * (1-alpha))
        # 하지만 prev_ema를 정확히 관리해야 하므로, 간단히 로깅만 하거나
        # 중요: IndicatorService에서 계산된 emas에는 '오늘'분이 포함되어 있을 수 있음.
        
        # 여기서는 실시간 가격 변동 로그만 남기고, 
        # 정밀한 알고리즘 매매를 위해서는 별도 Strategy Loop에서 state를 참조하도록 함.
        
        # logger.debug(f"⚡ Update {ticker}: {state.current_price} ({state.change_rate}%)")

    @classmethod
    def get_state(cls, ticker: str) -> Optional[TickerState]:
        return cls._states.get(ticker)

    @classmethod
    def get_all_states(cls) -> Dict[str, TickerState]:
        return cls._states
