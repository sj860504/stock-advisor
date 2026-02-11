import yfinance as yf
from typing import List
import time

class NewsService:
    """
    二쇱떇 愿??理쒖떊 ?댁뒪 ?섏쭛 諛??붿빟 ?쒕퉬??
    """
    
    @classmethod
    def get_latest_news(cls, ticker: str, limit: int = 3) -> List[dict]:
        """?뱀젙 醫낅ぉ??理쒖떊 ?댁뒪瑜?媛?몄샃?덈떎."""
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
        ?댁뒪 紐⑸줉???쒓?濡??붿빟?⑸땲?? 
        """
        if not news_list:
            return f"{ticker}?????理쒖떊 ?댁뒪媛 ?놁뒿?덈떎."
            
        summary = f"?벐 **{ticker} 理쒖떊 ?댁뒪 ?붿빟**\n"
        for i, news in enumerate(news_list, 1):
            summary += f"{i}. {news['title']} ({news['publisher']})\n"
            summary += f"   ?뵕 {news['link']}\n"
        
        return summary

    @classmethod
    def get_market_summary(cls) -> dict:
        """
        二쇱슂 吏???꾪솴??議고쉶?⑸땲??
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
