import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta

class DataService:
    CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    CACHE_FILE = os.path.join(CACHE_DIR, "ticker_cache.json")
    @classmethod
    def get_top_krx_tickers(cls, limit: int = 100) -> list:
        """KOSPI + KOSDAQ 시가총액 상위 종목을 반환합니다."""
        try:
            df = fdr.StockListing("KRX")
            # 시가총액 순 정렬 및 상위 N개 추출
            top_df = df.sort_values(by="Marcap", ascending=False).head(limit)
            return top_df["Code"].tolist()
        except Exception as e:
            print(f"Error fetching top KRX tickers: {e}")
            return []

    @classmethod
    def get_top_tickers_cached(cls, limit: int = 100, force_refresh: bool = False) -> dict:
        """캐시된 상위 종목 리스트를 반환하거나 갱신합니다."""
        if not os.path.exists(cls.CACHE_DIR):
            os.makedirs(cls.CACHE_DIR, exist_ok=True)
            
        now = datetime.now()
        cache_valid = False
        
        if os.path.exists(cls.CACHE_FILE) and not force_refresh:
            try:
                with open(cls.CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    updated_at_str = cache_data.get("updated_at", "2000-01-01")
                    updated_date = updated_at_str[:10] # YYYY-MM-DD
                    today_date = now.strftime("%Y-%m-%d")
                    
                    # 오늘 이미 수집된 데이터가 있다면 캐시 사용
                    if updated_date == today_date:
                        cache_valid = True
                        return cache_data.get("tickers", {})
            except Exception as e:
                print(f"Error reading ticker cache: {e}")

        # 캐시가 없거나 만료된 경우 새로 수집
        print("🕒 Refreshing Top Tickers Data from External APIs...")
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        
        result = {
            "kr": kr_tickers,
            "us": us_tickers
        }
        
        # 캐시 저장
        try:
            with open(cls.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "updated_at": now.isoformat(),
                    "tickers": result
                }, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving ticker cache: {e}")
            
        return result

    @classmethod
    def get_top_market_cap_tickers(cls, limit: int = 100) -> list:
        """레거시 호환용: 상위 종목 리스트 반환"""
        return cls.get_top_krx_tickers(limit=limit)

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """미국 시장(S&P 500 기반) 시가총액 상위 종목을 반환합니다."""
        try:
            # S&P 500 전체 리스트 가져오기
            candidates = cls.get_sp500_tickers()
            
            import yfinance as yf
            tickers_data = []
            
            # 50개씩 나눠서 가져오기 (API 부하 방지)
            chunk_size = 50
            for i in range(0, len(candidates), chunk_size):
                chunk = candidates[i:i + chunk_size]
                tickers_str = " ".join(chunk)
                tickers = yf.Tickers(tickers_str)
                
                for symbol in chunk:
                    try:
                        info = tickers.tickers[symbol].fast_info
                        cap = info.market_cap
                        tickers_data.append((symbol, cap))
                    except:
                        continue
            
            # 시총 순 정렬
            tickers_data.sort(key=lambda x: x[1], reverse=True)
            return [t[0] for t in tickers_data[:limit]]
            
        except Exception as e:
            print(f"Error fetching top US tickers: {e}")
            # 실패 시 기본 우량주 리스트 반환
            return ['AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'LLY', 'WMT'][:limit]

    @classmethod
    def get_sp500_tickers(cls) -> list:
        """S&P 500 종목 티커 리스트를 가져옵니다 (GitHub CSV 소스 사용)."""
        try:
            # GitHub의 신뢰할 수 있는 S&P 500 리스트 사용
            url = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv'
            import requests
            import io
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                df = pd.read_csv(io.StringIO(response.text))
                tickers = df['Symbol'].tolist()
                return [t.replace('.', '-') for t in tickers]
            else:
                raise Exception(f"GitHub returned status code {response.status_code}")
        except Exception as e:
            print(f"Error fetching S&P 500 tickers: {e}")
            # Fallback (주요 우량주 리스트 100개로 확장)
            return [
                'AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'BRK-B', 'LLY', 'AVGO', 'TSLA', 
                'JPM', 'UNH', 'MA', 'XOM', 'PG', 'COST', 'HD', 'JNJ', 'ASML', 'ORCL', 
                'ABBV', 'CRM', 'BAC', 'CVX', 'NFLX', 'ADBE', 'AMD', 'WMT', 'KO', 'PEP',
                'TMO', 'CSCO', 'ABT', 'COST', 'DIS', 'MCD', 'INTU', 'DHR', 'VZ', 'PFE',
                'AMGN', 'INTC', 'CMCSA', 'NEE', 'IBM', 'TXN', 'QCOM', 'CAT', 'GE', 'HON',
                'UNP', 'AXP', 'LOW', 'AMAT', 'LIN', 'PM', 'SYK', 'RTX', 'SBUX', 'ISRG',
                'DE', 'GILD', 'BKNG', 'EL', 'TJX', 'MDLZ', 'LMT', 'ADI', 'ADP', 'AMT',
                'REGN', 'MMC', 'VRTX', 'CB', 'MU', 'ZTS', 'PANW', 'BX', 'SNPS', 'CDNS',
                'CI', 'T', 'SCHW', 'FI', 'MO', 'CVS', 'EOG', 'ETN', 'ITW', 'MMC',
                'ORLY', 'PLD', 'C', 'BDX', 'APH', 'ICE', 'KLAC', 'MCK', 'SLB', 'MAR'
            ]

    @staticmethod
    def get_price_history(ticker: str, days: int = 300):
        """과거 N일간의 가격 데이터를 가져옵니다."""
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            df = fdr.DataReader(ticker, start_date)
            return df
        except Exception as e:
            print(f"Error fetching history for {ticker}: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_price_data(ticker: str, start_date: str = "2024-01-01"):
        try:
            df = fdr.DataReader(ticker, start_date)
            return df
        except Exception as e:
            print(f"Error fetching data for {ticker}: {e}")
            return None

    @staticmethod
    def get_current_price(ticker: str):
        df = fdr.DataReader(ticker) # Defaults to recent
        if df is None or df.empty:
            return None
        return float(df['Close'].iloc[-1])

    @staticmethod
    def get_stock_listing(market: str = "KRX"):
        try:
            return fdr.StockListing(market)
        except:
            return None
