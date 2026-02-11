import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from utils.logger import get_logger
from services.stock_meta_service import StockMetaService

logger = get_logger("data_service")

class DataService:
    CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    CACHE_FILE = os.path.join(CACHE_DIR, "ticker_cache.json")
    
    @classmethod
    def get_top_krx_tickers(cls, limit: int = 100) -> list:
        """KOSPI + KOSDAQ 시가총액 상위 종목을 반환합니다."""
        try:
            df = fdr.StockListing("KRX")
            top_df = df.sort_values(by="Marcap", ascending=False).head(limit)
            
            # DB에 메타 정보 저장
            for _, row in top_df.iterrows():
                StockMetaService.upsert_stock_meta(
                    ticker=row["Code"],
                    name_ko=row["Name"],
                    market_type="KR",
                    exchange_code="KRX"
                )
                
            return top_df["Code"].tolist()
        except Exception as e:
            logger.error(f"Error fetching top KRX tickers: {e}")
            return []

    @classmethod
    def get_top_tickers_cached(cls, limit: int = 100, force_refresh: bool = False) -> dict:
        """캐시된 상위 종목 리스트를 반환하거나 갱신합니다."""
        if not os.path.exists(cls.CACHE_DIR):
            os.makedirs(cls.CACHE_DIR, exist_ok=True)
            
        now = datetime.now()
        if os.path.exists(cls.CACHE_FILE) and not force_refresh:
            try:
                with open(cls.CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    if cache_data.get("updated_at", "")[:10] == now.strftime("%Y-%m-%d"):
                        return cache_data.get("tickers", {})
            except: pass

        logger.info("📡 Refreshing Top Tickers Data...")
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        
        result = {"kr": kr_tickers, "us": us_tickers}
        try:
            with open(cls.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"updated_at": now.isoformat(), "tickers": result}, f, ensure_ascii=False, indent=4)
        except: pass
        return result

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """미국 시장 주요 종목 반환 (yfinance 제거)"""
        # 실시간 시가총액 순위는 KIS API 부하가 크므로, 주요 S&P 500 종목 리스트로 대체
        # (필요 시 KIS 해외지수/순위 API로 확장 가능)
        default_us = [
            'AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'BRK-B', 'LLY', 'AVGO', 'TSLA', 
            'JPM', 'UNH', 'MA', 'XOM', 'PG', 'COST', 'HD', 'JNJ', 'ASML', 'ORCL'
        ]
        
        # DB에 메타 정보 저장 (해외 주식)
        for ticker in default_us[:limit]:
            StockMetaService.upsert_stock_meta(
                ticker=ticker,
                market_type="US",
                exchange_code="NASD" # 단순화
            )
            
        return default_us[:limit]

    @staticmethod
    def get_price_history(ticker: str, days: int = 300):
        """과거 N일간의 가격 데이터를 가져옵니다."""
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            return fdr.DataReader(ticker, start_date)
        except Exception as e:
            logger.error(f"Error fetching history for {ticker}: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_stock_listing(market: str = "KRX"):
        try:
            return fdr.StockListing(market)
        except: return None
