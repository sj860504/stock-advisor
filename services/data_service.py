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
        """KOSPI + KOSDAQ ?쒓?珥앹븸 ?곸쐞 醫낅ぉ??諛섑솚?⑸땲??"""
        try:
            df = fdr.StockListing("KRX")
            # ?쒓?珥앹븸 ???뺣젹 諛??곸쐞 N媛?異붿텧
            top_df = df.sort_values(by="Marcap", ascending=False).head(limit)
            return top_df["Code"].tolist()
        except Exception as e:
            print(f"Error fetching top KRX tickers: {e}")
            return []

    @classmethod
    def get_top_tickers_cached(cls, limit: int = 100, force_refresh: bool = False) -> dict:
        """罹먯떆???곸쐞 醫낅ぉ 由ъ뒪?몃? 諛섑솚?섍굅??媛깆떊?⑸땲??"""
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
                    
                    # ?ㅻ뒛 ?대? ?섏쭛???곗씠?곌? ?덈떎硫?罹먯떆 ?ъ슜
                    if updated_date == today_date:
                        cache_valid = True
                        return cache_data.get("tickers", {})
            except Exception as e:
                print(f"Error reading ticker cache: {e}")

        # 罹먯떆媛 ?녾굅??留뚮즺??寃쎌슦 ?덈줈 ?섏쭛
        print("?븩 Refreshing Top Tickers Data from External APIs...")
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        
        result = {
            "kr": kr_tickers,
            "us": us_tickers
        }
        
        # 罹먯떆 ???
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
        """?덇굅???명솚?? ?곸쐞 醫낅ぉ 由ъ뒪??諛섑솚"""
        return cls.get_top_krx_tickers(limit=limit)

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """誘멸뎅 ?쒖옣(S&P 500 湲곕컲) ?쒓?珥앹븸 ?곸쐞 醫낅ぉ??諛섑솚?⑸땲??"""
        try:
            # S&P 500 ?꾩껜 由ъ뒪??媛?몄삤湲?
            candidates = cls.get_sp500_tickers()
            
            import yfinance as yf
            tickers_data = []
            
            # 50媛쒖뵫 ?섎닠??媛?몄삤湲?(API 遺??諛⑹?)
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
            
            # ?쒖킑 ???뺣젹
            tickers_data.sort(key=lambda x: x[1], reverse=True)
            return [t[0] for t in tickers_data[:limit]]
            
        except Exception as e:
            print(f"Error fetching top US tickers: {e}")
            # ?ㅽ뙣 ??湲곕낯 ?곕웾二?由ъ뒪??諛섑솚
            return ['AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'AVGO', 'LLY', 'WMT'][:limit]

    @classmethod
    def get_sp500_tickers(cls) -> list:
        """S&P 500 醫낅ぉ ?곗빱 由ъ뒪?몃? 媛?몄샃?덈떎 (GitHub CSV ?뚯뒪 ?ъ슜)."""
        try:
            # GitHub???좊ː?????덈뒗 S&P 500 由ъ뒪???ъ슜
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
            # Fallback (二쇱슂 ?곕웾二?由ъ뒪??100媛쒕줈 ?뺤옣)
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
        """怨쇨굅 N?쇨컙??媛寃??곗씠?곕? 媛?몄샃?덈떎."""
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
