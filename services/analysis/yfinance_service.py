"""
Yahoo Finance-based financial data collection service.
- Collects FCF per share, Beta, growth rate for DCF calculations.
- KR stocks: tries ticker + '.KS' (KOSPI) first, then '.KQ' (KOSDAQ) on failure.
- In-memory cache (TTL: 24h) to minimize API calls.
"""
import time
from typing import Optional
from dataclasses import dataclass, field
from utils.logger import get_logger

logger = get_logger("yfinance_service")

_CACHE_TTL_SEC = 86400  # 24 hours


@dataclass
class YFinanceFundamentals:
    """DCF input fundamentals extracted from yfinance."""
    fcf_per_share: Optional[float]  # Free cash flow per share (FCF / shares)
    beta: float = 1.0
    growth_rate: float = 0.05       # earnings/revenue growth (decimal, e.g. 0.15)
    currency: str = "USD"
    source_ticker: str = ""         # Actual ticker sent to yfinance (e.g. "005930.KS")
    target_mean_price: Optional[float] = None  # Analyst consensus target price
    analyst_count: int = 0                     # numberOfAnalystOpinions


class YFinanceService:
    """Service for querying fundamental financial data from Yahoo Finance."""

    # { original_ticker: (YFinanceFundamentals, fetched_at) }
    _cache: dict = {}

    @classmethod
    def get_fundamentals(cls, ticker: str, market_type: str = "US") -> Optional[YFinanceFundamentals]:
        """
        Return FCF, Beta, growth rate for ticker.
        Returns cache if valid (24h), otherwise fetches from yfinance.
        """
        cached = cls._cache.get(ticker)
        if cached:
            data, fetched_at = cached
            if time.time() - fetched_at < _CACHE_TTL_SEC:
                return data

        result = cls._fetch(ticker, market_type)
        cls._cache[ticker] = (result, time.time())
        return result

    @classmethod
    def _fetch(cls, ticker: str, market_type: str) -> Optional[YFinanceFundamentals]:
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance package is not installed. pip install yfinance")
            return None

        yf_tickers = cls._build_yf_tickers(ticker, market_type)

        for yf_ticker in yf_tickers:
            try:
                t = yf.Ticker(yf_ticker)
                info = t.get_info()
                if not info:
                    continue

                fcf_total = info.get("freeCashflow") or 0
                shares = info.get("sharesOutstanding") or 0
                fcf_per_share: Optional[float] = None
                if fcf_total > 0 and shares > 0:
                    fcf_per_share = round(fcf_total / shares, 4)

                beta = float(info.get("beta") or 1.0)
                # Growth rate: 70% revenue growth (stable) + 30% earnings growth (volatile) blending
                # If no revenue growth, use earnings growth only; if neither, default 5%
                revenue_growth = float(info.get("revenueGrowth") or 0.0)
                earnings_growth = float(info.get("earningsGrowth") or 0.0)
                earnings_growth_clamped = max(-0.20, min(0.30, earnings_growth))
                if revenue_growth != 0.0:
                    growth_rate = revenue_growth * 0.7 + earnings_growth_clamped * 0.3
                elif earnings_growth != 0.0:
                    growth_rate = earnings_growth_clamped
                else:
                    growth_rate = 0.05
                growth_rate = max(-0.15, min(0.25, growth_rate))

                currency = info.get("currency", "USD")
                target_mean_price = info.get("targetMeanPrice")
                if target_mean_price is not None:
                    target_mean_price = float(target_mean_price)
                analyst_count = int(info.get("numberOfAnalystOpinions") or 0)

                logger.info(
                    f"[yfinance] {yf_ticker}: FCF/share={fcf_per_share}, "
                    f"beta={beta:.2f}, growth={growth_rate:.3f}, "
                    f"targetMeanPrice={target_mean_price}, analysts={analyst_count}"
                )
                return YFinanceFundamentals(
                    fcf_per_share=fcf_per_share,
                    beta=beta,
                    growth_rate=growth_rate,
                    currency=currency,
                    source_ticker=yf_ticker,
                    target_mean_price=target_mean_price,
                    analyst_count=analyst_count,
                )

            except Exception as e:
                logger.debug(f"[yfinance] {yf_ticker} lookup failed: {e}")
                continue

        logger.warning(f"[yfinance] {ticker} all ticker lookups failed: {yf_tickers}")
        return None

    @staticmethod
    def _build_yf_tickers(ticker: str, market_type: str) -> list[str]:
        """
        Build ticker list for yfinance requests.
        - US: use as-is
        - KR: try '{ticker}.KS' first, then '{ticker}.KQ'
        """
        if market_type == "KR":
            return [f"{ticker}.KS", f"{ticker}.KQ"]
        return [ticker]

    @classmethod
    def invalidate_cache(cls, ticker: str) -> None:
        """Force-expire cache for a specific ticker."""
        cls._cache.pop(ticker, None)

    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cache."""
        cls._cache.clear()
