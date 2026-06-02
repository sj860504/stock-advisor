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


def is_kr_preferred(ticker: str, name: str = "") -> bool:
    """KR 우선주 판별.
    - 종목명 끝에 '우' / '우B' / '우C' / '2우B' 등 포함 → 우선주 (가장 정확)
    - 종목코드 6자리 끝자리가 5/7/9 → 우선주 (보조 규칙)
      · 본주 005930 → 우선주 005935 / 005937 / 005939 패턴
    - 둘 중 하나라도 매치하면 True.
    """
    t = str(ticker or "")
    if not t.isdigit() or len(t) != 6:
        return False
    # 1) 종목명 기반 (정확)
    n = str(name or "")
    if n:
        # 종목명 끝에 '우' 또는 '우B/C' 패턴 — 우선주 표기
        # 예: 삼성전자우, 현대차2우B, 두산우, LG전자우
        if n.endswith("우") or "우B" in n or "우C" in n or "2우" in n or "3우" in n:
            return True
    # 2) 종목코드 끝자리 기반 (보조)
    # 본주는 보통 0으로 끝남. 끝자리 5/7/9는 대부분 우선주.
    if t[-1] in ("5", "7", "9"):
        return True
    return False


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
