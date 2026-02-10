import FinanceDataReader as fdr
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import time
import os
import json

class FinancialService:
    _krx_listing = None
    _dcf_cache = {}  # yfinance 데이터 캐싱
    _metrics_cache = {}  # 재무지표 캐싱
    _overrides_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'dcf_settings.json')

    @classmethod
    def get_overrides(cls) -> dict:
        """수동으로 설정된 DCF 파라미터를 가져옵니다."""
        if os.path.exists(cls._overrides_path):
            try:
                with open(cls._overrides_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @classmethod
    def save_override(cls, ticker: str, params: dict):
        """특정 종목의 DCF 파라미터를 저장합니다."""
        overrides = cls.get_overrides()
        overrides[ticker] = params
        
        # 폴더 생성 확인
        os.makedirs(os.path.dirname(cls._overrides_path), exist_ok=True)
        with open(cls._overrides_path, 'w', encoding='utf-8') as f:
            json.dump(overrides, f, indent=2)
        return overrides[ticker]

    @classmethod
    def _load_krx(cls):
        if cls._krx_listing is None:
            try:
                cls._krx_listing = fdr.StockListing('KRX')
            except Exception as e:
                print(f"KRX Listing Load Error: {e}")
                cls._krx_listing = pd.DataFrame()

    @classmethod
    def get_metrics(cls, ticker: str) -> dict:
        """
        yfinance를 사용해 재무 지표를 가져옵니다.
        """
        # 캐시 확인 (10분 유효)
        cache_key = ticker
        if cache_key in cls._metrics_cache:
            cached = cls._metrics_cache[cache_key]
            if time.time() - cached.get('_timestamp', 0) < 600:
                return cached
        
        metrics = {
            "market_cap": None,
            "per": None,
            "pbr": None,
            "roe": None,
            "dividend_yield": None,
            "eps": None,
            "revenue_growth": None,
            "_timestamp": time.time()
        }

        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 시가총액
            market_cap = info.get('marketCap')
            if market_cap:
                if market_cap >= 1_000_000_000_000:
                    metrics['market_cap'] = f"${market_cap / 1_000_000_000_000:.2f}T"
                elif market_cap >= 1_000_000_000:
                    metrics['market_cap'] = f"${market_cap / 1_000_000_000:.2f}B"
                else:
                    metrics['market_cap'] = f"${market_cap / 1_000_000:.2f}M"
            
            # PER (Trailing P/E)
            metrics['per'] = info.get('trailingPE') or info.get('forwardPE')
            if metrics['per']:
                metrics['per'] = round(metrics['per'], 2)
            
            # PBR (Price to Book)
            metrics['pbr'] = info.get('priceToBook')
            if metrics['pbr']:
                metrics['pbr'] = round(metrics['pbr'], 2)
            
            # ROE
            roe = info.get('returnOnEquity')
            if roe:
                metrics['roe'] = round(roe * 100, 2)
            
            # 배당수익률
            div_yield = info.get('dividendYield')
            if div_yield:
                metrics['dividend_yield'] = round(div_yield * 100, 2)
            
            # EPS
            metrics['eps'] = info.get('trailingEps')
            
            # 매출 성장률
            rev_growth = info.get('revenueGrowth')
            if rev_growth:
                metrics['revenue_growth'] = round(rev_growth * 100, 2)
                
        except Exception as e:
            print(f"yfinance metrics error for {ticker}: {e}")
        
        cls._metrics_cache[cache_key] = metrics
        return metrics

    @classmethod
    def get_dcf_data(cls, yahoo_ticker: str) -> dict:
        """
        yfinance를 사용해 실제 FCF 데이터를 가져옵니다.
        """
        ticker = yahoo_ticker.replace('.KS', '').replace('.KQ', '')
        
        # 캐시 확인 (30분 유효)
        cache_key = ticker
        if cache_key in cls._dcf_cache:
            cached = cls._dcf_cache[cache_key]
            if time.time() - cached.get('timestamp', 0) < 1800:
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
            
            cf = stock.cashflow
            if cf is not None and not cf.empty:
                fcf = None
                if 'Free Cash Flow' in cf.index:
                    fcf = cf.loc['Free Cash Flow'].iloc[0]
                elif 'Operating Cash Flow' in cf.index and 'Capital Expenditure' in cf.index:
                    ocf = cf.loc['Operating Cash Flow'].iloc[0]
                    capex = cf.loc['Capital Expenditure'].iloc[0]
                    fcf = ocf + capex
                    
                if fcf and fcf > 0:
                    info = stock.info
                    shares = info.get('sharesOutstanding', info.get('impliedSharesOutstanding'))
                    if shares:
                        result['fcf_per_share'] = fcf / shares
                        result['shares'] = shares
                    
                    result['beta'] = info.get('beta', 1.0)
                    
                    if len(cf.columns) >= 3:
                        try:
                            fcf_values = cf.loc['Free Cash Flow'].iloc[:3].values
                            if all(v > 0 for v in fcf_values):
                                growth = (fcf_values[0] / fcf_values[2]) ** 0.5 - 1
                                result['growth_rate'] = min(max(growth, 0.03), 0.30)
                        except:
                            pass
                    
                    analyst_growth = info.get('earningsGrowth') or info.get('revenueGrowth')
                    if analyst_growth and analyst_growth > 0:
                        result['growth_rate'] = min(analyst_growth, 0.30)
                        
        except Exception as e:
            print(f"yfinance error for {ticker}: {e}")
        
        cls._dcf_cache[cache_key] = result
        return result

    @staticmethod
    def validate_dcf(dcf_price: float, current_price: float) -> tuple:
        """
        DCF 값의 신뢰도를 검증합니다.
        Returns: (validated_price, confidence, note)
        """
        if dcf_price is None or current_price is None or current_price <= 0:
            return None, "N/A", "데이터 없음"
        
        ratio = current_price / dcf_price
        
        if ratio > 20:
            return dcf_price, "LOW", f"현재가가 DCF 대비 {ratio:.0f}배 높음 (성장 기대 반영)"
        elif ratio > 5:
            return dcf_price, "MEDIUM", f"현재가가 DCF 대비 {ratio:.1f}배 높음"
        elif ratio > 2:
            return dcf_price, "HIGH", "합리적 프리미엄"
        elif ratio > 0.5:
            return dcf_price, "HIGH", "적정 범위"
        else:
            return dcf_price, "MEDIUM", "저평가 가능성"

    @staticmethod
    def _crawl_naver_finance(ticker: str):
        url = f"https://finance.naver.com/item/main.naver?code={ticker}"
        result = {}
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, 'lxml')
            
            per_em = soup.select_one('#_per')
            pbr_em = soup.select_one('#_pbr')
            dvr_em = soup.select_one('#_dvr')
            
            if per_em: result['per'] = float(per_em.text.replace(',', ''))
            if pbr_em: result['pbr'] = float(pbr_em.text.replace(',', ''))
            if dvr_em: result['dividend_yield'] = float(dvr_em.text.replace('%', ''))
            
            cap_em = soup.select_one('#_market_sum')
            if cap_em: result['market_cap'] = cap_em.text.strip().replace('\t', '').replace('\n', '') + " 억원"

        except Exception as e:
            print(f"Naver crawling error: {e}")
        
        return result
