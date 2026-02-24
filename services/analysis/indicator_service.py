"""
기술적 지표 계산 전담 서비스.
- pandas Series/DataFrame을 입력받아 RSI, EMA, 볼린저 밴드를 계산합니다.
- 반환은 도메인 모델(TechnicalIndicatorsSnapshot, BollingerBandsResult 등)로 통일합니다.
"""
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from models.schemas import (
    BollingerBandsLatest,
    TechnicalIndicatorsSnapshot,
)

# 기본 계산 구간
DEFAULT_RSI_PERIOD = 14
DEFAULT_BOLLINGER_WINDOW = 20
DEFAULT_BOLLINGER_NUM_STD = 2
EMA_SPANS = (5, 10, 20, 60, 100, 120, 200)
RSI_NEUTRAL_FALLBACK = 50.0


@dataclass
class BollingerBandsResult:
    """볼린저 밴드 계산 결과 (상단/중간/하단 Series)."""
    middle: pd.Series
    upper: pd.Series
    lower: pd.Series

    def to_latest(self) -> BollingerBandsLatest:
        """최신 봉 기준 상/중/하단 값을 스냅샷으로 반환."""
        if self.middle.empty:
            return BollingerBandsLatest()
        return BollingerBandsLatest(
            middle=round(float(self.middle.iloc[-1]), 2),
            upper=round(float(self.upper.iloc[-1]), 2),
            lower=round(float(self.lower.iloc[-1]), 2),
        )


class IndicatorService:
    """
    기술적 지표 계산 전담 서비스.
    모든 계산은 pandas Series 또는 DataFrame을 입력받아 처리합니다.
    """

    @staticmethod
    def compute_rsi_series(close_series: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
        """RSI(상대강도지수) 시계열 계산."""
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
        """EMA(지수이동평균) 시계열 계산."""
        if close_series.empty:
            return pd.Series()
        return close_series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def compute_bollinger_bands(
        close_series: pd.Series,
        window: int = DEFAULT_BOLLINGER_WINDOW,
        num_std: int = DEFAULT_BOLLINGER_NUM_STD,
    ) -> BollingerBandsResult:
        """볼린저 밴드 계산 (상단·중간·하단)."""
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
        """최신 시점의 RSI·EMA 스냅샷을 계산해 도메인 모델로 반환."""
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

    # --- 하위 호환용 별칭 (deprecated) ---
    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """[하위호환] RSI 시계열. compute_rsi_series 사용 권장."""
        return IndicatorService.compute_rsi_series(series, period=period)

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        """[하위호환] EMA 시계열. compute_ema_series 사용 권장."""
        return IndicatorService.compute_ema_series(series, period)

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, window: int = 20, num_std: int = 2) -> dict:
        """[하위호환] 볼린저 밴드 dict 반환. compute_bollinger_bands 사용 권장."""
        result = IndicatorService.compute_bollinger_bands(series, window=window, num_std=num_std)
        return {"middle": result.middle, "upper": result.upper, "lower": result.lower}

    @staticmethod
    def get_latest_indicators(series: pd.Series) -> dict:
        """[하위호환] 최신 지표를 dict로 반환. compute_latest_indicators_snapshot 사용 권장."""
        snapshot = IndicatorService.compute_latest_indicators_snapshot(series)
        if snapshot is None:
            return {}
        return snapshot.to_metrics_dict()
