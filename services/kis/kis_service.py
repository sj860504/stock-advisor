import requests
import json
import time
import os
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
        """Load valid token from file cache. Returns None if missing or expired."""
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_token.json')
        if not os.path.exists(token_cache_path):
            return None
        try:
            with open(token_cache_path, "r") as f:
                token_cache = json.load(f)
            expiry = datetime.fromisoformat(token_cache["expiry"])
            if datetime.now() < expiry:
                cls._access_token = token_cache["token"]
                cls._token_expiry = expiry
                logger.info("📄 KIS Access Token loaded from session file.")
                return cls._access_token
        except Exception:
            pass
        return None

    @classmethod
    def _request_new_token(cls) -> str:
        """Request new token from KIS API, save to file, and return."""
        from datetime import timedelta
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_token.json')
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
            cls._token_expiry = datetime.now() + timedelta(hours=2)
            os.makedirs(os.path.dirname(token_cache_path), exist_ok=True)
            with open(token_cache_path, 'w') as f:
                json.dump({"token": cls._access_token, "expiry": cls._token_expiry.isoformat()}, f)
            logger.info("🔑 KIS Access Token issued and saved to file.")
            return cls._access_token
        except Exception as e:
            logger.error(f"❌ Failed to get access token: {e}")
            raise

    @classmethod
    def get_access_token(cls) -> str:
        """Get access token with file-based cache."""
        # 1. Check in-memory cache
        if cls._access_token and cls._token_expiry and datetime.now() < cls._token_expiry:
            return cls._access_token
        # 2. Check file cache
        cached = cls._load_cached_token()
        if cached:
            return cached
        # 3. Request new token
        return cls._request_new_token()

    # ── Live account token (price quote / WebSocket only) ─────────────────────

    @classmethod
    def _load_cached_real_token(cls) -> Optional[str]:
        """Load valid live token from file cache. Returns None if missing or expired."""
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_real_token.json')
        if not os.path.exists(token_cache_path):
            return None
        try:
            with open(token_cache_path, "r") as f:
                token_cache = json.load(f)
            expiry = datetime.fromisoformat(token_cache["expiry"])
            if datetime.now() < expiry:
                cls._real_access_token = token_cache["token"]
                cls._real_token_expiry = expiry
                logger.info("📄 KIS Real Access Token loaded from session file.")
                return cls._real_access_token
        except Exception:
            pass
        return None

    @classmethod
    def _request_new_real_token(cls) -> str:
        """Request new live account token, save to file, and return."""
        from datetime import timedelta
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_real_token.json')
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
            cls._real_token_expiry = datetime.now() + timedelta(hours=2)
            os.makedirs(os.path.dirname(token_cache_path), exist_ok=True)
            with open(token_cache_path, 'w') as f:
                json.dump({"token": cls._real_access_token, "expiry": cls._real_token_expiry.isoformat()}, f)
            logger.info("🔑 KIS Real Access Token issued and saved to file.")
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
        """Convert output1/output2 to holdings/summary dict."""
        return {
            "holdings": data.get("output1", []),
            "summary": data.get("output2", [])
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
        """Get stock balance (domestic paper trading)."""
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
        result, last_err = cls._balance_retry_loop(url, headers, params)
        if result is not None:
            cls._last_balance_data = result
            return result
        logger.error(f"❌ Error fetching balance after retries: {last_err}")
        if cls._last_balance_data:
            logger.warning("⚠️ Using last successful balance response as fallback.")
            return cls._last_balance_data
        return None

    @classmethod
    def _parse_overseas_balance_response(cls, data: dict) -> dict:
        """Convert overseas balance output1/output2 to holdings/summary dict."""
        output1 = data.get("output1", []) or []
        output2 = data.get("output2", []) or []
        return {"holdings": output1, "summary": output2}

    @classmethod
    def get_overseas_balance(cls) -> Optional[dict]:
        """Get overseas stock balance - all exchanges (NYSE/NASD/AMEX). Returns None on failure."""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
        tr_ids = ["VTTS3012R", "TTTS3012R", "VTTT3012R", "TTTT3012R"]
        params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": "", "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""
        }

        for tr_id in tr_ids:
            try:
                headers = cls.get_headers(tr_id)
                response = requests.get(url, headers=headers, params=params, timeout=BALANCE_REQUEST_TIMEOUT)
                if response.status_code >= 500:
                    continue
                response.raise_for_status()
                response_data = response.json()
                if response_data.get("rt_cd") != "0":
                    continue
                return cls._parse_overseas_balance_response(response_data)
            except Exception:
                continue
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

        tr_id = "VTTS3007R" if Config.KIS_IS_VTS else "TTTS3007R"

        # Extract item_cd/exchange code from holdings (API requires a ticker for cash query)
        # Default to AAPL/NASD if no holdings (cash balance is ticker-independent)
        overseas_balance = cls.get_overseas_balance()
        item_cd = "AAPL"
        excg_cd = "NASD"
        if overseas_balance and overseas_balance.get("holdings"):
            first = overseas_balance["holdings"][0]
            item_cd = first.get("ovrs_pdno") or item_cd
            excg_cd = first.get("ovrs_excg_cd") or excg_cd

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
    def get_domestic_trade_history(cls, start_date: str, end_date: str) -> list:
        """Fetch domestic trade history from KIS API (dates in YYYYMMDD format)."""
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        tr_id = "VTTC8001R" if Config.KIS_IS_VTS else "TTTC8001R"
        headers = cls.get_headers(tr_id)
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
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("rt_cd") == "0":
                    return data.get("output1", [])
            logger.error(f"❌ KIS Domestic History API Error: {response.text}")
        except Exception as e:
            logger.error(f"❌ Request failed for domestic trade history: {e}")
        return []

    @classmethod
    def get_overseas_trade_history(cls, start_date: str, end_date: str) -> list:
        """Fetch overseas trade history from KIS API (dates in YYYYMMDD format)."""
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-ccnl"
        tr_id = "VTTT3001R" if Config.KIS_IS_VTS else "JTTT3001R"
        headers = cls.get_headers(tr_id)
        account_prefix, account_suffix = cls._get_account_parts()
        
        params = {
            "CANO": account_prefix,
            "ACNT_PRDT_CD": account_suffix,
            "OVRS_EXCG_CD": "NASD", # Mostly NASDAQ
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
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("rt_cd") == "0":
                    return data.get("output", [])
            logger.error(f"❌ KIS Overseas History API Error: {response.text}")
        except Exception as e:
            logger.error(f"❌ Request failed for overseas trade history: {e}")
        return []
