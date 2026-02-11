import pandas as pd
import logging
from typing import Dict, Optional
from models.ticker_state import TickerState
from services.indicator_service import IndicatorService
from services.dcf_service import DcfService
from services.data_service import DataService # FinanceDataReader 湲곕컲

from utils.logger import get_logger

logger = get_logger("market_data_service")

class MarketDataService:
    """
    ?ㅼ떆媛??쒖옣 ?곗씠??諛?吏??愿由??쒕퉬??
    - 醫낅ぉ蹂?TickerState 愿由?
    - 珥덇린 ?곗씠??濡쒕뵫 (Warm-up)
    - ?ㅼ떆媛??곗씠???섏떊 諛?吏???낅뜲?댄듃
    """
    
    _states: Dict[str, TickerState] = {}
    
    @classmethod
    def register_ticker(cls, ticker: str):
        """醫낅ぉ ?깅줉 諛?珥덇린??""
        if ticker in cls._states:
            return
            
        logger.info(f"?넅 Registering ticker: {ticker}")
        cls._states[ticker] = TickerState(ticker=ticker)
        
        # 蹂꾨룄 ?ㅻ젅?쒖뿉??Warm-up ?섑뻾 (?대깽??猷⑦봽 李⑤떒 諛⑹?)
        import threading
        threading.Thread(target=cls._warm_up_data, args=(ticker,), daemon=True).start()

    @classmethod
    def _warm_up_data(cls, ticker: str):
        """怨쇨굅 ?곗씠??濡쒕뵫 諛?珥덇린 吏??怨꾩궛"""
        try:
            # 1. ?쇰큺 ?곗씠??媛?몄삤湲?(DataService ?쒖슜)
            df = DataService.get_price_history(ticker, days=300)
            if df.empty:
                logger.warning(f"?좑툘 No history data for {ticker}")
                return

            # 2. 湲곕낯 ?뺣낫 ?명똿
            last_row = df.iloc[-1]
            state = cls._states[ticker]
            state.prev_close = float(df.iloc[-2]['Close']) if len(df) > 1 else float(last_row['Close'])
            state.current_price = float(last_row['Close'])
            
            # 3. 吏??珥덇린 怨꾩궛
            indicators = IndicatorService.get_latest_indicators(df['Close'])
            emas = indicators.get('ema', {})
            rsi = indicators.get('rsi')
            
            state.update_indicators(emas=emas, dcf=0.0, rsi=rsi)
            
            logger.info(f"??Warm-up complete for {ticker}: Price={state.current_price}, EMA100={emas.get(100)}, RSI={rsi}")
            
        except Exception as e:
            logger.error(f"??Failed to warm up {ticker}: {e}", exc_info=True)

    @classmethod
    def on_realtime_data(cls, ticker: str, data: dict):
        """
        WebSocket ?ㅼ떆媛??곗씠???섏떊 ???몄텧
        data: ?꾩쿂由щ맂 ?뺤뀛?덈━ (price, open, high, low, rate, etc.)
        """
        if ticker not in cls._states:
            cls.register_ticker(ticker)
            
        state = cls._states[ticker]
        
        # 1. 湲곕낯 ?쒖꽭 ?낅뜲?댄듃
        state.current_price = float(data.get('price', state.current_price))
        state.open_price = float(data.get('open', state.open_price))
        state.high_price = float(data.get('high', state.high_price))
        state.low_price = float(data.get('low', state.low_price))
        state.change_rate = float(data.get('rate', state.change_rate))
        state.volume = int(data.get('volume', state.volume))
        
        # 2. ?ㅼ떆媛?吏???ш퀎???쒖꽦??
        state.recalculate_indicators()
        
        # logger.debug(f"??Update {ticker}: {state.current_price} ({state.change_rate}%)")

    @classmethod
    def get_state(cls, ticker: str) -> Optional[TickerState]:
        return cls._states.get(ticker)

    @classmethod
    def get_all_states(cls) -> Dict[str, TickerState]:
        return cls._states
