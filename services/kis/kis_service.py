import requests
import json
import time
import threading
from datetime import datetime
from typing import Optional
from config import Config
from services.market.market_hour_service import MarketHourService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("kis_service")

# KIS API constants
KIS_RATE_LIMIT_MSG_CD = "EGW00201"
TOKEN_REQUEST_TIMEOUT = 5
BALANCE_REQUEST_TIMEOUT = 8
ORDER_REQUEST_TIMEOUT = 10
MAX_BALANCE_RETRIES = 3


class KisService:
    """Korea Investment & Securities (KIS) API integration service."""
    _access_token = None
    _token_expiry = None
    _last_balance_data = None
    _last_overseas_balance_data = None
    _req_lock = threading.Lock()
    _last_req_ts = 0.0
    _min_req_interval = 0.55  # ~2 TPS rate limit for VTS

    # Live trading account token (price quote only)
    _real_access_token = None
    _real_token_expiry = None
    
    @classmethod
    def _throttle_request(cls) -> None:
        """Throttle requests to comply with TPS rate limit."""
        with cls._req_lock:
            now = time.time()
            elapsed = now - cls._last_req_ts
            if elapsed < cls._min_req_interval:
                sleep_time = cls._min_req_interval - elapsed
                time.sleep(sleep_time)
            cls._last_req_ts = time.time()
    
    @classmethod
    def _is_rate_limited_response(cls, response: requests.Response) -> bool:
        """Check if response indicates TPS rate limit."""
        if response.status_code in (429, 500):
            try:
                body = response.json()
                if body.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD or "초당 거래건수" in (body.get("msg1") or ""):
                    return True
            except Exception:
                pass
        return False

    @classmethod
    def _get_account_parts(cls) -> tuple[str, str]:
        """Split account number into KIS parameter format.
        - Accepts: 50162391-01 / 5016239101 / 50162391
        - Returns: (CANO(8), ACNT_PRDT_CD(2))
        """
        raw = (Config.KIS_ACCOUNT_NO or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) >= 10:
            return digits[:8], digits[8:10]
        if len(digits) == 8:
            return digits, "01"
        logger.error(f"❌ Invalid KIS_ACCOUNT_NO format: '{raw}'")
        return "", "01"
    
    @classmethod
    def _load_cached_token(cls) -> Optional[str]:
        """Load valid token from DB cache. Returns None if missing or expired."""
        try:
            from repositories.settings_repo import SettingsRepo
            token = SettingsRepo.get("KIS_ACCESS_TOKEN")
            expiry_str = SettingsRepo.get("KIS_TOKEN_EXPIRY")
            if token and expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
                if datetime.now() < expiry:
                    cls._access_token = token
                    cls._token_expiry = expiry
                    logger.info("📄 KIS Access Token loaded from DB.")
                    return cls._access_token
        except Exception:
            pass
        return None

    @classmethod
    def _request_new_token(cls) -> str:
        """Request new token from KIS API, save to DB, and return."""
        from datetime import timedelta
        url = f"{Config.KIS_BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json; charset=utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET
        }
        try:
            response = requests.post(url, json=body, timeout=TOKEN_REQUEST_TIMEOUT)
            response.raise_for_status()
            token_data = response.json()
            cls._access_token = token_data["access_token"]
            cls._token_expiry = datetime.now() + timedelta(hours=23)
            from repositories.settings_repo import SettingsRepo
            SettingsRepo.set("KIS_ACCESS_TOKEN", cls._access_token, "KIS API access token")
            SettingsRepo.set("KIS_TOKEN_EXPIRY", cls._token_expiry.isoformat(), "KIS API token expiry")
            logger.info("🔑 KIS Access Token issued and saved to DB.")
            return cls._access_token
        except Exception as e:
            logger.error(f"❌ Failed to get access token: {e}")
            raise

    @classmethod
    def get_access_token(cls) -> str:
        """Get access token with DB-based cache."""
        # 1. Check in-memory cache
        if cls._access_token and cls._token_expiry and datetime.now() < cls._token_expiry:
            return cls._access_token
        # 2. Check DB cache
        cached = cls._load_cached_token()
        if cached:
            return cached
        # 3. Request new token
        return cls._request_new_token()

    # ── Live account token (price quote / WebSocket only) ─────────────────────

    @classmethod
    def _load_cached_real_token(cls) -> Optional[str]:
        """Load valid live token from DB cache. Returns None if missing or expired."""
        try:
            from repositories.settings_repo import SettingsRepo
            token = SettingsRepo.get("KIS_REAL_ACCESS_TOKEN")
            expiry_str = SettingsRepo.get("KIS_REAL_TOKEN_EXPIRY")
            if token and expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
                if datetime.now() < expiry:
                    cls._real_access_token = token
                    cls._real_token_expiry = expiry
                    logger.info("📄 KIS Real Access Token loaded from DB.")
                    return cls._real_access_token
        except Exception:
            pass
        return None

    @classmethod
    def _request_new_real_token(cls) -> str:
        """Request new live account token, save to DB, and return."""
        from datetime import timedelta
        url = f"{Config.KIS_REAL_BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json; charset=utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.KIS_REAL_APP_KEY,
            "appsecret": Config.KIS_REAL_APP_SECRET,
        }
        try:
            response = requests.post(url, json=body, timeout=TOKEN_REQUEST_TIMEOUT)
            response.raise_for_status()
            token_data = response.json()
            cls._real_access_token = token_data["access_token"]
            cls._real_token_expiry = datetime.now() + timedelta(hours=23)
            from repositories.settings_repo import SettingsRepo
            SettingsRepo.set("KIS_REAL_ACCESS_TOKEN", cls._real_access_token, "KIS real account access token")
            SettingsRepo.set("KIS_REAL_TOKEN_EXPIRY", cls._real_token_expiry.isoformat(), "KIS real account token expiry")
            logger.info("🔑 KIS Real Access Token issued and saved to DB.")
            return cls._real_access_token
        except Exception as e:
            logger.error(f"❌ Failed to get real access token: {e}")
            raise

    @classmethod
    def get_real_access_token(cls) -> str:
        """Return live account token. Falls back to VTS token if has_real_credentials()=False."""
        if not Config.has_real_credentials():
            return cls.get_access_token()
        if cls._real_access_token and cls._real_token_expiry and datetime.now() < cls._real_token_expiry:
            return cls._real_access_token
        cached = cls._load_cached_real_token()
        if cached:
            return cached
        return cls._request_new_real_token()

    @classmethod
    def get_real_headers(cls, tr_id: str) -> dict:
        """Headers for price quotes (uses live credentials if configured, otherwise VTS)."""
        if Config.has_real_credentials():
            token = cls.get_real_access_token()
            return {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {token}",
                "appkey": Config.KIS_REAL_APP_KEY,
                "appsecret": Config.KIS_REAL_APP_SECRET,
                "tr_id": tr_id,
            }
        return cls.get_headers(tr_id)

    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def get_headers(cls, tr_id: str) -> dict:
        """Build common API headers."""
        token = cls.get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id
        }

    @classmethod
    def _parse_balance_response(cls, data: dict) -> dict:
        """Convert output1/output2 to holdings/summary dict, preserving ctx tokens."""
        return {
            "holdings": data.get("output1", []),
            "summary": data.get("output2", []),
            "ctx_area_fk100": data.get("ctx_area_fk100", ""),
            "ctx_area_nk100": data.get("ctx_area_nk100", ""),
        }

    @classmethod
    def _fetch_domestic_balance(cls, url: str, headers: dict, params: dict, attempt: int) -> Optional[dict]:
        """Domestic balance API GET call. Returns None on HTTP 5xx, response_data on success."""
        response = requests.get(url, headers=headers, params=params, timeout=BALANCE_REQUEST_TIMEOUT)
        if response.status_code >= 500:
            logger.warning(
                f"⏳ Balance API {response.status_code} (attempt {attempt + 1}/{MAX_BALANCE_RETRIES}). retrying..."
            )
            return None
        response.raise_for_status()
        return response.json()

    @classmethod
    def _balance_retry_loop(cls, url: str, headers: dict, params: dict) -> tuple:
        """Domestic balance retry loop. Returns (result, last_err)."""
        last_err = None
        for attempt in range(MAX_BALANCE_RETRIES):
            try:
                response_data = cls._fetch_domestic_balance(url, headers, params, attempt)
                if response_data is None:
                    last_err = f"HTTP 5xx on attempt {attempt + 1}"
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if response_data.get("rt_cd") != "0":
                    msg = response_data.get("msg1") or response_data.get("msg_cd") or "unknown"
                    last_err = f"KIS rt_cd={response_data.get('rt_cd')}, msg={msg}"
                    if attempt < MAX_BALANCE_RETRIES - 1:
                        logger.warning(
                            f"⏳ Balance business error (attempt {attempt + 1}/{MAX_BALANCE_RETRIES}): {msg}. retrying..."
                        )
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    logger.error(f"❌ Balance fetch failed after retries: {msg}")
                    break
                return cls._parse_balance_response(response_data), None
            except Exception as e:
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        return None, last_err

    @classmethod
    def get_balance(cls) -> Optional[dict]:
        """Get stock balance (domestic) — fetches ALL pages."""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        from services.market.stock_meta_service import StockMetaService
        tr_id, _ = StockMetaService.get_api_info("주식잔고조회")
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = cls.get_headers(tr_id)
        params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "N", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }

        all_holdings = []
        summary = []

        for page in range(10):  # safety limit
            result, last_err = cls._balance_retry_loop(url, headers, params)
            if result is None:
                if page == 0:
                    logger.error(f"❌ Error fetching balance after retries: {last_err}")
                    if cls._last_balance_data:
                        logger.warning("⚠️ Using last successful balance response as fallback.")
                        return cls._last_balance_data
                    return None
                break
            all_holdings.extend(result.get("holdings", []))
            if not summary:
                summary = result.get("summary", [])

            # Check continuation tokens
            ctx_fk = result.get("ctx_area_fk100", "").strip()
            ctx_nk = result.get("ctx_area_nk100", "").strip()
            if not ctx_fk and not ctx_nk:
                break
            params["CTX_AREA_FK100"] = ctx_fk
            params["CTX_AREA_NK100"] = ctx_nk
            headers["tr_cont"] = "N"  # continuation request

        combined = {"holdings": all_holdings, "summary": summary}
        cls._last_balance_data = combined
        return combined

    @classmethod
    def _parse_overseas_balance_response(cls, data: dict) -> dict:
        """Convert overseas balance output1/output2 to holdings/summary dict."""
        output1 = data.get("output1", []) or []
        output2 = data.get("output2", []) or []
        return {"holdings": output1, "summary": output2}

    @classmethod
    def _fetch_one_overseas_page(cls, tr_id: str, url: str, params: dict, page_num: int) -> Optional[dict]:
        """Single HTTP GET for one page of overseas balance. Returns parsed page dict or None on error/business-fail."""
        headers = cls.get_headers(tr_id)
        if page_num > 0:
            headers["tr_cont"] = "N"
        response = requests.get(url, headers=headers, params=params, timeout=BALANCE_REQUEST_TIMEOUT)
        if response.status_code >= 500:
            return None
        response.raise_for_status()
        data = response.json()
        if data.get("rt_cd") != "0":
            return None
        return {
            "holdings": data.get("output1", []) or [],
            "summary": data.get("output2", []) or [],
            "ctx_fk": data.get("ctx_area_fk200", "").strip(),
            "ctx_nk": data.get("ctx_area_nk200", "").strip(),
        }

    @classmethod
    def _fetch_all_pages_for_tr_id(cls, tr_id: str, url: str, base_params: dict) -> Optional[dict]:
        """Fetch all pages of overseas balance for one TR ID. Returns {"holdings": [...], "summary": [...]} or None."""
        all_holdings = []
        summary = []
        params = dict(base_params)
        for page in range(10):  # safety limit
            page_data = cls._fetch_one_overseas_page(tr_id, url, params, page)
            if page_data is None:
                break
            all_holdings.extend(page_data["holdings"])
            if not summary:
                summary = page_data["summary"]
            if not page_data["ctx_fk"] and not page_data["ctx_nk"]:
                break
            params["CTX_AREA_FK200"] = page_data["ctx_fk"]
            params["CTX_AREA_NK200"] = page_data["ctx_nk"]
        return {"holdings": all_holdings, "summary": summary} if all_holdings else None

    @classmethod
    def get_overseas_balance(cls) -> Optional[dict]:
        """Get overseas stock balance - all exchanges (NYSE/NASD/AMEX). Fetches ALL pages. Returns None on failure."""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        from services.market.stock_meta_service import StockMetaService
        tr_id1, url_path = StockMetaService.get_api_info("해외주식_잔고조회")
        tr_id2, _ = StockMetaService.get_api_info("해외주식_잔고조회_종합")
        tr_ids = [t for t in [tr_id1, tr_id2] if t]
        if not tr_ids:
            logger.error("❌ 해외 잔고조회 TR ID를 DB에서 가져올 수 없습니다.")
            return None
        url = f"{Config.KIS_BASE_URL}{url_path}"
        base_params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": "", "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
        }

        for tr_id in tr_ids:
            try:
                result = cls._fetch_all_pages_for_tr_id(tr_id, url, base_params)
                if result:
                    cls._last_overseas_balance_data = result
                    return result
            except Exception as e:
                logger.warning(f"⚠️ Overseas balance tr_id={tr_id} failed: {e}")

        if cls._last_overseas_balance_data:
            logger.warning("⚠️ All overseas balance attempts failed. Using last cached result as fallback (stale).")
            stale_copy = dict(cls._last_overseas_balance_data)
            stale_copy["_stale"] = True
            return stale_copy
        logger.error("❌ All overseas balance attempts failed with no cached fallback.")
        return None

    @classmethod
    def _fetch_overseas_available_cash_raw(cls, tr_id: str, cano: str, acnt_prdt_cd: str, item_cd: str, excg_cd: str = "NASD") -> Optional[dict]:
        """Call overseas available cash API and return output dict. Returns None on error."""
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-psamount"
        params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": excg_cd, "OVRS_CRCY_CD": "USD",
            "OVRS_ORD_UNPR": "0", "ITEM_CD": item_cd
        }
        headers = cls.get_headers(tr_id)
        response = requests.get(url, headers=headers, params=params, timeout=BALANCE_REQUEST_TIMEOUT)
        if response.status_code >= 500:
            logger.warning(f"⚠️ Overseas available cash API HTTP {response.status_code}")
            return None
        response.raise_for_status()
        response_data = response.json()
        if response_data.get("rt_cd") != "0":
            msg = response_data.get("msg1", "")
            msg_cd = response_data.get("msg_cd", "")
            logger.warning(f"⚠️ Overseas available cash API failed: {msg} (msg_cd: {msg_cd})")
            return None
        return response_data.get("output", {}) or None

    @classmethod
    def get_overseas_available_cash(cls) -> Optional[float]:
        """Get overseas actual cash balance - includes orderable foreign currency + T+2 settlement."""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        from services.market.stock_meta_service import StockMetaService
        tr_id, _ = StockMetaService.get_api_info("해외주식_가용현금조회")
        if not tr_id:
            logger.error("❌ 해외주식_가용현금조회 TR ID를 DB에서 가져올 수 없습니다.")
            return None

        # API requires a ticker/exchange; use first overseas holding (ticker-independent result)
        overseas_balance = cls.get_overseas_balance()
        if not overseas_balance or not overseas_balance.get("holdings"):
            logger.warning("⚠️ No overseas holdings — skipping USD available cash query.")
            return None
        first = overseas_balance["holdings"][0]
        item_cd = first.get("ovrs_pdno") or ""
        excg_cd = first.get("ovrs_excg_cd") or ""
        if not item_cd or not excg_cd:
            logger.warning("⚠️ First overseas holding has no ticker/exchange — skipping USD available cash query.")
            return None

        try:
            output = cls._fetch_overseas_available_cash_raw(tr_id, cano, acnt_prdt_cd, item_cd, excg_cd)
            if output:
                # Actual cash incl. T+2 settlement: prefer frcr_drwg_psbl_amt2 (D+2), fallback to ord_psbl_frcr_amt
                frcr_drwg2 = float(output.get("frcr_drwg_psbl_amt2") or 0)
                ord_psbl = float(output.get("ord_psbl_frcr_amt") or 0)
                available_usd = frcr_drwg2 if frcr_drwg2 > 0 else ord_psbl
                if available_usd > 0:
                    logger.info(f"✅ USD actual cash query succeeded: ${available_usd:,.2f} (incl. T+2)")
                    from services.config.settings_service import SettingsService
                    SettingsService.set_setting("PORTFOLIO_USD_CASH_BALANCE", str(available_usd))
                    return available_usd
        except Exception as e:
            logger.error(f"❌ Failed to get overseas available cash: {e}")
        return None

    @classmethod
    def _handle_rate_limit_retry(cls, response: requests.Response, attempt: int, max_retries: int, log_tag: str) -> bool:
        """Wait and return True if TPS rate limited (needs retry), False otherwise."""
        if cls._is_rate_limited_response(response) and attempt < max_retries - 1:
            wait_sec = 1.2 * (attempt + 1)
            logger.warning(f"⏳ {log_tag} TPS limit hit. retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
            time.sleep(wait_sec)
            return True
        return False

    @classmethod
    def _handle_order_response(cls, data: dict, attempt: int, max_retries: int, log_tag: str) -> Optional[dict]:
        """Handle order response rt_cd. None=retry, dict=final result."""
        if data.get("rt_cd") == "0":
            return {"status": "success", "data": data.get("output") or data.get("output1") or {}}
        msg = data.get("msg1") or data.get("msg_cd") or "unknown"
        if data.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD and attempt < max_retries - 1:
            wait_sec = 1.2 * (attempt + 1)
            logger.warning(f"⏳ {log_tag} TPS limit (rt_cd). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
            time.sleep(wait_sec)
            return None
        logger.error(f"❌ {log_tag} failed: {msg} (rt_cd: {data.get('rt_cd')})")
        return {"status": "failed", "msg": msg}

    @classmethod
    def _post_order_with_retry(cls, url: str, headers: dict, body: dict, log_tag: str, max_retries: int = 3) -> dict:
        """POST order request with TPS rate limit retry logic."""
        for attempt in range(max_retries):
            try:
                cls._throttle_request()
                response = requests.post(url, headers=headers, data=json.dumps(body), timeout=ORDER_REQUEST_TIMEOUT)
                if cls._handle_rate_limit_retry(response, attempt, max_retries, log_tag):
                    continue
                if response.status_code >= 500:
                    logger.error(f"❌ {log_tag} HTTP {response.status_code}. Body: {response.text[:200]}")
                    response.raise_for_status()
                response.raise_for_status()
                result = cls._handle_order_response(response.json(), attempt, max_retries, log_tag)
                if result is None:
                    continue
                return result
            except requests.exceptions.HTTPError as e:
                error_msg = str(e)
                if hasattr(e, "response") and cls._is_rate_limited_response(e.response) and attempt < max_retries - 1:
                    wait_sec = 1.2 * (attempt + 1)
                    logger.warning(f"⏳ {log_tag} TPS limit (HTTP). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                    time.sleep(wait_sec)
                    continue
                logger.error(f"❌ Error sending {log_tag}: {error_msg}")
                return {"status": "error", "msg": error_msg}
            except Exception as e:
                logger.error(f"❌ Error sending {log_tag}: {e}")
                return {"status": "error", "msg": str(e)}
        logger.error(f"❌ {log_tag} failed after {max_retries} retries")
        return {"status": "error", "msg": f"Failed after {max_retries} retries due to rate limit"}

    @classmethod
    def _send_domestic_order(cls, ticker: str, quantity: int, tr_id: str, ord_dvsn: str, ord_price: str, log_tag: str) -> dict:
        """Execute domestic stock order (TPS rate limit compliant)."""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return {"status": "error", "msg": "Invalid KIS_ACCOUNT_NO format"}
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": ticker, "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity), "ORD_UNPR": ord_price,
        }
        result = cls._post_order_with_retry(url, cls.get_headers(tr_id), body, log_tag)
        if result.get("status") == "success":
            logger.info(f"✅ {log_tag} success! {ticker} {quantity}qty")
        return result

    @classmethod
    def send_order(cls, ticker: str, quantity: int, price: int = 0, order_type: str = "buy") -> dict:
        """Domestic stock order (buy/sell)."""
        if Config.DEV_MODE:
            logger.info(f"[DEV MODE] Live order blocked → {order_type.upper()} {ticker} {quantity}qty @ {price}")
            return {"status": "dev_blocked", "msg": "DEV MODE: Live order blocked"}
        from services.market.stock_meta_service import StockMetaService
        api_name = "주식주문_매수" if order_type == "buy" else "주식주문_매도"
        tr_id, _ = StockMetaService.get_api_info(api_name)

        ord_dvsn = "00" if price > 0 else "01"
        ord_price = str(price) if price > 0 else "0"
        return cls._send_domestic_order(
            ticker=ticker,
            quantity=quantity,
            tr_id=tr_id,
            ord_dvsn=ord_dvsn,
            ord_price=ord_price,
            log_tag=f"Order [{order_type.upper()}]"
        )

    @classmethod
    def send_after_hours_order(cls, ticker: str, quantity: int, order_type: str = "buy", ord_dvsn: Optional[str] = None) -> dict:
        """KR after-hours order (live trading only).
        - Allowed only when Config.KIS_ENABLE_AFTER_HOURS_ORDER=True
        - Blocked in paper trading (VTS)
        """
        if Config.DEV_MODE:
            logger.info(f"[DEV MODE] After-hours order blocked → {order_type.upper()} {ticker} {quantity}qty")
            return {"status": "dev_blocked", "msg": "DEV MODE: Live order blocked"}
        if Config.KIS_IS_VTS:
            return {"status": "failed", "msg": "After-hours orders are not supported in paper trading (VTS)."}
        if not Config.KIS_ENABLE_AFTER_HOURS_ORDER:
            return {"status": "failed", "msg": "After-hours orders are disabled. (KIS_ENABLE_AFTER_HOURS_ORDER=false)"}
        if not is_kr(ticker):
            return {"status": "failed", "msg": "After-hours orders only support domestic stock tickers."}
        if not MarketHourService.is_kr_after_hours_open():
            return {"status": "failed", "msg": "Not within KR after-hours order window."}

        from services.market.stock_meta_service import StockMetaService
        api_name = "주식주문_매수" if order_type == "buy" else "주식주문_매도"
        tr_id, _ = StockMetaService.get_api_info(api_name)
        ord_dvsn_final = (ord_dvsn or Config.KIS_AFTER_HOURS_ORD_DVSN or "81").strip()

        return cls._send_domestic_order(
            ticker=ticker,
            quantity=quantity,
            tr_id=tr_id,
            ord_dvsn=ord_dvsn_final,
            ord_price="0",
            log_tag=f"After-hours [{order_type.upper()}]"
        )

    @classmethod
    def send_after_hours_buy(cls, ticker: str, quantity: int, ord_dvsn: Optional[str] = None) -> dict:
        """KR after-hours buy order (live trading + config enabled only)."""
        return cls.send_after_hours_order(ticker=ticker, quantity=quantity, order_type="buy", ord_dvsn=ord_dvsn)

    @classmethod
    def send_after_hours_sell(cls, ticker: str, quantity: int, ord_dvsn: Optional[str] = None) -> dict:
        """KR after-hours sell order (live trading + config enabled only)."""
        return cls.send_after_hours_order(ticker=ticker, quantity=quantity, order_type="sell", ord_dvsn=ord_dvsn)

    @classmethod
    def send_overseas_order(cls, ticker: str, quantity: int, price: float = 0, order_type: str = "buy", market: str = "NASD") -> dict:
        """Overseas stock order (US market, TPS rate limit compliant)."""
        if Config.DEV_MODE:
            logger.info(f"[DEV MODE] Overseas order blocked → {order_type.upper()} {ticker} {quantity}qty @ {price}")
            return {"status": "dev_blocked", "msg": "DEV MODE: Live order blocked"}
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return {"status": "error", "msg": "Invalid KIS_ACCOUNT_NO format"}
        if price <= 0:
            return {"status": "error", "msg": "Overseas stock orders require a limit price."}
        from services.market.stock_meta_service import StockMetaService
        api_name = "해외주식_미국매수" if order_type == "buy" else "해외주식_미국매도"
        tr_id, _ = StockMetaService.get_api_info(api_name)
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
        body = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": market, "PDNO": ticker,
            "ORD_QTY": str(quantity), "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",
        }
        log_tag = f"Overseas Order [{order_type.upper()}]"
        result = cls._post_order_with_retry(url, cls.get_headers(tr_id), body, log_tag)
        if result.get("status") == "success":
            logger.info(f"✅ Overseas Order Success! [{order_type.upper()}] {ticker} {quantity}qty @ ${price}")
        return result

    # --- Extended methods (for modular integration) ---
    @classmethod
    def get_financials(cls, ticker: str, meta: Optional[dict] = None) -> dict:
        """Get domestic stock financials/fundamentals via KisFetcher. meta is dict or KisFinancialsMeta DTO."""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        meta_dict = meta.model_dump() if (meta is not None and hasattr(meta, "model_dump")) else meta
        return KisFetcher.fetch_domestic_price(token, ticker, meta=meta_dict)

    @classmethod
    def get_overseas_financials(cls, ticker: str, market: str = "NASD", meta: Optional[dict] = None) -> dict:
        """Get overseas stock financials/fundamentals via KisFetcher. meta is dict or KisFinancialsMeta DTO."""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        meta_dict = meta.model_dump() if (meta is not None and hasattr(meta, "model_dump")) else meta
        return KisFetcher.fetch_overseas_price(token, ticker, meta=meta_dict)
    @classmethod
    def get_overseas_ranking(cls, excd: str = "NAS") -> dict:
        """Get overseas stock market cap ranking via KisFetcher."""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        return KisFetcher.fetch_overseas_ranking(token, excd=excd)

    @classmethod
    def _paginate_trade_history(cls, url: str, tr_id: str, params: dict, output_key: str, ctx_suffix: str, description: str) -> list:
        """공통 페이지루프 헬퍼 — tr_cont 기반 다음 페이지 처리."""
        fk_resp_key = f"ctx_area_fk{ctx_suffix}"
        nk_resp_key = f"ctx_area_nk{ctx_suffix}"
        fk_param_key = f"CTX_AREA_FK{ctx_suffix}"
        nk_param_key = f"CTX_AREA_NK{ctx_suffix}"

        all_records = []
        try:
            for page in range(10):
                headers = cls.get_headers(tr_id)
                if page > 0:
                    headers["tr_cont"] = "N"
                cls._throttle_request()
                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code != 200:
                    break
                data = response.json()
                if data.get("rt_cd") != "0":
                    break
                all_records.extend(data.get(output_key, []))
                ctx_fk = data.get(fk_resp_key, "").strip()
                ctx_nk = data.get(nk_resp_key, "").strip()
                if not ctx_fk and not ctx_nk:
                    break
                params[fk_param_key] = ctx_fk
                params[nk_param_key] = ctx_nk
        except Exception as e:
            logger.error(f"❌ Request failed for {description} trade history: {e}")
        if not all_records:
            logger.warning(f"⚠️ No {description} trade history records found")
        return all_records

    @classmethod
    def get_domestic_trade_history(cls, start_date: str, end_date: str) -> list:
        """Fetch domestic trade history from KIS API (dates in YYYYMMDD format)."""
        from services.market.stock_meta_service import StockMetaService
        tr_id, path = StockMetaService.get_api_info("국내주식_체결조회")
        url = f"{Config.KIS_BASE_URL}{path}"
        account_prefix, account_suffix = cls._get_account_parts()
        params = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        return cls._paginate_trade_history(url, tr_id, params, output_key="output1", ctx_suffix="100", description="domestic")

    @classmethod
    def get_overseas_trade_history(cls, start_date: str, end_date: str) -> list:
        """Fetch overseas trade history from KIS API (dates in YYYYMMDD format)."""
        from services.market.stock_meta_service import StockMetaService
        tr_id, path = StockMetaService.get_api_info("해외주식_체결조회")
        url = f"{Config.KIS_BASE_URL}{path}"
        account_prefix, account_suffix = cls._get_account_parts()
        params = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "OVRS_EXCG_CD": "NASD",  # Mostly NASDAQ
            "PDNO": "%",
            "ORD_STRT_DT": start_date,
            "ORD_END_DT": end_date,
            "SLL_BUY_DVSN": "00",
            "CCLD_NCCS_DVSN": "00",
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "SORT_SQN": "",
            "CTX_AREA_NK200": "",
            "CTX_AREA_FK200": ""
        }
        return cls._paginate_trade_history(url, tr_id, params, output_key="output", ctx_suffix="200", description="overseas")

    # ── Unfilled Order Query ─────────────────────────────────────────────────

    @classmethod
    def get_unfilled_orders_kr(cls) -> "UnfilledOrdersResult":
        """국내 미체결 주문 조회. CCLD_DVSN=02(미체결)로 당일 미체결 조회."""
        from services.market.stock_meta_service import StockMetaService
        from models.schemas import UnfilledOrder, UnfilledOrdersResult

        tr_id, path = StockMetaService.get_api_info("국내주식_미체결조회")
        if not tr_id or not path:
            return UnfilledOrdersResult(error="국내주식_미체결조회 TR 정보 없음")

        url = f"{Config.KIS_BASE_URL}{path}"
        cano, acnt_prdt_cd = cls._get_account_parts()
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": today, "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00",
            "PDNO": "", "CCLD_DVSN": "02",
            "ORD_GNO_BRNO": "", "ODNO": "",
            "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
            "EXCG_ID_DVSN_CD": "KRX",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        return cls._fetch_unfilled_orders(url, tr_id, params, "output1", "100", "KR")

    @classmethod
    def get_unfilled_orders_us(cls) -> "UnfilledOrdersResult":
        """해외 미체결 주문 조회."""
        from services.market.stock_meta_service import StockMetaService
        from models.schemas import UnfilledOrder, UnfilledOrdersResult

        tr_id, path = StockMetaService.get_api_info("해외주식_미체결조회")
        if not tr_id or not path:
            return UnfilledOrdersResult(error="해외주식_미체결조회 TR 정보 없음")

        url = f"{Config.KIS_BASE_URL}{path}"
        cano, acnt_prdt_cd = cls._get_account_parts()
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": "NASD", "PDNO": "%",
            "ORD_STRT_DT": today, "ORD_END_DT": today,
            "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "01",
            "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
            "SORT_SQN": "",
            "CTX_AREA_NK200": "", "CTX_AREA_FK200": "",
        }
        return cls._fetch_unfilled_orders(url, tr_id, params, "output", "200", "US")

    @classmethod
    def _fetch_unfilled_orders(
        cls, url: str, tr_id: str, params: dict,
        output_key: str, ctx_suffix: str, market: str,
    ) -> "UnfilledOrdersResult":
        """미체결 조회 공통 헬퍼. KR/US 모두 동일 패턴."""
        from models.schemas import UnfilledOrder, UnfilledOrdersResult

        orders: list[UnfilledOrder] = []
        try:
            raw_records = cls._paginate_trade_history(
                url, tr_id, params, output_key, ctx_suffix,
                description=f"{market} unfilled orders",
            )
            for item in raw_records:
                ticker = str(
                    item.get("pdno") or item.get("ovrs_pdno") or ""
                ).strip().upper()
                if not ticker:
                    continue
                rmn = int(float(item.get("rmn_qty") or item.get("nccs_qty") or 0))
                if rmn <= 0:
                    continue
                sll_buy = item.get("sll_buy_dvsn_cd", "")
                order_type = "sell" if sll_buy == "01" else "buy"
                orders.append(UnfilledOrder(
                    ticker=ticker,
                    order_type=order_type,
                    order_qty=int(float(item.get("ord_qty") or item.get("ft_ord_qty") or 0)),
                    filled_qty=int(float(item.get("tot_ccld_qty") or item.get("ft_ccld_qty") or 0)),
                    remaining_qty=rmn,
                    order_price=float(item.get("ord_unpr") or item.get("ft_ord_unpr3") or 0),
                    order_date=item.get("ord_dt", ""),
                    order_time=item.get("ord_tmd", ""),
                ))
            logger.info(f"📋 [{market}] 미체결 조회: {len(orders)}건")
        except Exception as e:
            logger.error(f"❌ [{market}] 미체결 조회 실패: {e}")
            return UnfilledOrdersResult(error=f"{market} 미체결 조회 실패: {str(e)}")
        return UnfilledOrdersResult(orders=orders)
