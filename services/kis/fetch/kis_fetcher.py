import requests
import json
import logging
import time
import threading
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_fetcher")

# KIS API 상수
KIS_RATE_LIMIT_MSG_CD = "EGW00201"
REQUEST_TIMEOUT_DEFAULT = 5


class KisFetcher:
    """
    한국투자증권(KIS) REST API를 통해 원시 데이터를 수집하는 헬퍼 클래스
    - 데이터베이스(api_tr_meta)에 저장된 TR ID와 경로 정보를 동적으로 사용합니다.
    - 모의투자(VTS) 및 실전투자 환경을 Config.KIS_IS_VTS 플래그로 구분하여 대응합니다.
    """
    _req_lock = threading.Lock()
    _last_req_ts = 0.0
    _min_req_interval = 0.55  # VTS 기준 약 2TPS 제한 대응
    
    @staticmethod
    def _get_api_info(api_name: str) -> tuple:
        """DB에서 TR ID와 경로 정보를 가져옵니다 (환경 자동 선택)."""
        from services.market.stock_meta_service import StockMetaService
        return StockMetaService.get_api_info(api_name, is_vts=Config.KIS_IS_VTS)

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
    def _throttle_request(cls):
        with cls._req_lock:
            now = time.time()
            elapsed = now - cls._last_req_ts
            if elapsed < cls._min_req_interval:
                time.sleep(cls._min_req_interval - elapsed)
            cls._last_req_ts = time.time()

    @classmethod
    def _is_rate_limited_response(cls, response: requests.Response) -> bool:
        if response.status_code in (429, 500):
            return True
        text = response.text or ""
        if "초당 거래건수" in text:
            return True
        try:
            body = response.json()
            if body.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD:
                return True
        except Exception:
            pass
        return False

    @classmethod
    def _get_with_retry(cls, url: str, headers: dict, params: dict, timeout: int = None, retries: int = 4):
        if timeout is None:
            timeout = REQUEST_TIMEOUT_DEFAULT
        last_response = None
        for attempt in range(retries):
            cls._throttle_request()
            try:
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
                last_response = response
                if cls._is_rate_limited_response(response):
                    wait_sec = 1.2 * (attempt + 1)
                    logger.warning(f"⏳ TPS limit hit. retry {attempt + 1}/{retries} in {wait_sec:.1f}s...")
                    time.sleep(wait_sec)
                    continue
                return response
            except Exception:
                time.sleep(0.7 * (attempt + 1))
        return last_response

    @classmethod
    def fetch_domestic_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """국내 주식 현재가 조회"""
        tr_id, path = cls._get_api_info("주식현재가_시세")
        if not path: return {}
        
        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
        
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            if response.status_code == 200:
                response_data = response.json()
                output = response_data.get("output", {})
                if not output:
                    logger.warning(f"⚠️ Domestic price output empty for {ticker}: {response_data.get('msg1')}")
                    return {}
                def safe_float(val, default=0.0):
                    try:
                        if val is None or str(val).strip() == "": return default
                        return float(val)
                    except: return default

                return {
                    "price": safe_float(output.get('stck_prpr')),
                    "prev_close": safe_float(output.get('stck_sdpr')),
                    "change": safe_float(output.get('prdy_vrss')),
                    "change_rate": safe_float(output.get('prdy_ctrt')),
                    "per": safe_float(output.get('per')),
                    "pbr": safe_float(output.get('pbr')),
                    "eps": safe_float(output.get('eps')),
                    "bps": safe_float(output.get('bps')),
                    "market_cap": safe_float(output.get('lstn_stcn')) * safe_float(output.get('stck_prpr')) if output.get('lstn_stcn') else 0,
                    "high52": safe_float(output.get('h52_curr_prc')),
                    "low52": safe_float(output.get('l52_curr_prc')),
                    "volume": safe_float(output.get('acml_vol')),
                    "amount": safe_float(output.get('acml_tr_pbmn')),
                    "name": output.get('hts_kor_isnm', ticker),
                    "raw": output
                }
            elif response.status_code == 500 or "초당" in response.text:
                logger.warning(f"⏳ TPS Limit reached for {ticker}. Waiting 1.5s...")
                time.sleep(1.5)
                return {}
            else:
                logger.error(f"❌ KIS Domestic Price Error {response.status_code}: {response.text}")
                return {}
        except Exception as e:
            logger.error(f"Error fetching domestic price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_detail(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """해외 주식 상세 시세 조회 (PER, PBR, EPS 등 포함)"""
        from models.kis_schemas import OverseasDetailPriceResponse
        
        tr_id, path = cls._get_api_info("해외주식_상세시세")
        if not path:
             path = "/uapi/overseas-price/v1/quotations/price-detail"
        
        market = (meta and meta.get('api_market_code')) or "NAS"
        market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_market = market_map_4to3.get(market.upper(), market.upper())
        if len(kis_market) > 3 and kis_market != "IDX":
             kis_market = kis_market[:3]

        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"AUTH": "", "EXCD": kis_market, "SYMB": ticker}
        
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            if response.status_code == 200:
                response_data = response.json()
                output_raw = response_data.get("output", {})
                if output_raw:
                    # 스키마를 통한 검증 및 파싱
                    output = OverseasDetailPriceResponse(**output_raw)
                    
                    def safe_float(val, default=0.0):
                        try:
                            if val is None or str(val).strip() == "": return default
                            return float(val)
                        except: return default

                    return {
                        "price": safe_float(output.last),
                        "prev_close": safe_float(output.base),
                        "change": safe_float(output.t_xdif or output.p_xdif), # 당일/전일 대비 유연하게
                        "change_rate": safe_float(output.t_xrat or output.p_xrat),
                        "per": safe_float(output.perx),
                        "pbr": safe_float(output.pbrx),
                        "eps": safe_float(output.epsx),
                        "bps": safe_float(output.bpsx),
                        "market_cap": safe_float(output.tomv),
                        "high52": safe_float(output.h52p),
                        "low52": safe_float(output.l52p),
                        "volume": safe_float(output.tvol),
                        "amount": safe_float(output.tamt),
                        "name": output.hnam or ticker,
                        "raw": output.model_dump()
                    }
            return {}
        except Exception as e:
            logger.error(f"❌ Overseas Detail Price Exception for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """해외 주식 기본 현재가 조회 (HHDFS00000300)"""
        tr_id, path = cls._get_api_info("해외주식_현재가")
        if not path: return {}
            
        market = (meta and meta.get('api_market_code')) or "NAS"
        market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_market = market_map_4to3.get(market.upper(), market.upper())
        if len(kis_market) > 3 and kis_market != "IDX":
             kis_market = kis_market[:3]

        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"AUTH": "", "EXCD": kis_market, "SYMB": ticker}
        
        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT)
            if response.status_code == 200:
                response_data = response.json()
                output = response_data.get("output", {})
                if output:
                    def safe_float(val, default=0.0):
                        try:
                            if val is None or str(val).strip() == "":
                                return default
                            return float(val)
                        except Exception:
                            return default
                    price = safe_float(output.get("last")) or safe_float(output.get("clos"))
                    return {
                        "price": price,
                        "prev_close": safe_float(output.get('base')),
                        "change": safe_float(output.get('diff')),
                        "change_rate": safe_float(output.get('rate')),
                        "name": output.get('hnam', ticker),
                        "raw": output
                    }
            return {}
        except Exception as e:
            logger.error(f"❌ Overseas Price Exception for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_ranking(cls, token: str, excd: str = "NAS") -> dict:
        """해외 주식 시가총액 순위 조회 (VTS 대응)"""
        tr_id, path = cls._get_api_info("해외주식_시가총액순위")
        if not path: return {}
        
        # EXCD 보정 (3자리만 사용)
        market_map = {"NASD": "NAS", "NAS": "NAS", "NYSE": "NYS", "NYS": "NYS", "AMEX": "AMS", "AMS": "AMS"}
        kis_excd = market_map.get(excd.upper(), excd.upper()[:3])

        url = f"{Config.KIS_BASE_URL}{path}"
        params = {"AUTH": "", "EXCD": kis_excd, "GUBN": "0"}
        
        for attempt in range(2):
            try:
                headers = cls._get_headers(token, tr_id=tr_id)
                response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT)
                if response.status_code == 200:
                    response_data = response.json()
                    if response_data.get("output2"):
                        response_data["output"] = response_data["output2"]
                        return response_data
                elif response.status_code == 500 or "초당" in response.text:
                    logger.warning(f"⏳ Rate limit hit (Overseas Ranking {kis_excd}). Retrying in 1.5s...")
                    time.sleep(1.5)
                    continue
                else:
                    logger.error(f"❌ Overseas Ranking Error {response.status_code}: {response.text}")
                    break
            except Exception as e:
                logger.error(f"❌ Overseas Ranking Exception: {e}")
                time.sleep(1.5)
        return {}

    @classmethod
    def fetch_domestic_ranking(cls, token: str, mrkt_div: str = "0000") -> dict:
        """국내 주식 시가총액 순위 조회 (VTS 대응 폴백 포함)"""
        # 모의투자(VTS) 환경에서는 랭킹 API가 작동하지 않으므로 마스터 파일 기반 폴백 사용
        if Config.KIS_IS_VTS:
            from services.market.master_data_service import MasterDataService
            top_stocks = MasterDataService.get_top_market_cap_tickers(100)
            if top_stocks:
                logger.info(f"💡 VTS mode: Using MasterDataService for domestic ranking.")
                return {"output": top_stocks}

        tr_id, path = cls._get_api_info("국내주식_시가총액순위")
        if not path: return {}
        
        url = f"{Config.KIS_BASE_URL}{path}"
        for div_code in ["J"]: # '0'은 유효하지 않으므로 'J'만 시도
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
                response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT)
                if response.status_code == 200:
                    response_data = response.json()
                    output = response_data.get("output") or response_data.get("output2")
                    if output:
                        logger.info(f"✅ Success fetching domestic ranking with div_code={div_code} (Count: {len(output)})")
                        response_data["output"] = output
                        return response_data
                    logger.warning(f"⚠️ Domestic ranking output empty for {div_code}: {response_data.get('msg1')}")
                elif response.status_code == 500 or "초당" in response.text:
                    logger.warning("⏳ Rate limit or 500 error for ranking. Waiting 1.5s...")
                    time.sleep(1.5)
                    continue
                else:
                    logger.error(f"❌ Domestic Ranking Error {response.status_code}: {response.text}")
                time.sleep(1.2)
            except Exception as e:
                logger.error(f"❌ Domestic Ranking Exception: {e}")
                time.sleep(1.2)
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
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            return response.json() if response.status_code == 200 else {}
        except Exception as e:
            logger.error(f"Error fetching daily price for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_daily_price(cls, token: str, ticker: str, start_date: str, end_date: str) -> dict:
        """해외 주식 일자별 시세 조회"""
        from services.market.stock_meta_service import StockMetaService
        tr_id, path = StockMetaService.get_api_info("해외주식_기간별시세")
        if not tr_id:
            tr_id = "HHDFS76240000"
        if not path:
            # 해외 일봉은 overseas-price 경로가 맞음
            path = "/uapi/overseas-price/v1/quotations/dailyprice"
        
        url = f"{Config.KIS_BASE_URL}{path}"
        
        excd = "NAS"
        if ticker in ["SPX", "NAS", "VIX", "DJI", "TSX"]:
            excd = "IDX"
        else:
            try:
                meta = StockMetaService.get_stock_meta(ticker)
                if meta and meta.api_market_code:
                    excd = meta.api_market_code
            except: pass

        if tr_id == "FHKST03030100":
            # 차트 API (지수용 등)
            mrkt_map = {"NASD": "N", "NAS": "N", "NYSE": "Y", "NYS": "Y", "AMEX": "A", "AMS": "A", "IDX": "U"}
            mrkt_code = mrkt_map.get(excd.upper(), "N")
            
            params = {
                "fid_cond_mrkt_div_code": mrkt_code,
                "fid_input_iscd": ticker,
                "fid_input_date_1": start_date,
                "fid_input_date_2": end_date,
                "fid_period_div_code": "D"
            }
        else:
            # 기존 해외주식_기간별시세 (HHDFS76240000)
            # VTS여도 HHDFS TR이면 3자리를 기대함
            market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
            kis_excd = market_map_4to3.get(excd.upper(), excd.upper())
            if len(kis_excd) > 3 and kis_excd != "IDX": kis_excd = kis_excd[:3]
                
            params = {
                "AUTH": "",
                "EXCD": kis_excd, 
                "SYMB": ticker,
                "GUBN": "0",
                "BYMD": "",
                "MODP": "0" 
            }

        try:
            headers = cls._get_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=5)
            if response is None:
                return {}
            if response.status_code != 200:
                logger.error(f"❌ Overseas Price Error {response.status_code} [Daily]: {url} | TR: {tr_id} | Params: {params} | Body: {response.text}")
                return {}
            response_data = response.json()
            if response_data.get("output2") and not response_data.get("output"):
                response_data["output"] = response_data["output2"]
            return response_data
        except Exception as e:
            logger.error(f"Error fetching overseas daily price for {ticker}: {e}")
            return {}
