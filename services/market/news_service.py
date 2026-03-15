from typing import List
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("news_service")

# KIS 뉴스 API TR IDs (국내주식 공시/뉴스)
# FHKUP03500100 - 국내 종목 뉴스 (미확인, 테스트 필요)
KIS_DOMESTIC_NEWS_TR_ID = "FHKUP03500100"
KIS_DOMESTIC_NEWS_PATH = "/uapi/domestic-stock/v1/quotations/news-title"


class NewsService:
    """Stock news collection and summarization service."""

    @classmethod
    def get_latest_news(cls, ticker: str, limit: int = 3) -> List[dict]:
        """Fetch latest news for a specific ticker via KIS API."""
        try:
            if is_kr(ticker):
                return cls._fetch_kr_news(ticker, limit)
            # 해외주식 뉴스 KIS API 미지원 — 빈 리스트 반환
            return []
        except Exception as e:
            logger.error(f"News fetch error for {ticker}: {e}")
            return []

    @classmethod
    def _fetch_kr_news(cls, ticker: str, limit: int) -> List[dict]:
        """Fetch domestic stock news from KIS API."""
        try:
            from services.kis.kis_service import KisService
            token = KisService.get_access_token()
            headers = KisService.get_headers(KIS_DOMESTIC_NEWS_TR_ID)
            import requests
            from config import Config
            url = f"{Config.KIS_BASE_URL}{KIS_DOMESTIC_NEWS_PATH}"
            params = {"FID_INPUT_ISCD": ticker, "FID_TITL_LENG": "0"}
            response = requests.get(url, headers=headers, params=params, timeout=5)
            if response.status_code != 200:
                logger.warning(f"⚠️ KIS news API HTTP {response.status_code} for {ticker}")
                return []
            data = response.json()
            if data.get("rt_cd") != "0":
                logger.warning(f"⚠️ KIS news API error for {ticker}: {data.get('msg1')}")
                return []
            items = data.get("output", []) or []
            return [
                {
                    "title": item.get("news_ttl", ""),
                    "publisher": item.get("datas", ""),
                    "date": item.get("news_ofer_entp_dvsn", ""),
                }
                for item in items[:limit]
            ]
        except Exception as e:
            logger.error(f"❌ KIS news fetch failed for {ticker}: {e}")
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
