"""
Yahoo Finance 기반 재무 데이터 수집 서비스.
- FCF per share, Beta, 성장률을 수집하여 DCF 계산에 활용합니다.
- KR 종목: ticker + '.KS' (KOSPI), 실패 시 '.KQ' (KOSDAQ) 순으로 시도합니다.
- 인메모리 캐시 (TTL: 24시간) 로 API 호출을 최소화합니다.
"""
import time
from typing import Optional
from dataclasses import dataclass, field
from utils.logger import get_logger

logger = get_logger("yfinance_service")

_CACHE_TTL_SEC = 86400  # 24시간


@dataclass
class YFinanceFundamentals:
    """yfinance 에서 추출한 DCF 입력용 재무 기초 데이터."""
    fcf_per_share: Optional[float]  # 주당 잉여현금흐름 (FCF / shares)
    beta: float = 1.0
    growth_rate: float = 0.05       # earnings/revenue growth (소수, e.g. 0.15)
    currency: str = "USD"
    source_ticker: str = ""         # yfinance 에 요청한 실제 티커 (e.g. "005930.KS")


class YFinanceService:
    """Yahoo Finance 에서 재무 기초 데이터를 조회하는 서비스."""

    # { original_ticker: (YFinanceFundamentals, fetched_at) }
    _cache: dict[str, tuple[YFinanceFundamentals, float]] = {}

    @classmethod
    def get_fundamentals(cls, ticker: str, market_type: str = "US") -> Optional[YFinanceFundamentals]:
        """
        ticker 기준 FCF·Beta·성장률 반환.
        캐시 유효(24h) 시 캐시 반환, 아니면 yfinance 조회.
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
        """
        yfinance API를 통해 종목의 재무 기초 데이터를 조회한다.

        Args:
            ticker: 종목 코드 (예: "AAPL", "005930").
            market_type: 마켓 구분. "US"(미국) 또는 "KR"(한국).
                - "US": ticker를 그대로 사용.
                - "KR": '{ticker}.KS'(KOSPI) 우선 시도, 실패 시 '{ticker}.KQ'(KOSDAQ) 폴백.

        Returns:
            YFinanceFundamentals: 조회 성공 시 FCF/share, beta, 성장률 등을 담은 데이터.
            None: yfinance 미설치이거나 모든 티커 조회가 실패한 경우.
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 패키지가 설치되지 않았습니다. pip install yfinance")
            return None

        yf_tickers = cls._build_yf_tickers(ticker, market_type)

        for yf_ticker in yf_tickers:
            result = cls._try_fetch_single_ticker(yf, yf_ticker)
            if result is not None:
                return result

        logger.warning("[yfinance] %s 모든 티커 조회 실패: %s", ticker, yf_tickers)
        return None

    @classmethod
    def _try_fetch_single_ticker(cls, yf, yf_ticker: str) -> Optional[YFinanceFundamentals]:
        """단일 yfinance 티커에 대해 재무 데이터를 조회한다."""
        try:
            yf_stock = yf.Ticker(yf_ticker)
            stock_info = yf_stock.get_info()
            if not stock_info:
                return None

            fcf_per_share = cls._extract_fcf_per_share(stock_info)
            beta = float(stock_info.get("beta") or 1.0)
            growth_rate = cls._calculate_blended_growth_rate(
                revenue_growth=float(stock_info.get("revenueGrowth") or 0.0),
                earnings_growth=float(stock_info.get("earningsGrowth") or 0.0),
            )
            currency = stock_info.get("currency", "USD")

            logger.info(
                "[yfinance] %s: FCF/share=%s, beta=%.2f, growth=%.3f",
                yf_ticker, fcf_per_share, beta, growth_rate,
            )
            return YFinanceFundamentals(
                fcf_per_share=fcf_per_share,
                beta=beta,
                growth_rate=growth_rate,
                currency=currency,
                source_ticker=yf_ticker,
            )

        except Exception as e:
            logger.debug("[yfinance] %s 조회 실패: %s", yf_ticker, e)
            return None

    @staticmethod
    def _extract_fcf_per_share(stock_info: dict) -> Optional[float]:
        """stock_info에서 주당 잉여현금흐름(FCF per share)을 계산한다."""
        fcf_total = stock_info.get("freeCashflow") or 0
        shares = stock_info.get("sharesOutstanding") or 0
        if fcf_total > 0 and shares > 0:
            return round(fcf_total / shares, 4)
        return None

    @staticmethod
    def _calculate_blended_growth_rate(revenue_growth: float, earnings_growth: float) -> float:
        """
        매출 성장률과 이익 성장률을 블렌딩하여 최종 성장률을 산출한다.

        블렌딩 규칙:
        - 매출 성장률(안정적) 70% + 이익 성장률(변동성 큼) 30%
        - 매출 성장률이 없으면 이익 성장률만 사용
        - 둘 다 없으면 5% 기본값
        - 이익 성장률은 [-0.20, 0.30] 범위로 클램핑
        - 최종 성장률은 [-0.15, 0.25] 범위로 클램핑
        """
        earnings_growth_clamped = max(-0.20, min(0.30, earnings_growth))

        if revenue_growth != 0.0:
            growth_rate = revenue_growth * 0.7 + earnings_growth_clamped * 0.3
        elif earnings_growth != 0.0:
            growth_rate = earnings_growth_clamped
        else:
            growth_rate = 0.05

        return max(-0.15, min(0.25, growth_rate))

    @staticmethod
    def _build_yf_tickers(ticker: str, market_type: str) -> list[str]:
        """
        yfinance 요청용 티커 목록 생성.
        - US: 그대로 사용
        - KR: '{ticker}.KS' 우선, 실패 시 '{ticker}.KQ'
        """
        if market_type == "KR":
            return [f"{ticker}.KS", f"{ticker}.KQ"]
        return [ticker]

    @classmethod
    def invalidate_cache(cls, ticker: str) -> None:
        """특정 종목 캐시 강제 만료."""
        cls._cache.pop(ticker, None)

    @classmethod
    def clear_cache(cls) -> None:
        """전체 캐시 초기화."""
        cls._cache.clear()