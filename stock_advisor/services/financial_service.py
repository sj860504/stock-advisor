import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

class FinancialService:
    _krx_listing = None
    _dcf_cache = {}  # yfinance 데이터 캐싱 (느리므로)


    @classmethod
    def _load_krx(cls):
        # KRX 데이터는 한 번 로드해서 캐싱 (PER, PBR 등이 포함됨)
        if cls._krx_listing is None:
            try:
                cls._krx_listing = fdr.StockListing('KRX')
                # 컬럼명 매핑/전처리 필요시 수행
            except Exception as e:
                print(f"KRX Listing Load Error: {e}")
                cls._krx_listing = pd.DataFrame()

    @classmethod
    def get_metrics(cls, ticker: str) -> dict:
        """
        종목의 재무 지표(PER, PBR, 배당수익률 등)를 가져옵니다.
        """
        metrics = {
            "market_cap": None,
            "per": None,
            "pbr": None,
            "roe": None,
            "dividend_yield": None
        }

        # 1. 한국 주식인 경우 (숫자 6자리)
        if ticker.isdigit() and len(ticker) == 6:
            cls._load_krx()
            if not cls._krx_listing.empty:
                row = cls._krx_listing[cls._krx_listing['Code'] == ticker]
                if not row.empty:
                    data = row.iloc[0]
                    # FinanceDataReader KRX 컬럼: Name, Code, Market, Sector, Close, ChangeCode, Changes, ChagesRatio, Open, High, Low, Volume, Amount, Marcap, Stocks, MarketId
                    # 참고: 최신 버전 fdr은 PER/PBR 컬럼이 없을 수 있음. (확인 필요)
                    # 만약 없다면 네이버 금융 크롤링으로 fallback
                    if 'PER' in data:
                        metrics['per'] = float(data['PER']) if pd.notnull(data['PER']) else None
                    if 'PBR' in data:
                        metrics['pbr'] = float(data['PBR']) if pd.notnull(data['PBR']) else None
                    
                    # KRX Listing에 재무 정보가 부족하다면 네이버 금융 크롤링
                    if metrics['per'] is None:
                        metrics.update(cls._crawl_naver_finance(ticker))
                    
                    return metrics

        # 2. 미국 주식인 경우 (알파벳)
        else:
            return cls._crawl_yahoo_finance(ticker)
        
        return metrics

    # 주요 미국 주식 FCF/Shares 데이터 (2025년 1월 기준)
    # fcf_per_share: 주당 잉여현금흐름 (USD)
    _us_fundamentals = {
        'AAPL': {'fcf_per_share': 7.2, 'beta': 1.28, 'growth_rate': 0.08},
        'NVDA': {'fcf_per_share': 1.1, 'beta': 1.70, 'growth_rate': 0.35},
        'MSFT': {'fcf_per_share': 9.5, 'beta': 0.90, 'growth_rate': 0.14},
        'AMZN': {'fcf_per_share': 3.3, 'beta': 1.15, 'growth_rate': 0.20},
        'GOOGL': {'fcf_per_share': 5.6, 'beta': 1.05, 'growth_rate': 0.12},
        'META': {'fcf_per_share': 16.8, 'beta': 1.20, 'growth_rate': 0.15},
        'TSLA': {'fcf_per_share': 2.4, 'beta': 2.30, 'growth_rate': 0.25},
        'BRK-B': {'fcf_per_share': 24.0, 'beta': 0.85, 'growth_rate': 0.08},
        'AVGO': {'fcf_per_share': 3.9, 'beta': 1.10, 'growth_rate': 0.15},
        'LLY': {'fcf_per_share': 8.9, 'beta': 0.40, 'growth_rate': 0.22},
        'JPM': {'fcf_per_share': 10.5, 'beta': 1.10, 'growth_rate': 0.08},
        'XOM': {'fcf_per_share': 9.0, 'beta': 0.95, 'growth_rate': 0.05},
        'V': {'fcf_per_share': 9.3, 'beta': 0.95, 'growth_rate': 0.12},
        'UNH': {'fcf_per_share': 25.0, 'beta': 0.55, 'growth_rate': 0.12},
        'MA': {'fcf_per_share': 12.9, 'beta': 1.05, 'growth_rate': 0.14},
        'PG': {'fcf_per_share': 7.6, 'beta': 0.40, 'growth_rate': 0.06},
        'COST': {'fcf_per_share': 15.8, 'beta': 0.75, 'growth_rate': 0.10},
        'JNJ': {'fcf_per_share': 8.3, 'beta': 0.55, 'growth_rate': 0.05},
        'HD': {'fcf_per_share': 17.2, 'beta': 1.00, 'growth_rate': 0.08},
        'WMT': {'fcf_per_share': 1.9, 'beta': 0.50, 'growth_rate': 0.06}
    }


    @classmethod
    def get_dcf_data(cls, yahoo_ticker: str) -> dict:
        """
        yfinance를 사용해 실제 FCF 데이터를 가져옵니다.
        """
        # 티커 정규화 (.KS, .KQ 제거 후 다시 추가 필요시)
        ticker = yahoo_ticker.replace('.KS', '').replace('.KQ', '')
        
        # 캐시 확인 (30분 유효)
        import time
        cache_key = ticker
        if cache_key in cls._dcf_cache:
            cached = cls._dcf_cache[cache_key]
            if time.time() - cached.get('timestamp', 0) < 1800:  # 30분
                return cached
        
        result = {
            "fcf_per_share": None,
            "beta": None,
            "growth_rate": 0.10,
            "shares": None,
            "timestamp": time.time()
        }
        
        try:
            stock = yf.Ticker(ticker)
            
            # 1. 현금흐름표에서 FCF 가져오기
            cf = stock.cashflow
            if cf is not None and not cf.empty:
                # Free Cash Flow 또는 Operating Cash Flow - CapEx
                if 'Free Cash Flow' in cf.index:
                    fcf = cf.loc['Free Cash Flow'].iloc[0]
                elif 'Operating Cash Flow' in cf.index and 'Capital Expenditure' in cf.index:
                    ocf = cf.loc['Operating Cash Flow'].iloc[0]
                    capex = cf.loc['Capital Expenditure'].iloc[0]
                    fcf = ocf + capex  # capex는 음수이므로 +
                else:
                    fcf = None
                    
                if fcf and fcf > 0:
                    # 발행주식수
                    info = stock.info
                    shares = info.get('sharesOutstanding', info.get('impliedSharesOutstanding'))
                    if shares:
                        result['fcf_per_share'] = fcf / shares
                        result['shares'] = shares
                    
                    # Beta
                    result['beta'] = info.get('beta', 1.0)
                    
                    # 성장률 추정 (과거 3년 FCF 성장률 또는 애널리스트 추정)
                    if len(cf.columns) >= 3:
                        try:
                            fcf_values = cf.loc['Free Cash Flow'].iloc[:3].values
                            if all(v > 0 for v in fcf_values):
                                growth = (fcf_values[0] / fcf_values[2]) ** 0.5 - 1
                                result['growth_rate'] = min(max(growth, 0.03), 0.30)  # 3%~30% 제한
                        except:
                            pass
                    
                    # 애널리스트 성장률 추정치가 있으면 사용
                    analyst_growth = info.get('earningsGrowth') or info.get('revenueGrowth')
                    if analyst_growth and analyst_growth > 0:
                        result['growth_rate'] = min(analyst_growth, 0.30)
                        
        except Exception as e:
            print(f"yfinance error for {ticker}: {e}")
        
        cls._dcf_cache[cache_key] = result
        return result



    @classmethod
    def _crawl_dcf_data(cls, yahoo_ticker: str) -> dict:

        """
        DCF 계산에 필요한 데이터를 Yahoo Finance Key Statistics에서 가져옵니다.
        필요 데이터: Levered Free Cash Flow, Shares Outstanding, Beta
        """
        url = f"https://finance.yahoo.com/quote/{yahoo_ticker}/key-statistics"
        result = {
            "fcf": None,
            "shares": None,
            "beta": None,
            "growth_rate": 0.05 # 기본값 5%
        }
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, 'lxml')
            
            # 1. Levered Free Cash Flow (TTM)
            # 보통 "Levered Free Cash Flow" 라벨의 다음 td
            # data-reactid 구조가 복잡하므로 텍스트 기반 검색
            for tr in soup.find_all('tr'):
                cols = tr.find_all('td')
                if len(cols) >= 2:
                    label = cols[0].text.strip()
                    if "Levered Free Cash Flow" in label:
                        val_text = cols[1].text.strip()
                        result['fcf'] = cls._parse_yahoo_number(val_text)
                    elif "Shares Outstanding" in label:
                        val_text = cols[1].text.strip()
                        result['shares'] = cls._parse_yahoo_number(val_text)
                    elif "Beta (5Y Monthly)" in label:
                        val_text = cols[1].text.strip()
                        try:
                            result['beta'] = float(val_text)
                        except: pass
            
            # Growth Rate 추정 (Analysis 페이지)
            # https://finance.yahoo.com/quote/{ticker}/analysis
            # "Next 5 Years (per annum)" 찾기
            analysis_url = f"https://finance.yahoo.com/quote/{yahoo_ticker}/analysis"
            res_analysis = requests.get(analysis_url, headers=headers)
            soup_analysis = BeautifulSoup(res_analysis.text, 'lxml')
            
            for tr in soup_analysis.find_all('tr'):
                cols = tr.find_all('td')
                if len(cols) >= 2:
                    label = cols[0].text.strip()
                    if "Next 5 Years (per annum)" in label:
                        val_text = cols[1].text.strip().replace('%', '')
                        try:
                            result['growth_rate'] = float(val_text) / 100.0
                        except: pass
                        
        except Exception as e:
            print(f"Yahoo DCF data crawling error: {e}")
            
        return result

    @staticmethod
    def _parse_yahoo_number(text: str) -> float:
        # 1.23B, 456M, 1.5T 등을 숫자로 변환
        text = text.upper().replace(',', '')
        if 'T' in text:
            return float(text.replace('T', '')) * 1_000_000_000_000
        elif 'B' in text:
            return float(text.replace('B', '')) * 1_000_000_000
        elif 'M' in text:
            return float(text.replace('M', '')) * 1_000_000
        elif 'K' in text:
            return float(text.replace('K', '')) * 1_000
        try:
            return float(text)
        except:
            return None

    @staticmethod
    def _crawl_naver_finance(ticker: str):
        # 네이버 금융 종목분석 페이지
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        result = {}
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, 'lxml')
            
            # PER, PBR 찾기
            per_em = soup.select_one('#_per')
            pbr_em = soup.select_one('#_pbr')
            dvr_em = soup.select_one('#_dvr') # 배당수익률
            
            if per_em: result['per'] = float(per_em.text.replace(',', ''))
            if pbr_em: result['pbr'] = float(pbr_em.text.replace(',', ''))
            if dvr_em: result['dividend_yield'] = float(dvr_em.text.replace('%', ''))
            
            # 시가총액
            cap_em = soup.select_one('#_market_sum')
            if cap_em: result['market_cap'] = cap_em.text.strip().replace('\t', '').replace('\n', '') + " 억원"

            # 목표주가 (Analyst Consensus)
            try:
                # summary="투자의견 정보" 테이블 검색
                consensus_table = soup.find('table', summary='투자의견 정보')
                if consensus_table:
                    # em 태그 중 2번째가 보통 목표주가
                    nums = consensus_table.find_all('em')
                    if len(nums) >= 2:
                        tp_text = nums[1].text.strip().replace(',', '')
                        if tp_text.isdigit():
                            result['target_price'] = float(tp_text)
            except:
                pass

        except Exception as e:
            print(f"Naver crawling error: {e}")
        
        return result

    @staticmethod
    def _crawl_yahoo_finance(ticker: str):
        # 야후 파이낸스 요약 페이지
        url = f"https://finance.yahoo.com/quote/{ticker}"
        result = {}
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, 'lxml')
            
            # PE Ratio (TTM)
            pe_elm = soup.find('td', {'data-test': 'PE_RATIO-value'})
            if pe_elm: result['per'] = float(pe_elm.text.replace(',', ''))
            
            # Market Cap
            cap_elm = soup.find('td', {'data-test': 'MARKET_CAP-value'})
            if cap_elm: result['market_cap'] = cap_elm.text
            
            # 1y Target Est (Analyst Consensus)
            target_elm = soup.find('td', {'data-test': 'ONE_YEAR_TARGET_PRICE-value'})
            if target_elm:
                val = target_elm.text.replace(',', '')
                if val and val != 'N/A':
                    result['target_price'] = float(val)

        except Exception as e:
            print(f"Yahoo crawling error: {e}")
            
        return result
