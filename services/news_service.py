import yfinance as yf
from typing import List
import time

class NewsService:
    """
    주식 관련 최신 뉴스 수집 및 요약 서비스
    """
    
    @classmethod
    def get_latest_news(cls, ticker: str, limit: int = 3) -> List[dict]:
        """특정 종목의 최신 뉴스를 가져옵니다."""
        try:
            stock = yf.Ticker(ticker)
            news_items = stock.news
            
            results = []
            for item in news_items[:limit]:
                content = item.get('content', {})
                provider = content.get('provider', {})
                url_info = content.get('canonicalUrl', {})
                
                results.append({
                    "title": content.get("title"),
                    "link": url_info.get("url"),
                    "publisher": provider.get("displayName"),
                    "pubDate": content.get("pubDate")
                })
            return results
        except Exception as e:
            print(f"News fetch error for {ticker}: {e}")
            return []

    @classmethod
    def summarize_news(cls, ticker: str, news_list: List[dict]) -> str:
        """
        뉴스 목록을 한글로 요약합니다. 
        """
        if not news_list:
            return f"{ticker}에 대한 최신 뉴스가 없습니다."
            
        summary = f"📰 **{ticker} 최신 뉴스 요약**\n"
        for i, news in enumerate(news_list, 1):
            summary += f"{i}. {news['title']} ({news['publisher']})\n"
            summary += f"   🔗 {news['link']}\n"
        
        return summary
