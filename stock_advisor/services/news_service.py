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

    @classmethod
    def get_market_summary(cls) -> dict:
        """
        주요 지수 현황을 조회합니다.
        KOSPI, KOSDAQ, S&P 500, NASDAQ, USD/KRW, VIX
        """
        indices = {
            "KOSPI": "^KS11",
            "KOSDAQ": "^KQ11",
            "S&P 500": "^GSPC",
            "NASDAQ": "^IXIC",
            "USD/KRW": "KRW=X",
            "VIX": "^VIX"
        }
        
        result = {}
        for name, ticker_symbol in indices.items():
            try:
                ticker = yf.Ticker(ticker_symbol)
                info = ticker.fast_info
                price = info.last_price
                prev_close = info.previous_close
                
                if price and prev_close:
                    change = price - prev_close
                    pct_change = (change / prev_close) * 100
                    result[name] = {
                        "price": round(price, 2),
                        "change": round(change, 2),
                        "change_pct": round(pct_change, 2)
                    }
                else:
                    # Fallback to history
                    hist = ticker.history(period="2d")
                    if len(hist) >= 2:
                        close = hist['Close'].iloc[-1]
                        prev = hist['Close'].iloc[-2]
                        change = close - prev
                        pct_change = (change / prev) * 100
                        result[name] = {
                            "price": round(close, 2),
                            "change": round(change, 2),
                            "change_pct": round(pct_change, 2)
                        }
                    elif len(hist) == 1:
                        result[name] = {
                            "price": round(hist['Close'].iloc[-1], 2),
                            "change": None,
                            "change_pct": None
                        }
                    else:
                        result[name] = {"price": None, "change": None, "change_pct": None, "error": "No data"}
            except Exception as e:
                result[name] = {"price": None, "change": None, "change_pct": None, "error": str(e)}
        
        return result
