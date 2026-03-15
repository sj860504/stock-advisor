import websockets
import asyncio
import json
import logging
import os
import requests
from config import Config
from services.market.market_data_service import MarketDataService
from utils.logger import get_logger
from utils.market import is_kr

logger = get_logger("kis_ws_service")

# WebSocket constants
WS_RETRY_DELAY_INITIAL = 5
WS_RETRY_DELAY_MAX = 60
WS_APPROVAL_REQUEST_TIMEOUT = 5


class KisWsService:
    """KIS WebSocket service.
    - Receives real-time execution prices
    - Pushes data to MarketDataService
    """
    
    def __init__(self):
        self.ws_url = Config.KIS_WS_URL
        self.approval_key = None
        self.connected = False
        self.subscribed_tickers = set()
        self.subscribed_markets = {}
        
    def get_approval_key(self):
        """Get WebSocket approval key (uses live server if real credentials configured)."""
        if Config.has_real_credentials():
            url = f"{Config.KIS_REAL_BASE_URL}/oauth2/Approval"
            body = {
                "grant_type": "client_credentials",
                "appkey": Config.KIS_REAL_APP_KEY,
                "secretkey": Config.KIS_REAL_APP_SECRET,
            }
            self.ws_url = Config.KIS_REAL_WS_URL
            env_label = "live"
        else:
            url = f"{Config.KIS_BASE_URL}/oauth2/Approval"
            body = {
                "grant_type": "client_credentials",
                "appkey": Config.KIS_APP_KEY,
                "secretkey": Config.KIS_APP_SECRET,
            }
            env_label = "VTS"

        headers = {"content-type": "application/json; charset=utf-8"}
        try:
            response = requests.post(url, headers=headers, json=body, timeout=WS_APPROVAL_REQUEST_TIMEOUT)
            if response.status_code == 200:
                self.approval_key = response.json().get("approval_key")
                logger.info(f"🔑 WebSocket Approval Key acquired ({env_label}).")
                return True
            logger.error(f"❌ Failed to get approval key: {response.text}")
            return False
        except Exception as e:
            logger.error(f"❌ Error getting approval key: {e}")
            return False

    async def _resubscribe_all_tickers(self) -> None:
        """재연결 시 이전 구독 종목 복원. TPS 준수를 위해 1초 간격."""
        if not self.subscribed_tickers:
            return
        logger.info(f"🔄 Re-subscribing to {len(self.subscribed_tickers)} tickers...")
        saved_items = [(t, self.subscribed_markets.get(t)) for t in self.subscribed_tickers]
        self.subscribed_tickers.clear()
        for ticker, market in saved_items:
            if not market:
                market = "KRX" if is_kr(ticker) else "NAS"
            await self.subscribe(ticker, market=market)
            await asyncio.sleep(1.0)

    async def _run_message_loop(self, websocket) -> None:
        """메시지 수신 루프. ConnectionClosed 또는 예외 시 break."""
        while True:
            try:
                msg = await websocket.recv()
                await self.handle_message(msg)
            except websockets.ConnectionClosed:
                logger.warning("📡 WebSocket Connection Closed by Server.")
                break
            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                break

    async def connect(self):
        """WebSocket connection with auto-reconnect loop."""
        retry_delay = WS_RETRY_DELAY_INITIAL
        while True:
            try:
                if not self.approval_key:
                    if not self.get_approval_key():
                        await asyncio.sleep(retry_delay)
                        continue

                ws_url = self.ws_url
                # Switch to port 31000 in VTS environment only (live account keeps 21000)
                if not Config.has_real_credentials() and "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
                    ws_url = ws_url.replace(":21000", ":31000")
                    logger.info(f"🔌 VTS Environment detected. Using port 31000: {ws_url}")

                logger.info(f"🌐 Connecting to WebSocket: {ws_url} (Timeout: 60s)")
                async with websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=20,
                    open_timeout=60,
                ) as websocket:
                    self.connected = True
                    self.websocket = websocket
                    retry_delay = WS_RETRY_DELAY_INITIAL
                    logger.info("✅ WebSocket Connected!")
                    await self._resubscribe_all_tickers()
                    await self._run_message_loop(websocket)
            except Exception as e:
                logger.error(f"❌ WebSocket Connection Error: {e}")

            self.connected = False
            self.websocket = None
            logger.info(f"🔄 Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, WS_RETRY_DELAY_MAX)

    async def subscribe(self, ticker: str, market: str = "KRX"):
        """Subscribe to real-time execution price for a ticker."""
        MarketDataService.register_ticker(ticker)
        market = (market or "KRX").upper()
        
        if not self.connected or not self.websocket:
            self.subscribed_tickers.add(ticker)
            self.subscribed_markets[ticker] = market
            logger.info(f"🕒 {ticker} added to subscription queue (Waiting for connection...)")
            return

        if ticker in self.subscribed_tickers:
            pass
            
        if market == "KRX":
            tr_id = "H0STCNT0"
            tr_key = ticker
        else:
            tr_id = "HDFSUSP0"
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
        try:
            await self.websocket.send(json.dumps(body))
            self.subscribed_tickers.add(ticker)
            self.subscribed_markets[ticker] = market
            logger.info(f"➕ Subscribed to {ticker} ({market})")
        except Exception as e:
            logger.warning(f"⚠️ {ticker} subscription send failed (WS closed): {e}")
            self.connected = False
            self.websocket = None

    async def handle_message(self, msg):
        """Handle and parse incoming messages."""
        if msg[0] not in ('0', '1'):
            return

        try:
            parts = msg.split('|')
            if len(parts) < 4: return
            
            tr_id = parts[1]
            data_str = parts[3]
            
            if tr_id == "H0STCNT0":
                ticker = parts[2]
                # Some KIS messages have sequence numbers in parts[2] instead of ticker — only process 6-digit KR tickers
                if is_kr(ticker) and len(ticker) == 6:
                    self.parse_realtime_price(ticker, data_str)
            elif tr_id == "HDFSUSP0":
                values = data_str.split('^')
                ticker = values[0]
                self.parse_overseas_realtime_price(ticker, data_str)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def parse_overseas_realtime_price(self, ticker: str, data_str: str):
        """Parse HDFSUSP0 data (US stocks)."""
        values = data_str.split('^')
        if len(values) < 10: return
        
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            "open": float(values[7]) if len(values) > 7 else 0,
            "high": float(values[8]) if len(values) > 8 else 0,
            "low": float(values[9]) if len(values) > 9 else 0,
            "volume": int(values[6]) if len(values) > 6 else 0
        }
        MarketDataService.on_realtime_data(ticker, parsed_data)

    def parse_realtime_price(self, ticker: str, data_str: str):
        """Parse H0STCNT0 data (domestic stocks)."""
        values = data_str.split('^')
        if len(values) < 10: return
        
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            "open": float(values[10]),
            "high": float(values[11]),
            "low": float(values[12]),
            "volume": int(values[13])
        }
        MarketDataService.on_realtime_data(ticker, parsed_data)

kis_ws_service = KisWsService()
