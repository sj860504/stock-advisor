"""Market classification helper functions.

Utilities shared across services for KR/US ticker identification,
holdings filtering, return calculation, etc.
"""
from __future__ import annotations

from typing import List


def is_kr(ticker: str) -> bool:
    """Whether ticker is a Korean stock — digits-only ticker."""
    return str(ticker).isdigit()


def is_us(ticker: str) -> bool:
    """Whether ticker is a US stock."""
    return not str(ticker).isdigit()


def market_type(ticker: str) -> str:
    """Return 'KR' or 'US'."""
    return "KR" if is_kr(ticker) else "US"


def _ticker_of(h) -> str:
    """Extract ticker from holding (dict or object)."""
    return str(getattr(h, "ticker", None) or (h.get("ticker", "") if isinstance(h, dict) else ""))


def filter_kr(holdings) -> list:
    """Filter Korean stocks only from holdings list."""
    return [h for h in holdings if is_kr(_ticker_of(h))]


def filter_us(holdings) -> list:
    """Filter US stocks only from holdings list."""
    return [h for h in holdings if is_us(_ticker_of(h))]


def profit_pct(current: float, invested: float, decimals: int = 2) -> float:
    """Calculate return rate (%). Returns 0.0 if invested <= 0."""
    if invested <= 0:
        return 0.0
    return round((current - invested) / invested * 100, decimals)
