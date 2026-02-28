import requests
import json
import time
import os
import threading
from datetime import datetime
from config import Config
from services.market.market_hour_service import MarketHourService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("kis_service")

# KIS API 상수
KIS_RATE_LIMIT_MSG_CD = "EGW00201"
TOKEN_REQUEST_TIMEOUT = 5
BALANCE_REQUEST_TIMEOUT = 8
ORDER_REQUEST_TIMEOUT = 10
MAX_BALANCE_RETRIES = 3


class KisService:
    """
    한국투자증권 API 연동 서비스
    """
    _access_token = None
    _token_expiry = None
    _last_balance_data = None
    _req_lock = threading.Lock()
    _last_req_ts = 0.0
    _min_req_interval = 0.55  # VTS 기준 약 2TPS 제한 대응
    
    @classmethod
    def _throttle_request(cls):
        """요청 간격 제한 (초당 거래건수 준수)"""
        with cls._req_lock:
            now = time.time()
            elapsed = now - cls._last_req_ts
            if elapsed < cls._min_req_interval:
                sleep_time = cls._min_req_interval - elapsed
                time.sleep(sleep_time)
            cls._last_req_ts = time.time()
    
    @classmethod
    def _is_rate_limited_response(cls, response: requests.Response) -> bool:
        """초당 거래건수 제한 응답인지 확인"""
        if response.status_code in (429, 500):
            try:
                body = response.json()
                if body.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD or "초당 거래건수" in (body.get("msg1") or ""):
                    return True
            except Exception:
                pass
        return False

    @classmethod
    def _get_account_parts(cls):
        """
        계좌번호를 KIS 파라미터 형식으로 분리
        - 입력 허용: 50162391-01 / 5016239101 / 50162391
        - 반환: (CANO(8), ACNT_PRDT_CD(2))
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
    def get_access_token(cls):
        """접근 토큰 발급 및 갱신 (파일 기반 캐시 적용)"""
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_token.json')
        
        # 1. 메모리 캐시 확인
        if cls._access_token and cls._token_expiry and datetime.now() < cls._token_expiry:
            return cls._access_token
            
        if os.path.exists(token_cache_path):
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
            from datetime import timedelta
            cls._token_expiry = datetime.now() + timedelta(hours=2)
            
            # 파일 캐시 저장
            os.makedirs(os.path.dirname(token_cache_path), exist_ok=True)
            with open(token_cache_path, 'w') as f:
                json.dump({
                    "token": cls._access_token,
                    "expiry": cls._token_expiry.isoformat()
                }, f)
                
            logger.info("🔑 KIS Access Token issued and saved to file.")
            return cls._access_token
        except Exception as e:
            logger.error(f"❌ Failed to get access token: {e}")
            raise

    @classmethod
    def get_headers(cls, tr_id: str):
        """API 공통 헤더 생성"""
        token = cls.get_access_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET,
            "tr_id": tr_id
        }

    @classmethod
    def get_balance(cls):
        """주식 잔고 조회 (국내 모의투자 기준)"""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        from services.market.stock_meta_service import StockMetaService
        tr_id, _ = StockMetaService.get_api_info("주식잔고조회")
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = cls.get_headers(tr_id)
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        last_err = None
        for attempt in range(MAX_BALANCE_RETRIES):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=BALANCE_REQUEST_TIMEOUT)
                if response.status_code >= 500:
                    last_err = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        f"⏳ Balance API {response.status_code} (attempt {attempt + 1}/{MAX_BALANCE_RETRIES}). retrying..."
                    )
                    time.sleep(1.2 * (attempt + 1))
                    continue
                response.raise_for_status()
                response_data = response.json()
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
                result = {
                    "holdings": response_data.get("output1", []),
                    "summary": response_data.get("output2", [])
                }
                cls._last_balance_data = result
                return result
            except Exception as e:
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        
        logger.error(f"❌ Error fetching balance after retries: {last_err}")
        if cls._last_balance_data:
            logger.warning("⚠️ Using last successful balance response as fallback.")
            return cls._last_balance_data
        return None

    @classmethod
    def get_overseas_balance(cls):
        """해외 주식 잔고 조회 (실패 시 None)"""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
        tr_ids = ["VTTS3012R", "TTTS3012R", "VTTT3012R", "TTTT3012R"]
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
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
                output1 = response_data.get("output1", []) or []
                output2 = response_data.get("output2", []) or []
                return {"holdings": output1, "summary": output2}
            except Exception:
                continue
        return None

    @classmethod
    def get_overseas_available_cash(cls):
        """해외 주식 가용 현금 조회 - 매수가능금액 조회 API 사용 (VTTS3007R)"""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return None

        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-psamount"
        tr_id = "VTTS3007R" if Config.KIS_IS_VTS else "TTTS3007R"
        
        # ITEM_CD 파라미터 필요 - 해외 잔고에서 첫 번째 종목 코드 사용
        overseas_balance = cls.get_overseas_balance()
        item_cd = None
        if overseas_balance and overseas_balance.get("holdings") and len(overseas_balance["holdings"]) > 0:
            item_cd = overseas_balance["holdings"][0].get("ovrs_pdno")
        
        if not item_cd:
            logger.warning("⚠️ Cannot get USD available cash: no overseas holdings found")
            return None
        
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": "NASD",
            "OVRS_CRCY_CD": "USD",
            "OVRS_ORD_UNPR": "0",
            "ITEM_CD": item_cd
        }

        try:
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
            output = response_data.get("output", {})
            if output:
                # 주문가능외화금액 (ord_psbl_frcr_amt) 사용
                available_usd = float(output.get("ord_psbl_frcr_amt") or 0)
                if available_usd > 0:
                    logger.info(f"✅ USD 가용 현금 조회 성공: ${available_usd:,.2f}")
                    # 데이터베이스에 저장
                    from services.config.settings_service import SettingsService
                    SettingsService.set_setting("PORTFOLIO_USD_CASH_BALANCE", str(available_usd))
                    return available_usd
        except Exception as e:
            logger.error(f"❌ Failed to get overseas available cash: {e}")
        return None

    @classmethod
    def _send_domestic_order(cls, ticker: str, quantity: int, tr_id: str, ord_dvsn: str, ord_price: str, log_tag: str):
        """국내주식 주문 공통 실행 (초당 거래건수 제한 준수)"""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return {"status": "error", "msg": "Invalid KIS_ACCOUNT_NO format"}

        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = cls.get_headers(tr_id)

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": ord_price
        }

        # 초당 거래건수 제한 준수를 위한 재시도 로직
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Throttle 적용
                cls._throttle_request()
                
                response = requests.post(url, headers=headers, data=json.dumps(body), timeout=ORDER_REQUEST_TIMEOUT)
                if cls._is_rate_limited_response(response):
                    wait_sec = 1.2 * (attempt + 1)
                    logger.warning(f"⏳ {log_tag} TPS limit hit. retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                    time.sleep(wait_sec)
                    continue
                if response.status_code >= 500:
                    try:
                        error_body = response.text
                        logger.error(f"❌ {log_tag} HTTP {response.status_code} Error. Response body: {error_body}")
                    except Exception:
                        pass
                    response.raise_for_status()
                response.raise_for_status()
                data = response.json()
                if data["rt_cd"] != "0":
                    msg = data.get("msg1") or data.get("msg_cd") or "unknown"
                    if data.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD and attempt < max_retries - 1:
                        wait_sec = 1.2 * (attempt + 1)
                        logger.warning(f"⏳ {log_tag} TPS limit (rt_cd). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                        time.sleep(wait_sec)
                        continue
                    logger.error(f"❌ {log_tag} failed: {msg} (rt_cd: {data.get('rt_cd')})")
                    return {"status": "failed", "msg": msg}

                logger.info(f"✅ {log_tag} success! {ticker} {quantity}qty")
                return {"status": "success", "data": data.get('output', {})}
                
            except requests.exceptions.HTTPError as e:
                error_msg = str(e)
                try:
                    error_body = e.response.text if hasattr(e, 'response') else ""
                    # 초당 거래건수 에러인 경우 재시도
                    if hasattr(e, 'response') and cls._is_rate_limited_response(e.response) and attempt < max_retries - 1:
                        wait_sec = 1.2 * (attempt + 1)
                        logger.warning(f"⏳ {log_tag} TPS limit (HTTP). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                        time.sleep(wait_sec)
                        continue
                    logger.error(f"❌ Error sending {log_tag}: {error_msg} | Body: {error_body}")
                except:
                    logger.error(f"❌ Error sending {log_tag}: {error_msg}")
                return {"status": "error", "msg": error_msg}
            except Exception as e:
                logger.error(f"❌ Error sending {log_tag}: {e}")
                return {"status": "error", "msg": str(e)}
        
        # 모든 재시도 실패
        logger.error(f"❌ {log_tag} failed after {max_retries} retries")
        return {"status": "error", "msg": f"Failed after {max_retries} retries due to rate limit"}

    @classmethod
    def send_order(cls, ticker: str, quantity: int, price: int = 0, order_type: str = "buy"):
        """국내 주식 주문 (매수/매도)"""
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
    def send_after_hours_order(cls, ticker: str, quantity: int, order_type: str = "buy", ord_dvsn: str = None):
        """
        한국 사후장 주문(실전 전용)
        - Config.KIS_ENABLE_AFTER_HOURS_ORDER=True 일 때만 허용
        - 모의투자(VTS)에서는 차단
        """
        if Config.KIS_IS_VTS:
            return {"status": "failed", "msg": "사후장 주문은 모의투자(VTS)에서 지원하지 않습니다."}
        if not Config.KIS_ENABLE_AFTER_HOURS_ORDER:
            return {"status": "failed", "msg": "사후장 주문이 비활성화되어 있습니다. (KIS_ENABLE_AFTER_HOURS_ORDER=false)"}
        if not is_kr(ticker):
            return {"status": "failed", "msg": "사후장 주문은 국내 주식 티커만 지원합니다."}
        if not MarketHourService.is_kr_after_hours_open():
            return {"status": "failed", "msg": "한국 사후장 주문 가능 시간이 아닙니다."}

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
    def send_after_hours_buy(cls, ticker: str, quantity: int, ord_dvsn: str = None):
        """한국 사후장 매수 주문 (실전+설정 활성화 전용)"""
        return cls.send_after_hours_order(ticker=ticker, quantity=quantity, order_type="buy", ord_dvsn=ord_dvsn)

    @classmethod
    def send_after_hours_sell(cls, ticker: str, quantity: int, ord_dvsn: str = None):
        """한국 사후장 매도 주문 (실전+설정 활성화 전용)"""
        return cls.send_after_hours_order(ticker=ticker, quantity=quantity, order_type="sell", ord_dvsn=ord_dvsn)

    @classmethod
    def send_overseas_order(cls, ticker: str, quantity: int, price: float = 0, order_type: str = "buy", market: str = "NASD"):
        """해외 주식 주문 (미국 기준, 초당 거래건수 제한 준수)"""
        cano, acnt_prdt_cd = cls._get_account_parts()
        if not cano:
            return {"status": "error", "msg": "Invalid KIS_ACCOUNT_NO format"}

        from services.market.stock_meta_service import StockMetaService
        api_name = "해외주식_미국매수" if order_type == "buy" else "해외주식_미국매도"
        tr_id, _ = StockMetaService.get_api_info(api_name)
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
        headers = cls.get_headers(tr_id)
        
        if price <= 0:
             return {"status": "error", "msg": "해외 주식 주문 시 지정가(price)를 입력해야 합니다."}

        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": market,
            "PDNO": ticker,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        # 초당 거래건수 제한 준수를 위한 재시도 로직
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Throttle 적용
                cls._throttle_request()
                
                response = requests.post(url, headers=headers, data=json.dumps(body), timeout=ORDER_REQUEST_TIMEOUT)
                if cls._is_rate_limited_response(response):
                    wait_sec = 1.2 * (attempt + 1)
                    logger.warning(f"⏳ Overseas Order TPS limit hit. retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                    time.sleep(wait_sec)
                    continue
                response.raise_for_status()
                data = response.json()
                if data["rt_cd"] != "0":
                    if data.get("msg_cd") == KIS_RATE_LIMIT_MSG_CD and attempt < max_retries - 1:
                        wait_sec = 1.2 * (attempt + 1)
                        logger.warning(f"⏳ Overseas Order TPS limit (rt_cd). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                        time.sleep(wait_sec)
                        continue
                    logger.error(f"❌ Overseas Order failed: {data['msg1']}")
                    return {"status": "failed", "msg": data['msg1']}
                    
                logger.info(f"✅ Overseas Order Success! [{order_type.upper()}] {ticker} {quantity}qty @ ${price}")
                return {"status": "success", "data": data['output']}
            except requests.exceptions.HTTPError as e:
                error_msg = str(e)
                try:
                    # 초당 거래건수 에러인 경우 재시도
                    if hasattr(e, 'response') and cls._is_rate_limited_response(e.response) and attempt < max_retries - 1:
                        wait_sec = 1.2 * (attempt + 1)
                        logger.warning(f"⏳ Overseas Order TPS limit (HTTP). retry {attempt + 1}/{max_retries} in {wait_sec:.1f}s...")
                        time.sleep(wait_sec)
                        continue
                    logger.error(f"❌ Error sending overseas order: {error_msg}")
                except:
                    logger.error(f"❌ Error sending overseas order: {error_msg}")
                return {"status": "error", "msg": error_msg}
            except Exception as e:
                logger.error(f"❌ Error sending overseas order: {e}")
                return {"status": "error", "msg": str(e)}
        
        # 모든 재시도 실패
        logger.error(f"❌ Overseas Order failed after {max_retries} retries")
        return {"status": "error", "msg": f"Failed after {max_retries} retries due to rate limit"}

    # --- 확장된 메서드 (Modular 통합용) ---
    @classmethod
    def get_financials(cls, ticker: str, meta: dict = None):
        """국내 주식 재무/기본 지표 조회 (KisFetcher 활용). meta는 dict 또는 KisFinancialsMeta DTO."""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        meta_dict = meta.model_dump() if (meta is not None and hasattr(meta, "model_dump")) else meta
        return KisFetcher.fetch_domestic_price(token, ticker, meta=meta_dict)

    @classmethod
    def get_overseas_financials(cls, ticker: str, market: str = "NASD", meta: dict = None):
        """해외 주식 재무/기본 지표 조회 (KisFetcher 활용). meta는 dict 또는 KisFinancialsMeta DTO."""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        meta_dict = meta.model_dump() if (meta is not None and hasattr(meta, "model_dump")) else meta
        return KisFetcher.fetch_overseas_price(token, ticker, meta=meta_dict)
    @classmethod
    def get_overseas_ranking(cls, excd: str = "NAS"):
        """해외 주식 시가총액 순위 조회 (KisFetcher 활용)"""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        return KisFetcher.fetch_overseas_ranking(token, excd=excd)
