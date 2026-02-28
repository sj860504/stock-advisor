"""시장 구분 헬퍼 함수.

티커의 한국/미국 판별, 보유 종목 필터링, 수익률 계산 등
여러 서비스에서 공통으로 사용하는 유틸리티.
"""
from __future__ import annotations

from typing import List


def is_kr(ticker: str) -> bool:
    """한국 종목 여부 — 숫자로만 구성된 티커."""
    return str(ticker).isdigit()


def is_us(ticker: str) -> bool:
    """미국 종목 여부."""
    return not str(ticker).isdigit()


def market_type(ticker: str) -> str:
    """'KR' 또는 'US' 반환."""
    return "KR" if is_kr(ticker) else "US"


def filter_kr(holdings: List[dict]) -> List[dict]:
    """보유 목록에서 한국 종목만 반환."""
    return [h for h in holdings if is_kr(str(h.get("ticker", "")))]


def filter_us(holdings: List[dict]) -> List[dict]:
    """보유 목록에서 미국 종목만 반환."""
    return [h for h in holdings if is_us(str(h.get("ticker", "")))]


def profit_pct(current: float, invested: float, decimals: int = 2) -> float:
    """수익률(%) 계산. invested ≤ 0 이면 0.0 반환."""
    if invested <= 0:
        return 0.0
    return round((current - invested) / invested * 100, decimals)
