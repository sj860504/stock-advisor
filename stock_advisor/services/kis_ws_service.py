import websockets
import asyncio
import json
import logging
import os
import requests
from stock_advisor.config import Config
from stock_advisor.services.market_data_service import MarketDataService
from stock_advisor.utils.logger import get_logger

logger = get_logger("kis_ws_service")

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
        
    def get_approval_key(self):
        """웹소켓 접속키 발급"""
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
                logger.info(f"🔑 WebSocket Approval Key acquired.")
                return True
            else:
                logger.error(f"❌ Failed to get approval key: {res.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Error getting approval key: {e}")
            return False

    async def connect(self):
        """웹소켓 연결 및 자동 재연결 루프"""
        while True:
            if not self.approval_key:
                if not self.get_approval_key():
                    await asyncio.sleep(5)
                    continue

            # 모의투자(VTS)인 경우 포트 조정 (21000 -> 31000)
            ws_url = self.ws_url
            if "vts" in Config.KIS_BASE_URL.lower() and ":21000" in ws_url:
                ws_url = ws_url.replace(":21000", ":31000")
                logger.info(f"🔄 VTS Environment detected. Using port 31000: {ws_url}")

            logger.info(f"🚀 Connecting to WebSocket: {ws_url}")
            
            try:
                async with websockets.connect(
                    ws_url, 
                    ping_interval=30, 
                    ping_timeout=10,
                    close_timeout=10
                ) as websocket:
                    self.connected = True
                    self.websocket = websocket
                    logger.info("✅ WebSocket Connected!")
                    
                    # 기존 구독 티커 재구독
                    if self.subscribed_tickers:
                        logger.info(f"🔄 Re-subscribing to {len(self.subscribed_tickers)} tickers...")
                        # 구독 로직을 위해 임시로 비우고 다시 등록 (내부적으로 send 함)
                        saved_tickers = list(self.subscribed_tickers)
                        self.subscribed_tickers.clear()
                        for ticker in saved_tickers:
                            # 주의: market 정보가 유실됨. 
                            # 필드 보관이 필요하지만 일단 국내 주식 가정
                            await self.subscribe(ticker, market="KRX" if ticker.isdigit() else "NAS")

                    while True:
                        try:
                            msg = await websocket.recv()
                            await self.handle_message(msg)
                        except websockets.ConnectionClosed:
                            logger.warning("⚠️ WebSocket Connection Closed. Retrying in 5s...")
                            break
                        except Exception as e:
                            logger.error(f"Error receiving message: {e}")
                            break
            except Exception as e:
                logger.error(f"❌ WebSocket Connection Error: {e}")
                
            self.connected = False
            self.websocket = None
            await asyncio.sleep(5) # 재연결 대기 5초

    async def subscribe(self, ticker: str, market: str = "KRX"):
        """종목 실시간 체결가 구독"""
        # MarketDataService에 종목 등록 (Warm-up은 데이터 연결 여부와 무관하게 시작)
        MarketDataService.register_ticker(ticker)
        
        if not self.connected or not self.websocket:
            # 아직 연결 전이라면 구독 대상 리스트에만 추가해둠 (접속 후 자동 처리됨)
            self.subscribed_tickers.add(ticker)
            logger.info(f"⏳ {ticker} added to subscription queue (Waiting for connection...)")
            return

        if ticker in self.subscribed_tickers:
            # 이미 처리된 경우 (재접속 상황 등)
            pass
            
        # ... 이하 TR 전송 로직 ...
        
        # TR_ID 및 TR_KEY 결정
        if market == "KRX":
            tr_id = "H0STCNT0"
            tr_key = ticker
        else:
            # 해외 주식 (미국 기준)
            tr_id = "HDFSUSP0" # 실시간 체결
            # market: NAS, NYS, AMS 등
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
        logger.info(f"📦 Subscribed to {ticker} ({market})")

    async def handle_message(self, msg):
        """수신 메시지 처리 및 파싱"""
        # 첫 글자가 0(실시간) or 1(실시간) 인 경우
        if msg[0] not in ('0', '1'):
            # 제어 메시지 (PingPong 등)
            return

        try:
            # 데이터 포맷: 0|TR_ID|DATA_KEY|DATA_VALUE...
            parts = msg.split('|')
            if len(parts) < 4: return
            
            tr_id = parts[1]
            data_str = parts[3]
            
            if tr_id == "H0STCNT0": # 주식 체결가 (국내)
                ticker = parts[2] # 혹은 parts[3].split('^')[0]
                self.parse_realtime_price(ticker, data_str)
            elif tr_id == "HDFSUSP0": # 해외 주식 체결가
                # 해외 주식 포맷: D + 거래소 + 종목
                # 데이터 예: SYMBOL^TIME^PRICE^...
                values = data_str.split('^')
                ticker = values[0]
                self.parse_overseas_realtime_price(ticker, data_str)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def parse_overseas_realtime_price(self, ticker: str, data_str: str):
        """HDFSUSP0 데이터 파싱 (미국 주식)"""
        values = data_str.split('^')
        if len(values) < 10: return
        
        # 인덱스: 0:티커, 1:시간, 2:현재가, 3:대비부호, 4:대비, 5:대비율...
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            # 미국 주식은 시/고/저 위치가 다를 수 있음 (필요시 보완)
            "open": float(values[7]) if len(values) > 7 else 0,
            "high": float(values[8]) if len(values) > 8 else 0,
            "low": float(values[9]) if len(values) > 9 else 0,
            "volume": int(values[6]) if len(values) > 6 else 0
        }
        MarketDataService.on_realtime_data(ticker, parsed_data)

    def parse_realtime_price(self, ticker: str, data_str: str):
        """
        H0STCNT0 데이터 파싱
        포맷: 종목코드^체결시간^현재가^...
        """
        values = data_str.split('^')
        if len(values) < 10: return
        
        # 문서 기준 매핑 (인덱스 주의)
        # 0: 종목코드
        # 1: 체결시간
        # 2: 현재가
        # 3: 전일대비부호
        # 4: 전일대비
        # 5: 전일대비율
        # 10: 시가
        # 11: 고가
        # 12: 저가
        # 13: 누적거래량
        
        parsed_data = {
            "price": float(values[2]),
            "rate": float(values[5]),
            "open": float(values[10]),
            "high": float(values[11]),
            "low": float(values[12]),
            "volume": int(values[13])
        }
        
        # MarketDataService로 푸시
        MarketDataService.on_realtime_data(ticker, parsed_data)

# 싱글톤 인스턴스
kis_ws_service = KisWsService()
