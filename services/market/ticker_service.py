import logging
import re
from typing import Optional

logger = logging.getLogger("ticker_service")

class TickerService:
    """
    종목 코드 관리 및 변환 서비스
    """
    # 종목명 -> 티커 매핑 (한글 종목명 지원을 위해 확장 가능)
    NAME_TO_TICKER = {
        "테슬라": "TSLA",
        "애플": "AAPL",
        "엔비디아": "NVDA",
        "마이크로소프트": "MSFT",
        "구글": "GOOGL",
        "아마존": "AMZN",
        "삼성전자": "005930",
        "SK하이닉스": "000660",
        "카카오": "035720",
        "네이버": "035420",
        "현대차": "005380"
    }

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
        """
        if not name_or_ticker:
            return None
            
        key = name_or_ticker.strip()
        
        # 1. 매핑 테이블 확인
        if key in cls.NAME_TO_TICKER:
            return cls.NAME_TO_TICKER[key]
            
        # 2. 이미 티커 형태인지 확인 (알파벳 대문자 or 숫자 6자리)
        # 한국 종목코드 (6자리 숫자)
        if re.match(r'^\d{6}$', key):
            return key
            
        # 미국 티커 (알파벳 1~5자리)
        if re.match(r'^[A-Z]{1,5}$', key.upper()):
            return key.upper()
            
        return None

    @classmethod
    def get_market_type(cls, ticker: str) -> str:
        """티커로 시장 구분 (KR / US)"""
        if not ticker:
            return "UNKNOWN"
            
        if ticker.isdigit(): # 005930
            return "KR"
        if ticker.endswith(".KS") or ticker.endswith(".KQ"):
            return "KR"
            
        return "US"
