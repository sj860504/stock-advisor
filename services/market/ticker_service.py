import re
from typing import Optional
from utils.logger import get_logger

logger = get_logger("ticker_service")


class TickerService:
    """종목 코드 관리 및 변환 서비스"""

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        """티커 정규화 (대문자 변환, 공백 제거)"""
        if not ticker:
            return ""
        return ticker.strip().upper()

    @classmethod
    def resolve_ticker(cls, name_or_ticker: str) -> Optional[str]:
        """
        종목명 또는 티커를 입력받아 표준 티커 반환
        예: '삼성전자' -> '005930', 'tsla' -> 'TSLA'

        순서:
        1. 한국 종목코드 (6자리 숫자) → 그대로 반환
        2. 미국 티커 형태 (알파벳 1~5자리) → 대문자 변환
        3. DB stock_meta 에서 name_ko / name_en 검색
        """
        if not name_or_ticker:
            return None

        key = name_or_ticker.strip()

        # 1. 한국 종목코드 (6자리 숫자)
        if re.match(r'^\d{6}$', key):
            return key

        # 2. 미국 티커 (알파벳만 1~5자리, 대소문자 무관)
        if re.match(r'^[A-Za-z]{1,5}$', key):
            return key.upper()

        # 3. DB 종목명 검색
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
        """티커로 시장 구분 (KR / US)"""
        if not ticker:
            return "UNKNOWN"

        if ticker.isdigit():  # 005930
            return "KR"
        if ticker.endswith(".KS") or ticker.endswith(".KQ"):
            return "KR"

        return "US"
