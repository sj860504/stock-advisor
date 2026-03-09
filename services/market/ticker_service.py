import re
from typing import Optional
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("ticker_service")


class TickerService:
    """Ticker code management and conversion service."""

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        """Normalize ticker (uppercase, strip whitespace)."""
        if not ticker:
            return ""
        return ticker.strip().upper()

    @classmethod
    def resolve_ticker(cls, name_or_ticker: str) -> Optional[str]:
        """Resolve stock name or ticker to standard ticker.
        e.g.: 'Samsung Electronics' -> '005930', 'tsla' -> 'TSLA'

        Order:
        1. KR ticker (6-digit number) -> return as-is
        2. US ticker format (1~5 alpha chars) -> uppercase
        3. DB stock_meta search by name_ko / name_en
        """
        if not name_or_ticker:
            return None

        key = name_or_ticker.strip()

        # 1. KR ticker (6-digit number)
        if re.match(r'^\d{6}$', key):
            return key

        # 2. US ticker (1~5 alpha chars, case-insensitive)
        if re.match(r'^[A-Za-z]{1,5}$', key):
            return key.upper()

        # 3. DB stock name search
        try:
            from services.market.stock_meta_service import StockMetaService
            ticker = StockMetaService.find_ticker_by_name(key)
            if ticker:
                return ticker
        except Exception as e:
            logger.warning(f"DB ticker lookup failed for '{key}': {e}")

        return None

    @classmethod
    def get_market_type(cls, ticker: str) -> str:
        """Determine market type from ticker (KR / US)."""
        if not ticker:
            return "UNKNOWN"

        if is_kr(ticker):  # 005930
            return "KR"
        if ticker.endswith(".KS") or ticker.endswith(".KQ"):
            return "KR"

        return "US"
