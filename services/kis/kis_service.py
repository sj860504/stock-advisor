import requests
import json
import time
import os
from datetime import datetime
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_service")

class KisService:
    """
    한국투자증권 API 연동 서비스
    """
    _access_token = None
    _token_expiry = None
    
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
        
        try:
            res = requests.get(url, headers=headers, params=params)
            res.raise_for_status()
            data = res.json()
            
            if data['rt_cd'] != '0':
                logger.error(f"❌ Balance fetch failed: {data['msg1']}")
                return None
                
            return {
                "holdings": data['output1'],
                "summary": data['output2']
            }
        except Exception as e:
            logger.error(f"❌ Error fetching balance: {e}")
            return None

    @classmethod
    def send_order(cls, ticker: str, quantity: int, price: int = 0, order_type: str = "buy"):
        """국내 주식 주문 (매수/매도)"""
        if order_type == "buy":
            tr_id = "VTTC0802U" 
        else:
            tr_id = "VTTC0801U"
            
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = cls.get_headers(tr_id)
        
        ord_dvsn = "00" if price > 0 else "01"
        ord_price = str(price) if price > 0 else "0"
        
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
                logger.error(f"❌ Order failed: {data['msg1']}")
                return {"status": "failed", "msg": data['msg1']}
                
            logger.info(f"✅ Order Success! [{order_type.upper()}] {ticker} {quantity}qty")
            return {"status": "success", "data": data['output']}
            
        except Exception as e:
            logger.error(f"❌ Error sending order: {e}")
            return {"status": "error", "msg": str(e)}

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
