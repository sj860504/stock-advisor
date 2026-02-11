import requests
import json
import time
from datetime import datetime
from stock_advisor.config import Config
from stock_advisor.utils.logger import get_logger

logger = get_logger("kis_service")

class KisService:
    """
    한국투자증권 API 연동 서비스
    """
    _access_token = None
    _token_expiry = None
    
    @classmethod
    def get_access_token(cls):
        """접근 토큰 발급 및 갱신"""
        # 기존 토큰이 있고 만료되지 않았으면 재사용
        if cls._access_token and cls._token_expiry and datetime.now() < cls._token_expiry:
            return cls._access_token
            
        url = f"{Config.KIS_BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json; charset=utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.KIS_APP_KEY,
            "appsecret": Config.KIS_APP_SECRET
        }
        
        try:
            # json=body를 사용하면 headers를 수정하지 않아도 application/json으로 전송됩니다.
            res = requests.post(url, json=body, timeout=5)
            
            # 에러 발생 시 상세 내용 확인을 위해 먼저 json 파싱 시도
            try:
                data = res.json()
            except:
                data = res.text
                
            res.raise_for_status()
            
            cls._access_token = data['access_token']
            # 만료 시간 설정 (여유 있게 1시간 전으로 잡음, 실제 수명은 보통 24시간)
            # API 응답에는 expires_in이 초 단위로 옴
            cls._token_expiry = datetime.now().replace(microsecond=0) # 단순화: 매번 갱신하지 않도록 메모리에만 유지
            
            logger.info("🔑 KIS Access Token issued successfully.")
            return cls._access_token
        except Exception as e:
            logger.error(f"❌ Failed to get access token: {e}")
            logger.error(f"Response: {res.text if 'res' in locals() else 'No response'}")
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
        """
        주식 잔고 조회 (TTTC8434R : 주식잔고조회_실현손익포함 - 모의투자용)
        * 실전투자는 TR_ID가 다를 수 있음 (TTTC8434R 사용)
        """
        # 모의투자용 TR_ID: VTTC8434R (주식 잔고 조회)
        tr_id = "VTTC8434R" 
        
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = cls.get_headers(tr_id)
        
        # 쿼리 파라미터
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
        """
        주식 주문 (매수/매도)
        order_type: "buy" (매수) or "sell" (매도)
        price: 0이면 시장가(01), 0보다 크면 지정가(00)
        """
        # 모의투자용 TR_ID
        # 매수: VTTC0802U, 매도: VTTC0801U
        if order_type == "buy":
            tr_id = "VTTC0802U" 
        else:
            tr_id = "VTTC0801U"
            
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = cls.get_headers(tr_id)
        
        # 주문 구분 (00: 지정가, 01: 시장가)
        ord_dvsn = "00" if price > 0 else "01"
        ord_price = str(price) if price > 0 else "0"
        
        body = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
            "PDNO": ticker,         # 종목코드 (6자리)
            "ORD_DVSN": ord_dvsn,   # 주문구분
            "ORD_QTY": str(quantity), # 주문수량
            "ORD_UNPR": ord_price   # 주문단가
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
        """
        해외 주식 주문 (미국)
        ticker: 종목코드 (예: TSLA)
        market: 거래소 (NASD: 나스닥, NYS: 뉴욕, AMS: 아멕스)
        price: 0이면 시장가
        """
        # 모의투자 미국 주식 TR_ID
        # 매수: VTTT1002U, 매도: VTTT1001U
        tr_id = "VTTT1002U" if order_type == "buy" else "VTTT1001U"
        
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
        headers = cls.get_headers(tr_id)
        
        # 주문 구분 (00: 지정가) - 해외 주식은 시장가(01) 지원 여부가 증권사마다 다르므로 지정가 권장
        ord_dvsn = "00" 
        if price <= 0:
             # 가격 미입력 시 에러 처리 (안전을 위해)
             return {"status": "error", "msg": "해외 주식 주문 시 지정가(price)를 입력해야 합니다."}

        body = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
            "OVRS_EXCG_CD": market,
            "PDNO": ticker,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": ord_dvsn
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
