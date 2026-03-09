"""
Technical indicator calculation service.
- Computes RSI, EMA, and Bollinger Bands from pandas Series/DataFrame inputs.
- Returns domain models (TechnicalIndicatorsSnapshot, BollingerBandsResult, etc.).
"""
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from models.schemas import (
    BollingerBandsLatest,
    TechnicalIndicatorsSnapshot,
)

# Default calculation periods
DEFAULT_RSI_PERIOD = 14
DEFAULT_BOLLINGER_WINDOW = 20
DEFAULT_BOLLINGER_NUM_STD = 2
EMA_SPANS = (5, 10, 20, 60, 100, 120, 200)
RSI_NEUTRAL_FALLBACK = 50.0


@dataclass
class BollingerBandsResult:
    """Bollinger Bands calculation result (upper/middle/lower Series)."""
    middle: pd.Series
    upper: pd.Series
    lower: pd.Series

    def to_latest(self) -> BollingerBandsLatest:
        """Return upper/middle/lower values for the latest bar as a snapshot."""
        if self.middle.empty:
            return BollingerBandsLatest()
        return BollingerBandsLatest(
            middle=round(float(self.middle.iloc[-1]), 2),
            upper=round(float(self.upper.iloc[-1]), 2),
            lower=round(float(self.lower.iloc[-1]), 2),
        )


class IndicatorService:
    """
    Technical indicator calculation service.
    All calculations accept pandas Series or DataFrame as input.
    """

    @staticmethod
    def compute_rsi_series(close_series: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
        """Calculate RSI (Relative Strength Index) time series."""
        if close_series.empty:
            return pd.Series()
        price_delta = close_series.diff(1)
        gains_series = price_delta.where(price_delta > 0, 0.0).rolling(window=period).mean()
        losses_series = (-price_delta.where(price_delta < 0, 0.0)).rolling(window=period).mean()
        rs_ratio = gains_series / losses_series
        rsi_series = 100 - (100 / (1 + rs_ratio))
        return rsi_series

    @staticmethod
    def compute_ema_series(close_series: pd.Series, period: int) -> pd.Series:
        """Calculate EMA (Exponential Moving Average) time series."""
        if close_series.empty:
            return pd.Series()
        return close_series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def compute_bollinger_bands(
        close_series: pd.Series,
        window: int = DEFAULT_BOLLINGER_WINDOW,
        num_std: int = DEFAULT_BOLLINGER_NUM_STD,
    ) -> BollingerBandsResult:
        """Calculate Bollinger Bands (upper/middle/lower)."""
        if close_series.empty:
            empty = pd.Series(dtype=float)
            return BollingerBandsResult(middle=empty, upper=empty, lower=empty)
        middle_series = close_series.rolling(window=window).mean()
        std_series = close_series.rolling(window=window).std()
        upper_series = middle_series + (std_series * num_std)
        lower_series = middle_series - (std_series * num_std)
        return BollingerBandsResult(middle=middle_series, upper=upper_series, lower=lower_series)

    @staticmethod
    def compute_latest_indicators_snapshot(close_series: pd.Series) -> Optional[TechnicalIndicatorsSnapshot]:
        """Calculate latest RSI/EMA snapshot and return as domain model."""
        if close_series.empty:
            return None
        numeric_series = pd.to_numeric(close_series, errors="coerce").dropna()
        if numeric_series.empty:
            return None

        ema_by_span: Dict[int, Optional[float]] = {}
        for span in EMA_SPANS:
            if len(numeric_series) >= span:
                last_ema = numeric_series.ewm(span=span, adjust=False).mean().iloc[-1]
                ema_by_span[span] = round(float(last_ema), 2)
            else:
                ema_by_span[span] = None

        rsi_series = IndicatorService.compute_rsi_series(numeric_series)
        if not rsi_series.empty and not np.isnan(rsi_series.iloc[-1]):
            rsi_value = round(float(rsi_series.iloc[-1]), 2)
        else:
            rsi_value = RSI_NEUTRAL_FALLBACK

        return TechnicalIndicatorsSnapshot(rsi=rsi_value, ema=ema_by_span)

    # --- Backward-compatible aliases (deprecated) ---
    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """[Deprecated] RSI series. Use compute_rsi_series instead."""
        return IndicatorService.compute_rsi_series(series, period=period)

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """[Deprecated] EMA series. Use compute_ema_series instead."""
        return IndicatorService.compute_ema_series(series, period)

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, window: int = 20, num_std: int = 2) -> dict:
        """[Deprecated] Bollinger Bands as dict. Use compute_bollinger_bands instead."""
        result = IndicatorService.compute_bollinger_bands(series, window=window, num_std=num_std)
        return {"middle": result.middle, "upper": result.upper, "lower": result.lower}

    @staticmethod
    def get_latest_indicators(series: pd.Series) -> dict:
        """[Deprecated] Latest indicators as dict. Use compute_latest_indicators_snapshot instead."""
        snapshot = IndicatorService.compute_latest_indicators_snapshot(series)
        if snapshot is None:
            return {}
        return snapshot.to_metrics_dict()
