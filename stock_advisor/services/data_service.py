import FinanceDataReader as fdr
import pandas as pd
import numpy as np

class DataService:
    @classmethod
    def get_top_market_cap_tickers(cls, limit: int = 20) -> list:
        """
        시가총액 상위 종목을 반환합니다. (기본값: S&P 500 상위)
        실시간 크롤링이 부담스러우므로, 주요 대형주 풀에서 시총을 비교하여 갱신합니다.
        """
        # 후보군 (주요 대형주 + 관심 종목)
        candidates = [
            'AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'AVGO', 'LLY',
            'JPM', 'XOM', 'V', 'UNH', 'MA', 'PG', 'COST', 'JNJ', 'HD', 'WMT',
            'NFLX', 'AMD', 'CRM', 'ORCL', 'ADBE', 'LIN', 'CVX', 'MRK', 'KO', 'PEP'
        ]
        
        try:
            import yfinance as yf
            tickers_data = []
            
            # 배치로 가져오는 것이 빠름 (yfinance 멀티스레딩 지원)
            tickers_str = " ".join(candidates)
            tickers = yf.Tickers(tickers_str)
            
            for symbol in candidates:
                try:
                    info = tickers.tickers[symbol].info
                    cap = info.get('marketCap', 0)
                    tickers_data.append((symbol, cap))
                except:
                    continue
            
            # 시총 순 정렬
            tickers_data.sort(key=lambda x: x[1], reverse=True)
            return [t[0] for t in tickers_data[:limit]]
            
        except Exception as e:
            print(f"Error fetching top tickers: {e}")
            # 실패 시 안전하게 기본 리스트 반환
            return candidates[:limit]

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
