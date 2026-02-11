import yfinance as yf
import pandas as pd
import time
from datetime import datetime, timedelta
from stock_advisor.config import Config

class MacroService:
    """
    거시경제 지표 및 시장 국면 분석 서비스 (확장판)
    """
    _cache = {}
    _cache_expiry = 3600  # 1시간 캐싱

    # FRED 시리즈 ID 매핑
    FRED_SERIES = {
        "avg_hourly_earnings": "CES0500000003",
        "cpi": "CPIAUCSL",
        "ppi": "PPIACO",
        "nonfarm_payrolls": "PAYEMS",
        "unemployment_rate": "UNRATE",
        "consumer_confidence": "UMCSENT",
        "pmi": "IPMAN",
        "retail_sales": "RSXFS",
        "industrial_production": "INDPRO",
        "capacity_utilization": "TCU",
        "housing_starts": "HOUST",
        "building_permits": "PERMIT",
        "durable_goods_orders": "DGORDER",
        "initial_jobless_claims": "ICSA"
    }

    # 지표 판단 기준: "higher_is_good": True 이면 실제 > 기대 일 때 호재
    MACRO_RULES = {
        "avg_hourly_earnings": {"higher_is_good": False, "name": "시간당 평균 임금"},
        "cpi": {"higher_is_good": False, "name": "소비자 물가 지수"},
        "ppi": {"higher_is_good": False, "name": "생산자 물가 지수"},
        "nonfarm_payrolls": {"higher_is_good": True, "name": "비농업 고용 지수"},
        "unemployment_rate": {"higher_is_good": False, "name": "실업률"},
        "consumer_confidence": {"higher_is_good": True, "name": "소비자 신뢰 지수"},
        "pmi": {"higher_is_good": True, "name": "구매 관리자 지수"},
        "retail_sales": {"higher_is_good": True, "name": "소매 판매"},
        "industrial_production": {"higher_is_good": True, "name": "산업 생산"},
        "capacity_utilization": {"higher_is_good": True, "name": "설비 가동률"},
        "housing_starts": {"higher_is_good": True, "name": "주택 착공"},
        "building_permits": {"higher_is_good": True, "name": "건축 허가"},
        "durable_goods_orders": {"higher_is_good": True, "name": "내구재 주문"},
        "initial_jobless_claims": {"higher_is_good": False, "name": "실업 수당 청구"}
    }

    @classmethod
    def get_macro_data(cls) -> dict:
        """
        주요 지수, 국채금리, 코인, 원자재를 종합적으로 반환합니다.
        """
        now = time.time()
        if 'macro' in cls._cache:
            data, timestamp = cls._cache['macro']
            if now - timestamp < cls._cache_expiry:
                return data

        print("🌐 Fetching Comprehensive Macro Data...")
        data = {
            "indices": cls._get_major_indices(),
            "us_10y_yield": cls._get_us_10y_yield(),
            "market_regime": cls._get_market_regime(),
            "vix": cls._get_vix(),
            "fear_greed": cls._get_fear_greed_index(),
            "sector_performance": cls._get_sector_performance(),
            "crypto": cls._get_crypto_data(),
            "commodities": cls._get_commodity_data(),
            "economic_indicators": cls._get_economic_indicators(),
            "timestamp": now
        }
        
        cls._cache['macro'] = (data, now)
        return data

    @classmethod
    def _get_major_indices(cls) -> dict:
        """주요 지수 시세 (S&P500, 다우, 나스닥100, 러셀2000, 코스피, 항생)"""
        indices = {}
        mapping = [
            ("^GSPC", "S&P500"),
            ("^DJI", "Dow"),
            ("^NDX", "Nasdaq100"),
            ("^RUT", "Russell2000"),
            ("^KS11", "KOSPI"),
            ("^HSI", "HangSeng")
        ]
        for ticker, name in mapping:
            try:
                t = yf.Ticker(ticker)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                change = ((price - prev) / prev) * 100
                indices[name] = {"price": price, "change": change}
            except:
                indices[name] = {"price": 0, "change": 0}
        return indices

    @classmethod
    def _get_economic_indicators(cls) -> dict:
        """FRED에서 경제 지표 가져오기 및 호재/악재 판단"""
        results = {}
        total_score = 0
        count = 0
        
        try:
            start = datetime.now() - timedelta(days=365)
            end = datetime.now()
            if not Config.FRED_API_KEY:
                print("⚠️ FRED_API_KEY is missing. Skipping Economic Indicators analysis.")
                return {"summary": {"total_score": 0, "max_score": 0, "sentiment_ratio": 0}}

            import requests
            for key, series_id in cls.FRED_SERIES.items():
                try:
                    # FRED API 직접 호출 (pandas_datareader 미설치 대비)
                    url = f"https://api.stlouisfed.org/fred/series/observations"
                    params = {
                        "series_id": series_id,
                        "api_key": Config.FRED_API_KEY,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 5
                    }
                    res = requests.get(url, params=params, timeout=10)
                    res.raise_for_status()
                    data = res.json()
                    
                    observations = data.get('observations', [])
                    if len(observations) < 2:
                        continue
                        
                    vals = [float(o['value']) for o in observations if o['value'] != '.']
                    if len(vals) < 2:
                        continue
                        
                    val = vals[0]
                    prev_val = vals[1]
                    
                    # 기대치(Forecast)가 없으므로 전회(Prev)를 기대치로 가정
                    forecast = prev_val 
                    
                    rule = cls.MACRO_RULES.get(key, {"higher_is_good": True, "name": key})
                    is_good = (val > forecast) if rule["higher_is_good"] else (val < forecast)
                    
                    # 중립 판단 (변동폭이 매우 작을 때)
                    if abs(val - forecast) < 0.0001:
                        sentiment = "Neutral"
                        score = 0
                    else:
                        sentiment = "Bullish" if is_good else "Bearish"
                        score = 1 if is_good else -1
                        
                    results[key] = {
                        "name": rule["name"],
                        "value": round(val, 2),
                        "forecast": round(forecast, 2),
                        "sentiment": sentiment,
                        "score": score
                    }
                    total_score += score
                    count += 1
                except Exception as e:
                    print(f"⚠️ Error fetching FRED series {series_id}: {e}")
                    results[key] = {"name": key, "value": 0, "forecast": 0, "sentiment": "Unknown", "score": 0}
                    
        except Exception as e:
            print(f"Error in _get_economic_indicators: {e}")
            
        results["summary"] = {
            "total_score": total_score,
            "max_score": count,
            "sentiment_ratio": round(total_score / count, 2) if count > 0 else 0
        }
        return results

    @classmethod
    def _get_crypto_data(cls) -> dict:
        """비트코인 시세"""
        crypto = {}
        for ticker, name in [("BTC-USD", "Bitcoin")]:
            try:
                t = yf.Ticker(ticker)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                change = ((price - prev) / prev) * 100
                crypto[name] = {"price": price, "change": change}
            except:
                crypto[name] = {"price": price if 'price' in locals() else 0, "change": 0}
        return crypto

    @classmethod
    def _get_commodity_data(cls) -> dict:
        """금, 은 선물 시세"""
        commodities = {}
        # 금(GC=F), 은(SI=F)
        mapping = [("GC=F", "Gold"), ("SI=F", "Silver")]
        for ticker, name in mapping:
            try:
                t = yf.Ticker(ticker)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                change = ((price - prev) / prev) * 100
                commodities[name] = {"price": price, "change": change}
            except:
                commodities[name] = {"price": 0, "change": 0}
        return commodities

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """미국 10년물 국채 금리 (^TNX)"""
        try:
            ticker = yf.Ticker("^TNX")
            yield_val = ticker.fast_info.last_price
            return round(yield_val, 3) if yield_val else 4.500
        except Exception as e:
            print(f"Macro yield error: {e}")
            return 4.500  # Fallback

    @classmethod
    def _get_market_regime(cls) -> dict:
        """S&P 500 기준 시장 국면 (Bull/Bear) 판단"""
        try:
            ticker = yf.Ticker("^GSPC")
            hist = ticker.history(period="1y")
            
            if len(hist) < 200:
                return {"status": "Unknown", "ma200": None}
            
            current_price = hist['Close'].iloc[-1]
            ma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
            
            status = "Bull" if current_price > ma200 else "Bear"
            diff_pct = ((current_price - ma200) / ma200) * 100
            
            return {
                "status": status,
                "current": round(float(current_price), 2),
                "ma200": round(float(ma200), 2),
                "diff_pct": round(float(diff_pct), 2)
            }
        except Exception as e:
            print(f"Macro regime error: {e}")
            return {"status": "Unknown", "ma200": None}

    @classmethod
    def _get_vix(cls) -> float:
        """VIX 공포 지수 (^VIX)"""
        try:
            ticker = yf.Ticker("^VIX")
            return round(ticker.fast_info.last_price, 2)
        except:
            return 20.00 # Neutral fallback

    @classmethod
    def _get_fear_greed_index(cls) -> int:
        """CNN 공포탐욕지수 추정 (0-100)"""
        try:
            # 실시간 크롤링보다는 신뢰성 있는 오픈 API 또는 이동평균 기반 추정치 사용
            # 여기서는 편의상 S&P500의 이격도를 이용한 근사치를 사용하거나, 
            # 가능한 경우 외부 API(예: alternative.me 또는 전용 래퍼)를 사용합니다.
            import requests
            # 전용 API가 불안정할 경우를 대비해 S&P500 지수 위치로 보수적으로 추정 (Fallback)
            ticker = yf.Ticker("^GSPC")
            price = ticker.fast_info.last_price
            ma125 = ticker.history(period="200d")['Close'].tail(125).mean()
            
            # 지수 대비 가격 위치로 심리 추정 (임시 로직)
            fng = 50 + ((price - ma125) / ma125 * 500)
            return int(max(0, min(100, fng)))
        except:
            return 50

    @classmethod
    def _get_sector_performance(cls) -> dict:
        """주요 섹터별 당일 등락률 (Sector Rotation 감지용)"""
        sectors = {
            "XLK": "Technology",
            "XLF": "Financials",
            "XLV": "Health Care",
            "XLE": "Energy",
            "XLY": "Consumer Discretionary",
            "XLP": "Consumer Staples",
            "XLU": "Utilities",
            "XLI": "Industrials",
            "XLB": "Materials",
            "XLRE": "Real Estate",
            "XLC": "Communication"
        }
        results = {}
        try:
            for etf, name in sectors.items():
                t = yf.Ticker(etf)
                price = t.fast_info.last_price
                prev = t.fast_info.previous_close
                if price and prev:
                    change = ((price - prev) / prev) * 100
                    results[name] = round(change, 2)
                else:
                    results[name] = 0.0
        except Exception as e:
            print(f"Error fetching sector data: {e}")
        return results
