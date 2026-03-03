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

# WebSocket 상수
WS_RETRY_DELAY_INITIAL = 5
WS_RETRY_DELAY_MAX = 60
WS_APPROVAL_REQUEST_TIMEOUT = 5


class KisWsService:
    """
    한국투자증권 WebSocket 서비스
    - 실시간 체결가 수신
    - MarketDataService로 데이터 푸시
    """
    
    def __init__(self):
        self.ws_url = Config.KIS_WS_URL
        self.approval_key = None
        self.connected = False
        self.subscribed_tickers = set()
        self.subscribed_markets = {}
        
    def get_approval_key(self):
        """웹소켓 접속키 발급 (실전 크레덴셜 설정 시 실전 서버 사용)"""
        if Config.has_real_credentials():
            url = f"{Config.KIS_REAL_BASE_URL}/oauth2/Approval"
            body = {
                "grant_type": "client_credentials",
                "appkey": Config.KIS_REAL_APP_KEY,
                "secretkey": Config.KIS_REAL_APP_SECRET,
            }
            self.ws_url = Config.KIS_REAL_WS_URL
            env_label = "실전"
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

    async def connect(self):
        """웹소켓 연결 및 자동 재연결 루프"""
        retry_delay = 5
        while True:
            try:
                if not self.approval_key:
                    if not self.get_approval_key():
                        await asyncio.sleep(retry_delay)
                        continue

                # VTS 환경에서만 포트 31000으로 전환 (실전 계좌는 21000 유지)
                ws_url = self.ws_url
                if not Config.has_real_credentials() and "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
                    ws_url = ws_url.replace(":21000", ":31000")
                    logger.info(f"🔌 VTS Environment detected. Using port 31000: {ws_url}")

                logger.info(f"🌐 Connecting to WebSocket: {ws_url} (Timeout: 60s)")
                
                async with websockets.connect(
                    ws_url, 
                    ping_interval=30, 
                    ping_timeout=20,
                    close_timeout=20,
                    open_timeout=60 # 핸드쉐이크 타임아웃 60초로 연장
                ) as websocket:
                    self.connected = True
                    self.websocket = websocket
                    retry_delay = 5 # 연결 성공 시 대기시간 초기화
                    logger.info("✅ WebSocket Connected!")
                    
                    # 기존 구독 티커 재요구
                    if self.subscribed_tickers:
                        logger.info(f"🔄 Re-subscribing to {len(self.subscribed_tickers)} tickers...")
                        saved_items = [(t, self.subscribed_markets.get(t)) for t in self.subscribed_tickers]
                        self.subscribed_tickers.clear()
                        for ticker, market in saved_items:
                            if not market:
                                market = "KRX" if is_kr(ticker) else "NAS"
                            await self.subscribe(ticker, market=market)
                            await asyncio.sleep(1.0) # 재구독 속도 조절 (TPS 준수)

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
            except Exception as e:
                logger.error(f"❌ WebSocket Connection Error: {e}")
                
            self.connected = False
            self.websocket = None
            logger.info(f"🔄 Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            # 지수 백오프 적용 (최대 60초)
            retry_delay = min(retry_delay * 2, 60)

    async def subscribe(self, ticker: str, market: str = "KRX"):
        """종목 실시간 체결가 구독"""
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
        await self.websocket.send(json.dumps(body))
        self.subscribed_tickers.add(ticker)
        self.subscribed_markets[ticker] = market
        logger.info(f"➕ Subscribed to {ticker} ({market})")

    async def handle_message(self, msg):
        """수신 메시지 처리 및 파싱"""
        if msg[0] not in ('0', '1'):
            return

        try:
            parts = msg.split('|')
            if len(parts) < 4: return
            
            tr_id = parts[1]
            data_str = parts[3]
            
            if tr_id == "H0STCNT0":
                ticker = parts[2]
                # KIS 일부 메시지는 parts[2]가 종목코드가 아니라 순번('001','002') 등으로 옴 → 6자리 한국 종목만 처리
                if is_kr(ticker) and len(ticker) == 6:
                    self.parse_realtime_price(ticker, data_str)
            elif tr_id == "HDFSUSP0":
                values = data_str.split('^')
                ticker = values[0]
                self.parse_overseas_realtime_price(ticker, data_str)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def parse_overseas_realtime_price(self, ticker: str, data_str: str):
        """HDFSUSP0 데이터 파싱 (미국 주식)"""
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
        """H0STCNT0 데이터 파싱 (국내 주식)"""
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
