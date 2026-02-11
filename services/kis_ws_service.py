import websockets
import asyncio
import json
import logging
import os
import requests
from config import Config
from services.market_data_service import MarketDataService
from utils.logger import get_logger

logger = get_logger("kis_ws_service")

class KisWsService:
    """
    ?쒓뎅?ъ옄利앷텒 WebSocket ?쒕퉬??
    - ?ㅼ떆媛?泥닿껐媛 ?섏떊
    - MarketDataService濡??곗씠???몄떆
    """
    
    def __init__(self):
        self.ws_url = Config.KIS_WS_URL
        self.approval_key = None
        self.connected = False
        self.subscribed_tickers = set()
        
    def get_approval_key(self):
        """?뱀냼耳??묒냽??諛쒓툒"""
        url = f"{Config.KIS_BASE_URL}/oauth2/Approval"
        headers = {"content-type": "application/json; charset=utf-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": Config.KIS_APP_KEY,
            "secretkey": Config.KIS_APP_SECRET
        }
        
        try:
            res = requests.post(url, headers=headers, json=body, timeout=5)
            if res.status_code == 200:
                self.approval_key = res.json().get('approval_key')
                logger.info(f"?뵎 WebSocket Approval Key acquired.")
                return True
            else:
                logger.error(f"??Failed to get approval key: {res.text}")
                return False
        except Exception as e:
            logger.error(f"??Error getting approval key: {e}")
            return False

    async def connect(self):
        """?뱀냼耳??곌껐 諛??먮룞 ?ъ뿰寃?猷⑦봽"""
        while True:
            if not self.approval_key:
                if not self.get_approval_key():
                    await asyncio.sleep(5)
                    continue

            # 紐⑥쓽?ъ옄(VTS)??寃쎌슦 ?ы듃 議곗젙 (21000 -> 31000)
            ws_url = self.ws_url
            if "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
                ws_url = ws_url.replace(":21000", ":31000")
                logger.info(f"?봽 VTS Environment detected. Using port 31000: {ws_url}")

            logger.info(f"?? Connecting to WebSocket: {ws_url}")
            
            try:
                async with websockets.connect(
                    ws_url, 
                    ping_interval=30, 
                    ping_timeout=10,
                    close_timeout=10
                ) as websocket:
                    self.connected = True
                    self.websocket = websocket
                    logger.info("??WebSocket Connected!")
                    
                    # 湲곗〈 援щ룆 ?곗빱 ?ш뎄??
                    if self.subscribed_tickers:
                        logger.info(f"?봽 Re-subscribing to {len(self.subscribed_tickers)} tickers...")
                        # 援щ룆 濡쒖쭅???꾪빐 ?꾩떆濡?鍮꾩슦怨??ㅼ떆 ?깅줉 (?대??곸쑝濡?send ??
                        saved_tickers = list(self.subscribed_tickers)
                        self.subscribed_tickers.clear()
                        for ticker in saved_tickers:
                            # 二쇱쓽: market ?뺣낫媛 ?좎떎?? 
                            # ?꾨뱶 蹂닿????꾩슂?섏?留??쇰떒 援?궡 二쇱떇 媛??
                            await self.subscribe(ticker, market="KRX" if ticker.isdigit() else "NAS")

                    while True:
                        try:
                            msg = await websocket.recv()
                            await self.handle_message(msg)
                        except websockets.ConnectionClosed:
                            logger.warning("?좑툘 WebSocket Connection Closed. Retrying in 5s...")
                            break
                        except Exception as e:
                            logger.error(f"Error receiving message: {e}")
                            break
            except Exception as e:
                logger.error(f"??WebSocket Connection Error: {e}")
                
            self.connected = False
            self.websocket = None
            await asyncio.sleep(5) # ?ъ뿰寃??湲?5珥?

    async def subscribe(self, ticker: str, market: str = "KRX"):
        """醫낅ぉ ?ㅼ떆媛?泥닿껐媛 援щ룆"""
        # MarketDataService??醫낅ぉ ?깅줉 (Warm-up? ?곗씠???곌껐 ?щ?? 臾닿??섍쾶 ?쒖옉)
        MarketDataService.register_ticker(ticker)
        
        if not self.connected or not self.websocket:
            # ?꾩쭅 ?곌껐 ?꾩씠?쇰㈃ 援щ룆 ???由ъ뒪?몄뿉留?異붽??대몺 (?묒냽 ???먮룞 泥섎━??
            self.subscribed_tickers.add(ticker)
            logger.info(f"??{ticker} added to subscription queue (Waiting for connection...)")
            return

        if ticker in self.subscribed_tickers:
            # ?대? 泥섎━??寃쎌슦 (?ъ젒???곹솴 ??
            pass
            
        # ... ?댄븯 TR ?꾩넚 濡쒖쭅 ...
        
        # TR_ID 諛?TR_KEY 寃곗젙
        if market == "KRX":
            tr_id = "H0STCNT0"
            tr_key = ticker
        else:
            # ?댁쇅 二쇱떇 (誘멸뎅 湲곗?)
            tr_id = "HDFSUSP0" # ?ㅼ떆媛?泥닿껐
            # market: NAS, NYS, AMS ??
            tr_key = f"D{market}{ticker}"
            
        body = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key
                }
            }
        }
        await self.websocket.send(json.dumps(body))
        self.subscribed_tickers.add(ticker)
        logger.info(f"?벀 Subscribed to {ticker} ({market})")

    async def handle_message(self, msg):
        """?섏떊 硫붿떆吏 泥섎━ 諛??뚯떛"""
        # 泥?湲?먭? 0(?ㅼ떆媛? or 1(?ㅼ떆媛? ??寃쎌슦
        if msg[0] not in ('0', '1'):
            # ?쒖뼱 硫붿떆吏 (PingPong ??
            return

        try:
            # ?곗씠???щ㎎: 0|TR_ID|DATA_KEY|DATA_VALUE...
            parts = msg.split('|')
            if len(parts) < 4: return
            
            tr_id = parts[1]
            data_str = parts[3]
            
            if tr_id == "H0STCNT0": # 二쇱떇 泥닿껐媛 (援?궡)
                ticker = parts[2] # ?뱀? parts[3].split('^')[0]
                self.parse_realtime_price(ticker, data_str)
            elif tr_id == "HDFSUSP0": # ?댁쇅 二쇱떇 泥닿껐媛
                # ?댁쇅 二쇱떇 ?щ㎎: D + 嫄곕옒??+ 醫낅ぉ
                # ?곗씠???? SYMBOL^TIME^PRICE^...
                values = data_str.split('^')
                ticker = values[0]
                self.parse_overseas_realtime_price(ticker, data_str)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def parse_overseas_realtime_price(self, ticker: str, data_str: str):
        """HDFSUSP0 ?곗씠???뚯떛 (誘멸뎅 二쇱떇)"""
        values = data_str.split('^')
        if len(values) < 10: return
        
        # ?몃뜳?? 0:?곗빱, 1:?쒓컙, 2:?꾩옱媛, 3:?鍮꾨??? 4:?鍮? 5:?鍮꾩쑉...
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            # 誘멸뎅 二쇱떇? ??怨?? ?꾩튂媛 ?ㅻ? ???덉쓬 (?꾩슂??蹂댁셿)
            "open": float(values[7]) if len(values) > 7 else 0,
            "high": float(values[8]) if len(values) > 8 else 0,
            "low": float(values[9]) if len(values) > 9 else 0,
            "volume": int(values[6]) if len(values) > 6 else 0
        }
        MarketDataService.on_realtime_data(ticker, parsed_data)

    def parse_realtime_price(self, ticker: str, data_str: str):
        """
        H0STCNT0 ?곗씠???뚯떛
        ?щ㎎: 醫낅ぉ肄붾뱶^泥닿껐?쒓컙^?꾩옱媛^...
        """
        values = data_str.split('^')
        if len(values) < 10: return
        
        # 臾몄꽌 湲곗? 留ㅽ븨 (?몃뜳??二쇱쓽)
        # 0: 醫낅ぉ肄붾뱶
        # 1: 泥닿껐?쒓컙
        # 2: ?꾩옱媛
        # 3: ?꾩씪?鍮꾨???
        # 4: ?꾩씪?鍮?
        # 5: ?꾩씪?鍮꾩쑉
        # 10: ?쒓?
        # 11: 怨좉?
        # 12: ?媛
        # 13: ?꾩쟻嫄곕옒??
        
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            "open": float(values[10]),
            "high": float(values[11]),
            "low": float(values[12]),
            "volume": int(values[13])
        }
        
        # MarketDataService濡??몄떆
        MarketDataService.on_realtime_data(ticker, parsed_data)

# ?깃????몄뒪?댁뒪
kis_ws_service = KisWsService()
