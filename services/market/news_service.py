from typing import List
import time
from utils.logger import get_logger

logger = get_logger("news_service")

class NewsService:
    """
    주식 관련 뉴스 수집 및 요약 서비스 (yfinance 제거 버전)
    """
    
    @classmethod
    def get_latest_news(cls, ticker: str, limit: int = 3) -> List[dict]:
        """
        특정 종목의 최신 뉴스를 가져옵니다. 
        (KIS 뉴스 API 또는 RSS 등으로 대체 준비 중)
        """
        try:
            # TODO: KIS 뉴스 API 연동 (TR ID 확인 필요)
            # 현재는 yfinance를 제거하기 위해 빈 리스트 또는 샘플 데이터 반환
            logger.info(f"News fetch requested for {ticker} (Placeholder)")
            return []
        except Exception as e:
            logger.error(f"News fetch error for {ticker}: {e}")
            return []

    @classmethod
    def summarize_news(cls, ticker: str, news_list: List[dict]) -> str:
        """
        뉴스 목록을 시각적으로 요약합니다.
        """
        if not news_list:
            return f"{ticker}에 대한 최신 뉴스가 없습니다."
            
        summary = f"📰 **{ticker} 최신 뉴스 요약**\n"
        for idx, item in enumerate(news_list, 1):
            summary += f"{idx}. {item.get('title', '')} ({item.get('publisher', '')})\n"
            summary += f"   🔗 {item.get('link', '')}\n"
        
        return summary

    @classmethod
    def get_market_summary(cls) -> dict:
        """
        주요 지수 현황을 조회합니다 (MacroService 활용 권장).
        """
        from services.market.macro_service import MacroService
        macro_data = MacroService.get_major_indices()
        return macro_data
