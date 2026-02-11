import websockets
import asyncio
import json
import logging
import os
import requests
from stock_advisor.config import Config
from stock_advisor.services.market_data_service import MarketDataService

logger = logging.getLogger("kis_ws_service")

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
        """웹소켓 연결 및 메인 루프"""
        if not self.approval_key:
            if not self.get_approval_key():
                return

        logger.info(f"🚀 Connecting to WebSocket: {self.ws_url}")
        
        try:
            async with websockets.connect(self.ws_url) as websocket:
                self.connected = True
                logger.info("✅ WebSocket Connected!")
                
                # 재연결 시 구독 복구 로직 필요할 수 있음
                # 여기서는 예시로 삼성전자 구독
                await self.subscribe(websocket, "005930") 
                
                while True:
                    try:
                        msg = await websocket.recv()
                        await self.handle_message(msg)
                    except websockets.ConnectionClosed:
                        logger.warning("⚠️ WebSocket Connection Closed.")
                        break
        except Exception as e:
            logger.error(f"❌ WebSocket Error: {e}")
            self.connected = False

    async def subscribe(self, websocket, ticker: str):
        """종목 실시간 체결가 구독"""
        if ticker in self.subscribed_tickers:
            return
            
        # MarketDataService에 종목 등록 (Warm-up)
        MarketDataService.register_ticker(ticker)
        
        body = {
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0", # 실시간 주식 체결가
                    "tr_key": ticker
                }
            }
        }
        await websocket.send(json.dumps(body))
        self.subscribed_tickers.add(ticker)
        logger.info(f"📦 Subscribed to {ticker}")

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
            ticker = parts[3].split('^')[0] # 종목코드
            data_str = parts[3]
            
            if tr_id == "H0STCNT0": # 주식 체결가
                self.parse_realtime_price(ticker, data_str)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")

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
