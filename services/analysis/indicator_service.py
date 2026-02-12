import pandas as pd
import numpy as np

class IndicatorService:
    """
    湲곗닠??吏??怨꾩궛 ?꾨떞 ?쒕퉬??
    紐⑤뱺 怨꾩궛? pandas Series ?먮뒗 DataFrame???낅젰諛쏆븘 泥섎━?⑸땲??
    """

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """RSI (Relative Strength Index) 怨꾩궛"""
        if series.empty: return pd.Series()
        
        delta = series.diff(1)
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """EMA (Exponential Moving Average) 怨꾩궛"""
        if series.empty: return pd.Series()
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, window: int = 20, num_std: int = 2) -> dict:
        """蹂쇰┛? 諛대뱶 怨꾩궛 (?곷떒, ?섎떒, 以묎컙??"""
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
        """理쒖떊 ?쒖젏??二쇱슂 吏?쒕뱾????踰덉뿉 諛섑솚 (?ㅼ?以꾨윭??"""
        if series.empty: return {}
        
        # EMA 紐⑥쓬
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
