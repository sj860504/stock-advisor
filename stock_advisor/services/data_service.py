import FinanceDataReader as fdr
import pandas as pd
import numpy as np

class DataService:
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
