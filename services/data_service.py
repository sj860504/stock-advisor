import FinanceDataReader as fdr
import pandas as pd
import numpy as np

class DataService:
    @classmethod
    def get_sp500_tickers(cls) -> list:
        """S&P 500 종목 티커 리스트를 가져옵니다."""
        try:
            # Wikipedia에서 S&P 500 리스트 크롤링 (가장 안정적)
            # URL: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
            table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
            df = table[0]
            tickers = df['Symbol'].tolist()
            # Yahoo Finance용 티커 수정 (예: BRK.B -> BRK-B)
            return [t.replace('.', '-') for t in tickers]
        except Exception as e:
            print(f"Error fetching S&P 500 tickers: {e}")
            # Fallback (주요 우량주 리스트)
            return ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'LLY', 'AVGO', 'V', 'TSLA', 'JPM']

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
