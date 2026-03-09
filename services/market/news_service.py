from typing import List
import time
from utils.logger import get_logger

logger = get_logger("news_service")

class NewsService:
    """Stock news collection and summarization service (yfinance removed)."""
    
    @classmethod
    def get_latest_news(cls, ticker: str, limit: int = 3) -> List[dict]:
        """Fetch latest news for a specific ticker.
        (Preparing to replace with KIS news API or RSS)
        """
        try:
            # TODO: KIS news API integration (TR ID TBD)
            # Currently returns empty list after yfinance removal
            logger.info(f"News fetch requested for {ticker} (Placeholder)")
            return []
        except Exception as e:
            logger.error(f"News fetch error for {ticker}: {e}")
            return []

    @classmethod
    def summarize_news(cls, ticker: str, news_list: List[dict]) -> str:
        """Summarize news list visually."""
        if not news_list:
            return f"No latest news available for {ticker}."
            
        summary = f"📰 **{ticker} Latest News Summary**\n"
        for idx, item in enumerate(news_list, 1):
            summary += f"{idx}. {item.get('title', '')} ({item.get('publisher', '')})\n"
            summary += f"   🔗 {item.get('link', '')}\n"
        
        return summary

    @classmethod
    def get_market_summary(cls) -> dict:
        """Get major index overview (recommend using MacroService)."""
        from services.market.macro_service import MacroService
        macro_data = MacroService.get_major_indices()
        return macro_data
