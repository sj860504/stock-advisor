import requests
import json
import time
from datetime import datetime
from config import Config
from utils.logger import get_logger

logger = get_logger("kis_service")

class KisService:
    """
    ?쒓뎅?ъ옄利앷텒 API ?곕룞 ?쒕퉬??
    """
    _access_token = None
    _token_expiry = None
    
    @classmethod
    def get_access_token(cls):
        """?묎렐 ?좏겙 諛쒓툒 諛?媛깆떊"""
        # 湲곗〈 ?좏겙???덇퀬 留뚮즺?섏? ?딆븯?쇰㈃ ?ъ궗??
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
            # json=body瑜??ъ슜?섎㈃ headers瑜??섏젙?섏? ?딆븘??application/json?쇰줈 ?꾩넚?⑸땲??
            res = requests.post(url, json=body, timeout=5)
            
            # ?먮윭 諛쒖깮 ???곸꽭 ?댁슜 ?뺤씤???꾪빐 癒쇱? json ?뚯떛 ?쒕룄
            try:
                data = res.json()
            except:
                data = res.text
                
            res.raise_for_status()
            
            cls._access_token = data['access_token']
            # 留뚮즺 ?쒓컙 ?ㅼ젙 (?ъ쑀 ?덇쾶 1?쒓컙 ?꾩쑝濡??≪쓬, ?ㅼ젣 ?섎챸? 蹂댄넻 24?쒓컙)
            # API ?묐떟?먮뒗 expires_in??珥??⑥쐞濡???
            cls._token_expiry = datetime.now().replace(microsecond=0) # ?⑥닚?? 留ㅻ쾲 媛깆떊?섏? ?딅룄濡?硫붾え由ъ뿉留??좎?
            
            logger.info("?뵎 KIS Access Token issued successfully.")
            return cls._access_token
        except Exception as e:
            logger.error(f"??Failed to get access token: {e}")
            logger.error(f"Response: {res.text if 'res' in locals() else 'No response'}")
            raise

    @classmethod
    def get_headers(cls, tr_id: str):
        """API 怨듯넻 ?ㅻ뜑 ?앹꽦"""
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
        二쇱떇 ?붽퀬 議고쉶 (TTTC8434R : 二쇱떇?붽퀬議고쉶_?ㅽ쁽?먯씡?ы븿 - 紐⑥쓽?ъ옄??
        * ?ㅼ쟾?ъ옄??TR_ID媛 ?ㅻ? ???덉쓬 (TTTC8434R ?ъ슜)
        """
        # 紐⑥쓽?ъ옄??TR_ID: VTTC8434R (二쇱떇 ?붽퀬 議고쉶)
        tr_id = "VTTC8434R" 
        
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = cls.get_headers(tr_id)
        
        # 荑쇰━ ?뚮씪誘명꽣
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
                logger.error(f"??Balance fetch failed: {data['msg1']}")
                return None
                
            return {
                "holdings": data['output1'],
                "summary": data['output2']
            }
        except Exception as e:
            logger.error(f"??Error fetching balance: {e}")
            return None

    @classmethod
    def send_order(cls, ticker: str, quantity: int, price: int = 0, order_type: str = "buy"):
        """
        二쇱떇 二쇰Ц (留ㅼ닔/留ㅻ룄)
        order_type: "buy" (留ㅼ닔) or "sell" (留ㅻ룄)
        price: 0?대㈃ ?쒖옣媛(01), 0蹂대떎 ?щ㈃ 吏?뺢?(00)
        """
        # 紐⑥쓽?ъ옄??TR_ID
        # 留ㅼ닔: VTTC0802U, 留ㅻ룄: VTTC0801U
        if order_type == "buy":
            tr_id = "VTTC0802U" 
        else:
            tr_id = "VTTC0801U"
            
        url = f"{Config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        headers = cls.get_headers(tr_id)
        
        # 二쇰Ц 援щ텇 (00: 吏?뺢?, 01: ?쒖옣媛)
        ord_dvsn = "00" if price > 0 else "01"
        ord_price = str(price) if price > 0 else "0"
        
        body = {
            "CANO": Config.KIS_ACCOUNT_NO,
            "ACNT_PRDT_CD": "01",
            "PDNO": ticker,         # 醫낅ぉ肄붾뱶 (6?먮━)
            "ORD_DVSN": ord_dvsn,   # 二쇰Ц援щ텇
            "ORD_QTY": str(quantity), # 二쇰Ц?섎웾
            "ORD_UNPR": ord_price   # 二쇰Ц?④?
        }
        
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body))
            res.raise_for_status()
            data = res.json()
            
            if data['rt_cd'] != '0':
                logger.error(f"??Order failed: {data['msg1']}")
                return {"status": "failed", "msg": data['msg1']}
                
            logger.info(f"??Order Success! [{order_type.upper()}] {ticker} {quantity}qty")
            return {"status": "success", "data": data['output']}
            
        except Exception as e:
            logger.error(f"??Error sending order: {e}")
            return {"status": "error", "msg": str(e)}

    @classmethod
    def send_overseas_order(cls, ticker: str, quantity: int, price: float = 0, order_type: str = "buy", market: str = "NASD"):
        """
        ?댁쇅 二쇱떇 二쇰Ц (誘멸뎅)
        ticker: 醫낅ぉ肄붾뱶 (?? TSLA)
        market: 嫄곕옒??(NASD: ?섏뒪?? NYS: ?댁슃, AMS: ?꾨찕??
        price: 0?대㈃ ?쒖옣媛
        """
        # 紐⑥쓽?ъ옄 誘멸뎅 二쇱떇 TR_ID
        # 留ㅼ닔: VTTT1002U, 留ㅻ룄: VTTT1001U
        tr_id = "VTTT1002U" if order_type == "buy" else "VTTT1001U"
        
        url = f"{Config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
        headers = cls.get_headers(tr_id)
        
        # 二쇰Ц 援щ텇 (00: 吏?뺢?) - ?댁쇅 二쇱떇? ?쒖옣媛(01) 吏???щ?媛 利앷텒?щ쭏???ㅻⅤ誘濡?吏?뺢? 沅뚯옣
        ord_dvsn = "00" 
        if price <= 0:
             # 媛寃?誘몄엯?????먮윭 泥섎━ (?덉쟾???꾪빐)
             return {"status": "error", "msg": "?댁쇅 二쇱떇 二쇰Ц ??吏?뺢?(price)瑜??낅젰?댁빞 ?⑸땲??"}

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
                logger.error(f"??Overseas Order failed: {data['msg1']}")
                return {"status": "failed", "msg": data['msg1']}
                
            logger.info(f"??Overseas Order Success! [{order_type.upper()}] {ticker} {quantity}qty @ ${price}")
            return {"status": "success", "data": data['output']}
            
        except Exception as e:
            logger.error(f"??Error sending overseas order: {e}")
            return {"status": "error", "msg": str(e)}
