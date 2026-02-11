import requests
import json
import os
from typing import Optional

class ExecutionService:
    """
    ?쒓뎅?ъ옄利앷텒(KIS) API瑜??듯븳 ?ㅼ떆媛?留ㅻℓ ?ㅽ뻾 ?쒕퉬??
    """
    _base_url = "https://openapivts.koreainvestment.com:29443" # 紐⑥쓽?ъ옄??URL
    _access_token: Optional[str] = None
    
    @classmethod
    def _get_token(cls):
        """API ?묎렐???꾪븳 ?좏겙 諛쒓툒"""
        # .env???섍꼍蹂?섏뿉??濡쒕뱶 (Sean?섏씠 諛쒓툒諛쏆쑝?쒕㈃ ?ш린???ㅼ젙 ?꾩슂)
        app_key = os.getenv("KIS_APP_KEY")
        app_secret = os.getenv("KIS_APP_SECRET")
        
        if not app_key or not app_secret:
            print("??KIS API ?ㅺ? ?ㅼ젙?섏? ?딆븯?듬땲??")
            return None

        url = f"{cls._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret
        }
        
        try:
            res = requests.post(url, json=payload)
            cls._access_token = res.json().get("access_token")
            print("??KIS API ?좏겙 諛쒓툒 ?깃났")
            return cls._access_token
        except Exception as e:
            print(f"???좏겙 諛쒓툒 ?먮윭: {e}")
            return None

    @classmethod
    def buy_market_order(cls, ticker: str, quantity: int):
        """?쒖옣媛 留ㅼ닔 二쇰Ц"""
        if not cls._access_token:
            cls._get_token()
            
        url = f"{cls._base_url}/uapi/domestic-stock/v1/trading/order-cash" # 援?궡二쇱떇 湲곗? ?덉떆
        
        # ?댁쇅二쇱떇(誘멸뎅)??寃쎌슦 URL怨??ㅻ뜑媛 ?щ씪吏?
        if not ticker.isdigit(): # 誘멸뎅 二쇱떇??寃쎌슦 (?뚰뙆踰??곗빱)
            url = f"{cls._base_url}/uapi/overseas-stock/v1/trading/order"
            
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {cls._access_token}",
            "appkey": os.getenv("KIS_APP_KEY"),
            "appsecret": os.getenv("KIS_APP_SECRET"),
            "tr_id": "VTTT0001U" if ticker.isdigit() else "VTTT1002U" # 紐⑥쓽?ъ옄 留ㅼ닔 TR ID
        }
        
        # ?곸꽭 二쇰Ц ?곗씠??(?쒓뎅?ъ옄利앷텒 洹쒓꺽??留욎땄 ?꾩슂)
        # ??遺遺꾩? 諛쒓툒諛쏆쑝??怨꾩쥖踰덊샇媛 ?덉뼱???꾩꽦??媛?ν빀?덈떎.
        print(f"?? [{ticker}] {quantity}二??쒖옣媛 留ㅼ닔 二쇰Ц ?꾩넚 ?쒕룄...")
        return {"status": "ready", "message": "API ?ㅼ? 怨꾩쥖 ?뺣낫媛 ?ㅼ젙?섎㈃ ?ㅼ젣 二쇰Ц???섍컩?덈떎."}

    @classmethod
    def get_balance(cls):
        """怨꾩쥖 ?붽퀬 諛??꾧툑 議고쉶"""
        print("?뵇 怨꾩쥖 ?붽퀬 議고쉶 以?..")
        return {"cash": 10000000, "stocks": []} # ?뚯뒪?몄슜 媛吏??곗씠??
