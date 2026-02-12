import requests
import json
import logging
import time
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_fetcher")

class KisFetcher:
    """
    한국투자증권(KIS) REST API를 통해 원시 데이터를 수집하는 헬퍼 클래스
    - 데이터베이스(api_tr_meta)에 저장된 TR ID와 경로 정보를 동적으로 사용합니다.
    - 모의투자(VTS) 및 실전투자 환경을 Config.KIS_IS_VTS 플래그로 구분하여 대응합니다.
    """
    
    @staticmethod
    def _get_api_info(api_name: str) -> tuple:
        """DB에서 TR ID와 경로 정보를 가져옵니다."""
        from services.market.stock_meta_service import StockMetaService
        tr_id = StockMetaService.get_tr_id(api_name, is_vts=Config.KIS_IS_VTS)
        meta = StockMetaService.get_api_meta(api_name)
        
        if not meta:
            logger.warning(f"⚠️ API meta not found for: {api_name}")
            return tr_id, None
            
        return tr_id, meta.api_path

    @staticmethod
    def _get_headers(token: str, tr_id: str) -> dict:
        """KIS API 공통 헤더 생성"""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P"
        }

    @classmethod
    def fetch_domestic_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """국내 주식 현재가 조회"""
        tr_id, path = cls._get_api_info("주식현재가_시세")
        if not path: return {}
        
        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
        
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code != 200:
                logger.error(f"❌ KIS Domestic Price Error {res.status_code}: {res.text}")
            res.raise_for_status()
            data = res.json()
            output = data.get('output', {})
            if output:
                return {
                    "price": float(output.get('stck_prpr', 0)),
                    "prev_close": float(output.get('stck_sdpr', 0)),
                    "change": float(output.get('prdy_vrss', 0)),
                    "change_rate": float(output.get('prdy_ctrt', 0)),
                    "per": float(output.get('per', 0)),
                    "pbr": float(output.get('pbr', 0)),
                    "eps": float(output.get('eps', 0)),
                    "bps": float(output.get('bps', 0)),
                    "market_cap": float(output.get('lstn_stcn', 0)) * float(output.get('stck_prpr', 0)) if output.get('lstn_stcn') else 0,
                    "name": output.get('hts_kor_isnm', ticker),
                    "raw": output
                }
            return {}
        except Exception as e:
            logger.error(f"Error fetching domestic price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """해외 주식 상세 시세 조회 (VTS 대응)"""
        # VTS에서는 마켓 코드에 따른 분기가 민감함. NAS vs NASD
        market_map = {
            "NASD": "NASD", "NAS": "NAS",  # NASD와 NAS를 혼용 시도
            "NYSE": "NYSE", "NYS": "NYS", 
            "AMEX": "AMEX", "AMS": "AMS"
        }
        market = (meta and meta.get('api_market_code')) or "NASD"
        kis_market = market_map.get(market.upper(), market.upper())

        # 1. 상세시세 시도 (HHDFS70200200)
        tr_id, path = cls._get_api_info("해외주식_상세시세")
        if path:
            # VTS에서 NASD로 시도
            test_markets = [kis_market, "NASD", "NAS"] if "NAS" in kis_market else [kis_market]
            for m in test_markets:
                url = f"{Config.KIS_BASE_URL}{path}"
                params = {"AUTH": "", "EXCD": m, "SYMB": ticker}
                try:
                    headers = cls._get_headers(token, tr_id=tr_id)
                    res = requests.get(url, headers=headers, params=params, timeout=5)
                    if res.status_code == 200:
                        data = res.json()
                        output = data.get('output', {})
                        if output:
                            return {
                                "price": float(output.get('last', 0)),
                                "prev_close": float(output.get('base', 0)),
                                "change": float(output.get('diff', 0)),
                                "change_rate": float(output.get('rate', 0)),
                                "name": output.get('hnam', ticker),
                                "raw": output
                            }
                    time.sleep(0.2) # 속도 제한 대응
                except: pass

        # 2. 폴백: 해외주식_현재가 (HHDFS00000300)
        tr_id, path = cls._get_api_info("해외주식_현재가")
        if path:
            url = f"{Config.KIS_BASE_URL}{path}"
            params = {"AUTH": "", "EXCD": kis_market, "SYMB": ticker}
            try:
                headers = cls._get_headers(token, tr_id=tr_id)
                res = requests.get(url, headers=headers, params=params, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    output = data.get('output', {})
                    if output:
                        return {
                            "price": float(output.get('last', 0)),
                            "prev_close": float(output.get('base', 0)),
                            "change": float(output.get('diff', 0)),
                            "change_rate": float(output.get('rate', 0)),
                            "name": output.get('hnam', ticker),
                            "raw": output
                        }
            except: pass
        return {}

    @classmethod
    def fetch_overseas_ranking(cls, token: str, excd: str = "NAS") -> dict:
        """해외 주식 시가총액 순위 조회"""
        tr_id, path = cls._get_api_info("해외주식_시가총액순위")
        if not path: return {}
        
        # EXCD 보정
        market_map = {"NASD": "NASD", "NAS": "NASD", "NYSE": "NYSE", "AMS": "AMEX"}
        kis_excd = market_map.get(excd.upper(), excd.upper())

        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"AUTH": "", "EXCD": kis_excd, "VOL_RANG": "0"}
        
        for attempt in range(3):
            try:
                headers = cls._get_headers(token, tr_id=tr_id)
                res = requests.get(url, headers=headers, params=params, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if data.get('output'):
                        return data
                elif res.status_code == 500 or "초당" in res.text:
                    logger.warning(f"⏳ Rate limit hit (Overseas Ranking). Retrying in 1s...")
                    time.sleep(1.1)
                    continue
                else: break
            except:
                time.sleep(1.1)
        return {}

    @classmethod
    def fetch_domestic_ranking(cls, token: str, mrkt_div: str = "0000") -> dict:
        """국내 주식 시가총액 순위 조회"""
        tr_id, path = cls._get_api_info("국내주식_시가총액순위")
        if not path: return {}
        
        url = f"{Config.KIS_BASE_URL}{path}"
        # VTS에서는 J와 0을 모두 시도하며 지연을 둡니다.
        for div_code in ["J", "0"]:
            params = {
                "fid_cond_mrkt_div_code": div_code,
                "fid_cond_scr_div_code": "20170",
                "fid_div_cls_code": "0",
                "fid_rank_sort_cls_code": "0",
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "0",
                "fid_input_iscd_1": mrkt_div
            }
            try:
                headers = cls._get_headers(token, tr_id=tr_id)
                res = requests.get(url, headers=headers, params=params, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if data.get('output'):
                        return data
                elif res.status_code == 500 or "초당" in res.text:
                    time.sleep(1.1)
                    continue
                time.sleep(1.1)
            except: pass
        return {}

    @classmethod
    def fetch_daily_price(cls, token: str, ticker: str, start_date: str, end_date: str) -> dict:
        """국내 주식 일자별 시세 조회"""
        tr_id, path = cls._get_api_info("국내주식_일자별시세")
        if not path: path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        
        url = f"{Config.KIS_BASE_URL}{path}"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
            "fid_input_date_1": start_date,
            "fid_input_date_2": end_date,
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1"
        }
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching daily price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_daily_price(cls, token: str, ticker: str, start_date: str, end_date: str) -> dict:
        """해외 주식 일자별 시세 조회"""
        tr_id, path = cls._get_api_info("해외주식_기간별시세")
        if not path: path = "/uapi/overseas-stock/v1/quotations/dailyprice"
        
        url = f"{Config.KIS_BASE_URL}{path}"
        
        # 지수(Index) 심볼 체크 (SPX, NAS, VIX 등)
        excd = "NAS"
        if ticker in ["SPX", "NAS", "VIX", "DJI", "TSX"]:
            excd = "IDX"
            
        params = {
            "AUTH": "",
            "EXCD": excd, 
            "SYMB": ticker,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "Y"
        }
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            res = requests.get(url, headers=headers, params=params, timeout=5)
            # 해외주식은 응답 구조가 다를 수 있음
            return res.json()
        except Exception as e:
            logger.error(f"Error fetching overseas daily price for {ticker}: {e}")
            return {}
