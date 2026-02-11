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
    _dcf_cache = {}  # yfinance ?곗씠??罹먯떛
    _metrics_cache = {}  # ?щТ吏??罹먯떛
    _overrides_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'dcf_settings.json')

    @classmethod
    def get_overrides(cls) -> dict:
        """?섎룞?쇰줈 ?ㅼ젙??DCF ?뚮씪誘명꽣瑜?媛?몄샃?덈떎."""
        if os.path.exists(cls._overrides_path):
            try:
                with open(cls._overrides_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    @classmethod
    def save_override(cls, ticker: str, params: dict):
        """?뱀젙 醫낅ぉ??DCF ?뚮씪誘명꽣瑜???ν빀?덈떎."""
        overrides = cls.get_overrides()
        overrides[ticker] = params
        
        # ?대뜑 ?앹꽦 ?뺤씤
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
        yfinance瑜??ъ슜???щТ 吏?쒕? 媛?몄샃?덈떎.
        """
        # 罹먯떆 ?뺤씤 (10遺??좏슚)
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
            
            # ?쒓?珥앹븸
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
            
            # 諛곕떦?섏씡瑜?
            div_yield = info.get('dividendYield')
            if div_yield:
                metrics['dividend_yield'] = round(div_yield * 100, 2)
            
            # EPS
            metrics['eps'] = info.get('trailingEps')
            
            # 留ㅼ텧 ?깆옣瑜?
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
        yfinance瑜??ъ슜???ㅼ젣 FCF ?곗씠?곕? 媛?몄샃?덈떎.
        """
        ticker = yahoo_ticker.replace('.KS', '').replace('.KQ', '')
        
        # 罹먯떆 ?뺤씤 (30遺??좏슚)
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
        DCF 媛믪쓽 ?좊ː?꾨? 寃利앺빀?덈떎.
        Returns: (validated_price, confidence, note)
        """
        if dcf_price is None or current_price is None or current_price <= 0:
            return None, "N/A", "?곗씠???놁쓬"
        
        ratio = current_price / dcf_price
        
        if ratio > 20:
            return dcf_price, "LOW", f"?꾩옱媛媛 DCF ?鍮?{ratio:.0f}諛??믪쓬 (?깆옣 湲곕? 諛섏쁺)"
        elif ratio > 5:
            return dcf_price, "MEDIUM", f"?꾩옱媛媛 DCF ?鍮?{ratio:.1f}諛??믪쓬"
        elif ratio > 2:
            return dcf_price, "HIGH", "?⑸━???꾨━誘몄뾼"
        elif ratio > 0.5:
            return dcf_price, "HIGH", "?곸젙 踰붿쐞"
        else:
            return dcf_price, "MEDIUM", "??됯? 媛?μ꽦"

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
            if cap_em: result['market_cap'] = cap_em.text.strip().replace('\t', '').replace('\n', '') + " ?듭썝"

        except Exception as e:
            print(f"Naver crawling error: {e}")
        
        return result
