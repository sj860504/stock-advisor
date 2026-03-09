import requests
import json
import logging
import time
import threading
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_fetcher")

# KIS API constants
KIS_RATE_LIMIT_MSG_CD = "EGW00201"
REQUEST_TIMEOUT_DEFAULT = 5


def _safe_float(val, default: float = 0.0) -> float:
    """Convert string/None to float. Returns default on failure."""
    try:
        if val is None or str(val).strip() == "":
            return default
        return float(val)
    except Exception:
        return default


class KisFetcher:
    """KIS REST API raw data fetcher.
    - Dynamically uses TR ID and path from DB (api_tr_meta).
    - Supports both paper trading (VTS) and live trading via Config.KIS_IS_VTS flag.
    """
    _req_lock = threading.Lock()
    _last_req_ts = 0.0
    _min_req_interval = 0.55  # ~2 TPS rate limit for VTS
    
    @staticmethod
    def _get_api_info(api_name: str) -> tuple:
        """Get TR ID and path from DB (auto-selects environment)."""
        from services.market.stock_meta_service import StockMetaService
        return StockMetaService.get_api_info(api_name, is_vts=Config.KIS_IS_VTS)

    @staticmethod
    def _get_headers(token: str, tr_id: str) -> dict:
        """Build common KIS API headers."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id,
            "custtype": "P"
        }

    @staticmethod
    def _get_price_base_url() -> str:
        """Base URL for price quotes. Uses live server if real credentials configured, otherwise VTS."""
        return Config.KIS_REAL_BASE_URL if Config.has_real_credentials() else Config.KIS_BASE_URL

    @classmethod
    def _get_price_headers(cls, token: str, tr_id: str) -> dict:
        """Headers for price quotes. Uses live credentials if configured."""
        if Config.has_real_credentials():
            from services.kis.kis_service import KisService
            real_token = KisService.get_real_access_token()
            return {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {real_token}",
                "appkey": Config.KIS_REAL_APP_KEY,
                "appsecret": Config.KIS_REAL_APP_SECRET,
                "tr_id": tr_id,
                "custtype": "P",
            }
        return cls._get_headers(token, tr_id)

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
        if "초당 거래건수" in text:  # KIS TPS rate limit message
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
        """Fetch domestic stock current price."""
        tr_id, path = cls._get_api_info("주식현재가_시세")
        if not path: return {}

        url = f"{cls._get_price_base_url()}{path}"
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}

        try:
            headers = cls._get_price_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            if response.status_code == 200:
                response_data = response.json()
                output = response_data.get("output", {})
                if not output:
                    logger.warning(f"⚠️ Domestic price output empty for {ticker}: {response_data.get('msg1')}")
                    return {}
                return {
                    "price": _safe_float(output.get('stck_prpr')),
                    "prev_close": _safe_float(output.get('stck_sdpr')),
                    "change": _safe_float(output.get('prdy_vrss')),
                    "change_rate": _safe_float(output.get('prdy_ctrt')),
                    "per": _safe_float(output.get('per')),
                    "pbr": _safe_float(output.get('pbr')),
                    "eps": _safe_float(output.get('eps')),
                    "bps": _safe_float(output.get('bps')),
                    "market_cap": _safe_float(output.get('lstn_stcn')) * _safe_float(output.get('stck_prpr')) if output.get('lstn_stcn') else 0,
                    "high52": _safe_float(output.get('h52_curr_prc')),
                    "low52": _safe_float(output.get('l52_curr_prc')),
                    "volume": _safe_float(output.get('acml_vol')),
                    "amount": _safe_float(output.get('acml_tr_pbmn')),
                    "name": output.get('hts_kor_isnm', ticker),
                    "raw": output
                }
            elif response.status_code == 500 or "\ucd08\ub2f9" in response.text:
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
        """Fetch overseas stock detailed price quote (incl. PER, PBR, EPS)."""
        from models.kis_schemas import OverseasDetailPriceResponse
        
        tr_id, path = cls._get_api_info("해외주식_상세시세")
        if not path:
             path = "/uapi/overseas-price/v1/quotations/price-detail"
        
        market = (meta and meta.get('api_market_code')) or "NAS"
        market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_market = market_map_4to3.get(market.upper(), market.upper())
        if len(kis_market) > 3 and kis_market != "IDX":
             kis_market = kis_market[:3]

        url = f"{cls._get_price_base_url()}{path}"
        params = {"AUTH": "", "EXCD": kis_market, "SYMB": ticker}

        try:
            headers = cls._get_price_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            if response.status_code == 200:
                response_data = response.json()
                output_raw = response_data.get("output", {})
                if output_raw:
                    # Validate and parse via schema
                    output = OverseasDetailPriceResponse(**output_raw)
                    return {
                        "price": _safe_float(output.last),
                        "prev_close": _safe_float(output.base),
                        "change": _safe_float(output.t_xdif or output.p_xdif),
                        "change_rate": _safe_float(output.t_xrat or output.p_xrat),
                        "per": _safe_float(output.perx),
                        "pbr": _safe_float(output.pbrx),
                        "eps": _safe_float(output.epsx),
                        "bps": _safe_float(output.bpsx),
                        "market_cap": _safe_float(output.tomv),
                        "high52": _safe_float(output.h52p),
                        "low52": _safe_float(output.l52p),
                        "volume": _safe_float(output.tvol),
                        "amount": _safe_float(output.tamt),
                        "name": output.hnam or ticker,
                        "raw": output.model_dump()
                    }
            return {}
        except Exception as e:
            logger.error(f"❌ Overseas Detail Price Exception for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_price(cls, token: str, ticker: str, meta: dict = None) -> dict:
        """Fetch overseas stock basic current price (HHDFS00000300)."""
        tr_id, path = cls._get_api_info("해외주식_현재가")
        if not path: return {}
            
        market = (meta and meta.get('api_market_code')) or "NAS"
        market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
        kis_market = market_map_4to3.get(market.upper(), market.upper())
        if len(kis_market) > 3 and kis_market != "IDX":
             kis_market = kis_market[:3]

        url = f"{cls._get_price_base_url()}{path}"
        params = {"AUTH": "", "EXCD": kis_market, "SYMB": ticker}

        try:
            headers = cls._get_price_headers(token, tr_id=tr_id)
            response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT)
            if response.status_code == 200:
                response_data = response.json()
                output = response_data.get("output", {})
                if output:
                    price = _safe_float(output.get("last")) or _safe_float(output.get("clos"))
                    return {
                        "price": price,
                        "prev_close": _safe_float(output.get('base')),
                        "change": _safe_float(output.get('diff')),
                        "change_rate": _safe_float(output.get('rate')),
                        "name": output.get('hnam', ticker),
                        "raw": output
                    }
            return {}
        except Exception as e:
            logger.error(f"❌ Overseas Price Exception for {ticker}: {e}")
            return {}

    @classmethod
    def fetch_overseas_ranking(cls, token: str, excd: str = "NAS") -> dict:
        """Fetch overseas stock market cap ranking (VTS compatible)."""
        tr_id, path = cls._get_api_info("해외주식_시가총액순위")
        if not path: return {}
        
        # Normalize EXCD to 3 chars
        market_map = {"NASD": "NAS", "NAS": "NAS", "NYSE": "NYS", "NYS": "NYS", "AMEX": "AMS", "AMS": "AMS"}
        kis_excd = market_map.get(excd.upper(), excd.upper()[:3])

        url = f"{cls._get_price_base_url()}{path}"
        params = {"AUTH": "", "EXCD": kis_excd, "GUBN": "0"}

        for attempt in range(2):
            try:
                headers = cls._get_price_headers(token, tr_id=tr_id)
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
        """Fetch domestic stock market cap ranking (VTS uses master file fallback)."""
        # In VTS mode, always use master file fallback regardless of live credentials
        # (mixing live server URL with VTS params causes ERROR INPUT FIELD NOT FOUND)
        if Config.KIS_IS_VTS:
            from services.market.master_data_service import MasterDataService
            top_stocks = MasterDataService.get_top_market_cap_tickers(100)
            if top_stocks:
                logger.info(f"💡 VTS mode: Using MasterDataService for domestic ranking.")
                return {"output": top_stocks}

        tr_id, path = cls._get_api_info("국내주식_시가총액순위")
        if not path: return {}

        url = f"{cls._get_price_base_url()}{path}"
        for div_code in ["J"]:  # '0' is invalid, only try 'J'
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
                headers = cls._get_price_headers(token, tr_id=tr_id)
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
        """Fetch domestic stock daily OHLCV data."""
        tr_id, path = cls._get_api_info("국내주식_일자별시세")
        if not path: path = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        
        url = f"{cls._get_price_base_url()}{path}"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
            "fid_input_date_1": start_date,
            "fid_input_date_2": end_date,
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1"
        }
        try:
            headers = cls._get_price_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=4)
            if response is None:
                return {}
            return response.json() if response.status_code == 200 else {}
        except Exception as e:
            logger.error(f"Error fetching daily price for {ticker}: {e}")
            return {}

    @staticmethod
    def _build_overseas_daily_params(tr_id: str, ticker: str, excd: str, start_date: str, end_date: str) -> dict:
        """Build params dict for overseas daily price request based on TR ID."""
        if tr_id == "FHKST03030100":
            mrkt_map = {"NASD": "N", "NAS": "N", "NYSE": "Y", "NYS": "Y", "AMEX": "A", "AMS": "A", "IDX": "U"}
            mrkt_code = mrkt_map.get(excd.upper(), "N")
            return {
                "fid_cond_mrkt_div_code": mrkt_code,
                "fid_input_iscd": ticker,
                "fid_input_date_1": start_date,
                "fid_input_date_2": end_date,
                "fid_period_div_code": "D"
            }
        else:
            market_map_4to3 = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
            kis_excd = market_map_4to3.get(excd.upper(), excd.upper())
            if len(kis_excd) > 3 and kis_excd != "IDX":
                kis_excd = kis_excd[:3]
            return {
                "AUTH": "",
                "EXCD": kis_excd,
                "SYMB": ticker,
                "GUBN": "0",
                "BYMD": "",
                "MODP": "0"
            }

    @classmethod
    def _try_exchange_fallback(cls, ticker: str, url: str, headers: dict, params: dict) -> dict | None:
        """Try alternate exchange code (NAS<->NYS) when initial query returns empty. Returns data or None."""
        if "EXCD" not in params:
            return None
        alt_excd = "NYS" if params["EXCD"] == "NAS" else ("NAS" if params["EXCD"] == "NYS" else None)
        if not alt_excd:
            return None
        logger.info(f"🔄 {ticker} {params['EXCD']}→{alt_excd} fallback query (empty response)")
        alt_params = {**params, "EXCD": alt_excd}
        alt_response = cls._get_with_retry(url, headers=headers, params=alt_params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=2)
        if alt_response and alt_response.status_code == 200:
            alt_data = alt_response.json()
            if alt_data.get("output2"):
                alt_data["output"] = alt_data["output2"]
                try:
                    from services.market.stock_meta_service import StockMetaService
                    StockMetaService.update_market_code(ticker, alt_excd)
                    logger.info(f"✅ {ticker} api_market_code auto-corrected: {params['EXCD']} → {alt_excd}")
                except Exception:
                    pass
                return alt_data
        return None

    @classmethod
    def fetch_overseas_daily_price(cls, token: str, ticker: str, start_date: str, end_date: str) -> dict:
        """Fetch overseas stock daily OHLCV data."""
        from services.market.stock_meta_service import StockMetaService
        tr_id, path = StockMetaService.get_api_info("해외주식_기간별시세")

        url = f"{cls._get_price_base_url()}{path}"

        excd = "NAS"
        if ticker in ["SPX", "NAS", "VIX", "DJI", "TSX"]:
            excd = "IDX"
        else:
            try:
                meta = StockMetaService.get_stock_meta(ticker)
                if meta and meta.api_market_code:
                    excd = meta.api_market_code
            except: pass

        params = cls._build_overseas_daily_params(tr_id, ticker, excd, start_date, end_date)

        try:
            headers = cls._get_price_headers(token, tr_id=tr_id)
            response = cls._get_with_retry(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_DEFAULT, retries=5)
            if response is None:
                return {}
            if response.status_code != 200:
                logger.error(f"❌ Overseas Price Error {response.status_code} [Daily]: {url} | TR: {tr_id} | Params: {params} | Body: {response.text}")
                return {}
            response_data = response.json()
            if response_data.get("output2") and not response_data.get("output"):
                response_data["output"] = response_data["output2"]

            if tr_id != "FHKST03030100" and not response_data.get("output"):
                fallback = cls._try_exchange_fallback(ticker, url, headers, params)
                if fallback is not None:
                    return fallback

            return response_data
        except Exception as e:
            logger.error(f"Error fetching overseas daily price for {ticker}: {e}")
            return {}
