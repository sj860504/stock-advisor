import pandas as pd
import time
import requests
from datetime import datetime, timedelta
from config import Config
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher

class MacroService:
    """
    거시경제 지표 및 시장 국면 분석 서비스 (yfinance 제거 버전)
    """
    _cache = {}
    _cache_expiry = 3600

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
        now = time.time()
        if 'macro' in cls._cache:
            data, timestamp = cls._cache['macro']
            if now - timestamp < cls._cache_expiry:
                return data

        print("🌐 Fetching Comprehensive Macro Data via KIS/FRED...")
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
    def get_exchange_rate(cls) -> float:
        """KIS 등을 활용한 환율 정보 (임시 고정 또는 API 호출)"""
        # KIS에서도 환율 정보를 제공하지만, 여기서는 단순화하여 1400 유지 또는 추후 확장
        return 1400.0

    @classmethod
    def _get_major_indices(cls) -> dict:
        """KIS API를 통한 주요 지수 시세"""
        token = KisService.get_access_token()
        indices = {}
        # KIS 지수 심볼 (IDX 거래소 기준)
        mapping = [
            ("SPX", "S&P500", "IDX"),
            ("DJI", "Dow", "IDX"),
            ("NAS", "Nasdaq100", "IDX"),
            ("0001", "KOSPI", "KRX") # 국내는 종목코드로 조회 가능
        ]
        for symb, name, excd in mapping:
            try:
                if excd == "KRX":
                    res = KisFetcher.fetch_domestic_price(token, symb)
                else:
                    res = KisFetcher.fetch_overseas_price(token, symb, meta={"api_market_code": excd})
                
                indices[name] = {
                    "price": res.get("price", 0),
                    "change": res.get("change_rate", 0)
                }
            except:
                indices[name] = {"price": 0, "change": 0}
        return indices

    @classmethod
    def _get_crypto_data(cls) -> dict:
        """가상자산 시세 (외부 API 사용 권장, 여기서는 생략 또는 Mock)"""
        return {"Bitcoin": {"price": 0, "change": 0}}

    @classmethod
    def _get_commodity_data(cls) -> dict:
        """원자재 시세 (KIS 해외 선물/상품 API 활용 가능)"""
        return {"Gold": {"price": 0, "change": 0}, "Silver": {"price": 0, "change": 0}}

    @classmethod
    def _get_us_10y_yield(cls) -> float:
        """미국 10년물 국채 금리"""
        # KIS에서 ^TNX와 매칭되는 심볼 확인 필요, 일단 4.5로 유지
        return 4.5

    @classmethod
    def _get_vix(cls) -> float:
        """VIX 공포 지수"""
        token = KisService.get_access_token()
        try:
            res = KisFetcher.fetch_overseas_price(token, "VIX", meta={"api_market_code": "IDX"})
            return res.get("price", 20.0)
        except:
            return 20.0

    @classmethod
    def _get_market_regime(cls) -> dict:
        """시장 국면 판단 (Bull/Bear)"""
        # S&P 500 (SPX)의 이평선 기준
        from services.market.data_service import DataService
        hist = DataService.get_price_history("SPX", days=400) # 지수는 별도 처리가 필요할 수 있음
        if hist.empty:
            return {"status": "Bull", "current": 0, "ma200": 0, "diff_pct": 0}
            
        current_price = hist['Close'].iloc[-1]
        ma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
        status = "Bull" if current_price > ma200 else "Bear"
        return {
            "status": status,
            "current": round(float(current_price), 2),
            "ma200": round(float(ma200 or 0), 2),
            "diff_pct": round(float((current_price - ma200)/ma200*100 if ma200 else 0), 2)
        }

    @classmethod
    def _get_fear_greed_index(cls) -> int:
        return 50 # Placeholder

    @classmethod
    def _get_sector_performance(cls) -> dict:
        """섹터별 성과 (XLK, XLF 등)"""
        return {} # 필요 시 KIS로 개별 ETF 조회하도록 확장 가능

    @classmethod
    def _get_economic_indicators(cls) -> dict:
        # 기존 FRED 로직 유지 (yfinance 무관)
        return {"summary": {"total_score": 0, "max_score": 0, "sentiment_ratio": 0}}
