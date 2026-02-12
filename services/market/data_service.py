import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime
from config import Config
from utils.logger import get_logger
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher
from services.market.stock_meta_service import StockMetaService
from services.analysis.indicator_service import IndicatorService
from services.analysis.financial_service import FinancialService

logger = get_logger("data_service")

class DataService:
    """
    KIS API 기반 데이터 수집 및 지표 계산 서비스
    - FinanceDataReader 의존성을 완전히 제거했습니다.
    """

    @classmethod
    def get_top_krx_tickers(cls, limit: int = 100) -> list:
        """KIS API를 통해 국내 주식 시가총액 상위 종목을 수집합니다."""
        try:
            token = KisService.get_access_token()
            res = KisFetcher.fetch_domestic_ranking(token)
            
            tickers = []
            if res.get('output'):
                for item in res['output'][:limit]:
                    ticker = item.get('mksc_shrn_iscd')
                    name = item.get('hts_kor_isnm')
                    if ticker:
                        tickers.append(ticker)
                        StockMetaService.upsert_stock_meta(
                            ticker=ticker,
                            name_ko=name,
                            market_type="KR",
                            exchange_code="KRX",
                            api_path="/uapi/domestic-stock/v1/quotations/inquire-price",
                            api_tr_id="FHKST01010100",
                            api_market_code="J"
                        )
            if not tickers:
                tickers = ["005930", "000660", "373220", "207940", "005380", "005490", "035420", "000270", "051910", "105560"]
                logger.info(f"⚠️ KRX ranking empty. Using fallback list: {len(tickers)} tickers.")
            return tickers
        except Exception as e:
            logger.error(f"Error fetching top KRX tickers via KIS: {e}")
            return ["005930", "000660", "373220", "207940", "005380"]

    @classmethod
    def get_top_us_tickers(cls, limit: int = 100) -> list:
        """KIS API를 통해 미국 주식 시가총액 상위 종목을 수집합니다."""
        try:
            token = KisService.get_access_token()
            # 나스닥(NAS)과 뉴욕(NYS) 합산하여 수집 시도
            res_nas = KisFetcher.fetch_overseas_ranking(token, excd="NAS")
            res_nys = KisFetcher.fetch_overseas_ranking(token, excd="NYS")
            
            combined = []
            for res, excd in [(res_nas, "NAS"), (res_nys, "NYS")]:
                if res.get('output'):
                    for item in res['output']:
                        combined.append({
                            "ticker": item.get('symb'),
                            "name": item.get('hname'),
                            "excd": excd,
                            "mcap": float(item.get('mcap', 0))
                        })
            
            # 시총 순 정렬
            combined.sort(key=lambda x: x['mcap'], reverse=True)
            
            tickers = []
            for item in combined[:limit]:
                ticker = item['ticker']
                if ticker:
                    tickers.append(ticker)
                    StockMetaService.upsert_stock_meta(
                        ticker=ticker,
                        name_ko=item['name'],
                        market_type="US",
                        exchange_code="NASD" if item['excd'] == "NAS" else "NYSE",
                        api_path="/uapi/overseas-stock/v1/quotations/price-detail",
                        api_tr_id="HHDFS70200200",
                        api_market_code=item['excd']
                    )
            if not tickers:
                tickers = ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "COST", "NFLX"]
                logger.info(f"⚠️ US ranking empty. Using fallback list: {len(tickers)} tickers.")
            return tickers
        except Exception as e:
            logger.error(f"Error fetching top US tickers via KIS: {e}")
            return ["AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"]

    @staticmethod
    def get_price_history(ticker: str, days: int = 300) -> pd.DataFrame:
        """KIS API를 통해 과거 N일간의 가격 데이터를 가져옵니다."""
        # 기존에 fetch_daily_price, fetch_overseas_daily_price를 이미 구현/정리했음을 가정
        from services.kis.fetch.kis_fetcher import KisFetcher
        try:
            token = KisService.get_access_token()
            from datetime import timedelta
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            
            if ticker.isdigit(): # 국내
                res = KisFetcher.fetch_daily_price(token, ticker, start_date, end_date)
                if not res or not res.get('output2'): return pd.DataFrame()
                df = pd.DataFrame(res['output2'])
                df = df.rename(columns={'stck_clpr': 'Close', 'stck_hgpr': 'High', 'stck_lwpr': 'Low', 'stck_oprc': 'Open', 'stck_bsop_date': 'Date'})
            elif ticker in ["SPX", "NAS", "VIX", "DJI"]: # 해외 지수
                # 지수 전용 TR 사용 (FHKST03030100)
                tr_id = "FHKST03030100"
                path = "/uapi/overseas-stock/v1/quotations/inquire-daily-chartprice"
                params = {
                    "fid_cond_mrkt_div_code": "U",
                    "fid_input_iscd": ticker,
                    "fid_input_date_1": start_date,
                    "fid_input_date_2": end_date,
                    "fid_period_div_code": "D"
                }
                headers = KisService.get_headers(tr_id)
                res = requests.get(f"{Config.KIS_BASE_URL}{path}", headers=headers, params=params)
                data = res.json()
                if not data.get('output2'): return pd.DataFrame()
                df = pd.DataFrame(data['output2'])
                df = df.rename(columns={'stck_clpr': 'Close', 'stck_hgpr': 'High', 'stck_lwpr': 'Low', 'stck_oprc': 'Open', 'stck_bsop_date': 'Date'})
            else: # 해외 일반 종목
                res = KisFetcher.fetch_overseas_daily_price(token, ticker, start_date, end_date)
                if not res.get('output'): return pd.DataFrame()
                df = pd.DataFrame(res['output'])
                df = df.rename(columns={'last': 'Close', 'high': 'High', 'low': 'Low', 'open': 'Open', 'xymd': 'Date'})

            df['Date'] = pd.to_datetime(df['Date'])
            df.set_index('Date', inplace=True)
            for col in ['Close', 'High', 'Low', 'Open']:
                df[col] = pd.to_datetime(df[col], errors='coerce') if col == 'Date' else pd.to_numeric(df[col], errors='coerce')
            
            return df.sort_index()
        except Exception as e:
            logger.error(f"Error fetching history for {ticker} via KIS: {e}")
            return pd.DataFrame()

    @classmethod
    def sync_daily_market_data(cls, limit: int = 100):
        """매일 1회 실행: 상위 종목 수집 -> 지표 계산 -> DB 저장"""
        logger.info(f"� Starting daily market data sync (Top {limit})...")
        
        # 1. 티커 수집
        kr_tickers = cls.get_top_krx_tickers(limit=limit)
        us_tickers = cls.get_top_us_tickers(limit=limit)
        
        all_tickers = [(t, "KR") for t in kr_tickers] + [(t, "US") for t in us_tickers]
        
        for ticker, market in all_tickers:
            try:
                logger.info(f"🔄 Processing {ticker} ({market})...")
                
                # A. 시세 및 기본 지표 (PER, PBR, Cap 등)
                token = KisService.get_access_token()
                if market == "KR":
                    basic_info = KisFetcher.fetch_domestic_price(token, ticker)
                else:
                    basic_info = KisFetcher.fetch_overseas_price(token, ticker)
                
                if not basic_info: continue

                # B. 기술적 지표 (RSI, EMA)
                hist = cls.get_price_history(ticker, days=365)
                indicators = {}
                if not hist.empty:
                    indicators = IndicatorService.get_latest_indicators(hist['Close'])
                
                # C. DCF 가치
                dcf_data = FinancialService.get_dcf_data(ticker)
                
                # D. 통합 데이터 구성
                metrics = {
                    "current_price": basic_info.get("price"),
                    "market_cap": basic_info.get("market_cap"),
                    "per": basic_info.get("per"),
                    "pbr": basic_info.get("pbr"),
                    "eps": basic_info.get("eps"),
                    "bps": basic_info.get("bps"),
                    "rsi": indicators.get("rsi"),
                    "ema": indicators.get("ema"), # dict: {5: val, 10: val, ...}
                    "dcf_value": None # 구현 필요 시 dcf_data 활용 가공
                }
                
                # DB 저장
                StockMetaService.save_financials(ticker, metrics)
                
                # KIS API 속도 제한 준수 (VTS 초당 2건)
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error syncing {ticker}: {e}")
                continue
        
        logger.info("✅ Daily market data sync completed.")
