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


def filter_kr(holdings: List[dict]) -> List[dict]:
    """Filter Korean stocks only from holdings list."""
    return [h for h in holdings if is_kr(str(h.get("ticker", "")))]


def filter_us(holdings: List[dict]) -> List[dict]:
    """Filter US stocks only from holdings list."""
    return [h for h in holdings if is_us(str(h.get("ticker", "")))]


def profit_pct(current: float, invested: float, decimals: int = 2) -> float:
    """Calculate return rate (%). Returns 0.0 if invested <= 0."""
    if invested <= 0:
        return 0.0
    return round((current - invested) / invested * 100, decimals)
