import requests
import json
import logging
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_fetcher")

class KisFetcher:
    """
    한국투자증권(KIS) REST API를 통해 원시 데이터를 수집하는 헬퍼 클래스
    - 모의투자(VTS) 환경 호환성을 고려하여 설계되었습니다.
    """
    
    @staticmethod
    def _get_headers(token: str, tr_id: str = None, api_name: str = None) -> dict:
        """KIS API 공통 헤더 생성 (DB 연동 지원)"""
        from services.stock_meta_service import StockMetaService
        
        # tr_id가 없으면 api_name으로 DB에서 조회
        if not tr_id and api_name:
            is_vts = "vts" in Config.KIS_BASE_URL.lower()
            tr_id = StockMetaService.get_tr_id(api_name, is_vts=is_vts)
            logger.info(f"🔍 TR ID lookup for {api_name} (vts={is_vts}): {tr_id}")
            
        if not tr_id:
            logger.warning(f"⚠️ TR ID not found for API: {api_name}. Using fallback.")

        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P" # 개인 고객
        }

    @classmethod
    def fetch_domestic_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """
        국내 주식 기본 시세 및 지표 수집
        """
        api_name = "주식현재가_시세"
        path = (meta and meta.get('api_path')) or "/uapi/domestic-stock/v1/quotations/inquire-price"
        market = (meta and meta.get('api_market_code')) or "J"
        
        url = f"{Config.KIS_BASE_URL}{path}"
        
        params = {
            "fid_cond_mrkt_div_code": market,
            "fid_input_iscd": ticker
        }
        
        try:
            headers = cls._get_headers(token, api_name=api_name)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching domestic price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """
        해외 주식 상세 시세 및 지표 수집
        """
        api_name = "해외주식_상세시세"
        path = (meta and meta.get('api_path')) or "/uapi/overseas-stock/v1/quotations/price-detail"
        market = (meta and meta.get('api_market_code')) or "NASD"
        
        url = f"{Config.KIS_BASE_URL}{path}"
        
        # market mapping (NASD -> NAS 등)
        market_map = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_market = market_map.get(market.upper(), market.upper())
        
        params = {
            "AUTH": "",
            "EXCD": kis_market,
            "SYMB": ticker
        }
        
        try:
            headers = cls._get_headers(token, api_name=api_name)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching overseas price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_domestic_financials(cls, token: str, ticker: str) -> dict:
        """
        국내 주식 재무제표
        """
        api_name = "주식잔고조회" # 목록에 맞는 것으로 대체
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/financial-statement"
        
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
            "fid_div_cls_code": "0"
        }
        
        try:
            headers = cls._get_headers(token, api_name=api_name)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching domestic financials for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_ranking(cls, token: str, excd: str = "NAS") -> dict:
        """
        해외 주식 시가총액 순위 조회
        """
        api_name = "해외주식_시가총액순위"
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/ranking/market-cap"
        
        params = {
            "KEYB": "",
            "AUTH": "",
            "EXCD": excd,
            "VOL_RANG": "0" # 전체 거래량
        }
        
        try:
            headers = cls._get_headers(token, api_name=api_name)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code != 200:
                logger.error(f"❌ KIS API Error {res.status_code}: {res.text}")
            res.raise_for_status()
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching overseas ranking for {excd}: {e}")
            return {}
