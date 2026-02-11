import pandas as pd
import numpy as np

class IndicatorService:
    """
    기술적 지표 계산 전담 서비스
    모든 계산은 pandas Series 또는 DataFrame을 입력받아 처리합니다.
    """

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """RSI (Relative Strength Index) 계산"""
        if series.empty: return pd.Series()
        
        delta = series.diff(1)
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """EMA (Exponential Moving Average) 계산"""
        if series.empty: return pd.Series()
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, window: int = 20, num_std: int = 2) -> dict:
        """볼린저 밴드 계산 (상단, 하단, 중간선)"""
        if series.empty: return {}
        
        sma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        
        upper = sma + (std * num_std)
        lower = sma - (std * num_std)
        
        return {
            "middle": sma,
            "upper": upper,
            "lower": lower
        }
        
    @staticmethod
    def get_latest_indicators(series: pd.Series) -> dict:
        """최신 시점의 주요 지표들을 한 번에 반환 (스케줄러용)"""
        if series.empty: return {}
        
        # EMA 모음
        emas = {}
        for span in [5, 10, 20, 60, 100, 120, 200]:
            if len(series) >= span:
                emas[span] = round(series.ewm(span=span, adjust=False).mean().iloc[-1], 2)
            else:
                emas[span] = None
                
        # RSI
        rsi_series = IndicatorService.calculate_rsi(series)
        rsi = round(rsi_series.iloc[-1], 2) if not rsi_series.empty and not np.isnan(rsi_series.iloc[-1]) else None
        
        return {
            "rsi": rsi,
            "ema": emas
        }
