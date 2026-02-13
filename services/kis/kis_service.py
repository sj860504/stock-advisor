import requests
import json
import time
import os
from datetime import datetime
from config import Config
from services.market.market_hour_service import MarketHourService
from utils.logger import get_logger

logger = get_logger("kis_service")

class KisService:
    """
    한국투자증권 API 연동 서비스
    """
    _access_token = None
    _token_expiry = None
    _last_balance_data = None
    
    @classmethod
    def get_access_token(cls):
        """접근 토큰 발급 및 갱신 (파일 기반 캐시 적용)"""
        token_cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'kis_token.json')
        
        # 1. 메모리 캐시 확인
        if cls._access_token and cls._token_expiry and datetime.now() < cls._token_expiry:
            return cls._access_token
            
        # 2. 파일 캐시 확인
        if os.path.exists(token_cache_path):
            try:
                with open(token_cache_path, 'r') as f:
                    cache = json.load(f)
                    expiry = datetime.fromisoformat(cache['expiry'])
                    if datetime.now() < expiry:
                        cls._access_token = cache['token']
                        cls._token_expiry = expiry
                        logger.info("📄 KIS Access Token loaded from session file.")
                        return cls._access_token
            except: pass

        # 3. 새로운 토큰 발급
        url = f"{Config.KIS_BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json; charset=utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET
        }
        
        try:
            res = requests.post(url, json=body, timeout=5)
            res.raise_for_status()
            data = res.json()
            
            cls._access_token = data['access_token']
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
        tr_id = "VTTC8434R" 
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = cls.get_headers(tr_id)
        
        params = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
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
        for attempt in range(3):
            try:
                res = requests.get(url, headers=headers, params=params, timeout=8)
                if res.status_code >= 500:
                    logger.warning(
                        f"⏳ Balance API {res.status_code} (attempt {attempt + 1}/3). retrying..."
                    )
                    time.sleep(1.2 * (attempt + 1))
                    continue
                res.raise_for_status()
                data = res.json()
                
                if data.get('rt_cd') != '0':
                    logger.error(f"❌ Balance fetch failed: {data.get('msg1')}")
                    return None
                
                result = {
                    "holdings": data.get('output1', []),
                    "summary": data.get('output2', [])
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
    def _send_domestic_order(cls, ticker: str, quantity: int, tr_id: str, ord_dvsn: str, ord_price: str, log_tag: str):
        """국내주식 주문 공통 실행"""
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = cls.get_headers(tr_id)

        body = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
            "PDNO": ticker,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": ord_price
        }

        try:
            res = requests.post(url, headers=headers, data=json.dumps(body))
            res.raise_for_status()
            data = res.json()

            if data['rt_cd'] != '0':
                logger.error(f"❌ {log_tag} failed: {data['msg1']}")
                return {"status": "failed", "msg": data['msg1']}

            logger.info(f"✅ {log_tag} success! {ticker} {quantity}qty")
            return {"status": "success", "data": data.get('output', {})}
        except Exception as e:
            logger.error(f"❌ Error sending {log_tag}: {e}")
            return {"status": "error", "msg": str(e)}

    @classmethod
    def send_order(cls, ticker: str, quantity: int, price: int = 0, order_type: str = "buy"):
        """국내 주식 주문 (매수/매도)"""
        if order_type == "buy":
            tr_id = "VTTC0802U" 
        else:
            tr_id = "VTTC0801U"

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
        if not ticker.isdigit():
            return {"status": "failed", "msg": "사후장 주문은 국내 주식 티커만 지원합니다."}
        if not MarketHourService.is_kr_after_hours_open():
            return {"status": "failed", "msg": "한국 사후장 주문 가능 시간이 아닙니다."}

        tr_id = "TTTC0802U" if order_type == "buy" else "TTTC0801U"
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
        """해외 주식 주문 (미국 기준)"""
        tr_id = "VTTT1002U" if order_type == "buy" else "VTTT1001U"
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
        headers = cls.get_headers(tr_id)
        
        if price <= 0:
             return {"status": "error", "msg": "해외 주식 주문 시 지정가(price)를 입력해야 합니다."}

        body = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": market,
            "PDNO": ticker,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"
        }
        
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body))
            res.raise_for_status()
            data = res.json()
            
            if data['rt_cd'] != '0':
                logger.error(f"❌ Overseas Order failed: {data['msg1']}")
                return {"status": "failed", "msg": data['msg1']}
                
            logger.info(f"✅ Overseas Order Success! [{order_type.upper()}] {ticker} {quantity}qty @ ${price}")
            return {"status": "success", "data": data['output']}
        except Exception as e:
            logger.error(f"❌ Error sending overseas order: {e}")
            return {"status": "error", "msg": str(e)}

    # --- 확장된 메서드 (Modular 통합용) ---
    @classmethod
    def get_financials(cls, ticker: str, meta: dict = None):
        """국내 주식 재무/기본 지표 조회 (KisFetcher 활용)"""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        return KisFetcher.fetch_domestic_price(token, ticker, meta=meta)

    @classmethod
    def get_overseas_financials(cls, ticker: str, market: str = "NASD", meta: dict = None):
        """해외 주식 재무/기본 지표 조회 (KisFetcher 활용)"""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        return KisFetcher.fetch_overseas_price(token, ticker, meta=meta)
    @classmethod
    def get_overseas_ranking(cls, excd: str = "NAS"):
        """해외 주식 시가총액 순위 조회 (KisFetcher 활용)"""
        from services.kis.fetch.kis_fetcher import KisFetcher
        token = cls.get_access_token()
        return KisFetcher.fetch_overseas_ranking(token, excd=excd)
