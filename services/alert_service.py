import requests
from typing import Optional

class AlertService:
    """
    슬랙 알림 서비스
    """
    _webhook_url: Optional[str] = None
    _sent_alerts = set()  # 중복 알림 방지
    _prev_data = {}  # {ticker: {price, ema20, ema60, ema200}}
    
    @classmethod
    def set_webhook(cls, webhook_url: str):
        cls._webhook_url = webhook_url
    
    @classmethod
    def send_slack_alert(cls, message: str, channel: str = None) -> bool:
        """슬랙으로 알림을 보냅니다."""
        # Slack 툴을 통해 메시지 전송 시도
        from message import message as send_message
        try:
            # channel이 #all-seanclaw 처럼 시작하면 이름으로, 아니면 ID로 처리
            target = channel if channel else "C0ACP30M527" # 기본 채널 ID (all-seanclaw)
            send_message(action="send", target=target, message=message)
            return True
        except:
            if not cls._webhook_url:
                print(f"[Alert] No webhook configured: {message}")
                return False
            
            try:
                payload = {"text": message}
                response = requests.post(cls._webhook_url, json=payload)
                return response.status_code == 200
            except Exception as e:
                print(f"Slack alert error: {e}")
                return False
    
    @classmethod
    def check_and_alert(cls, ticker: str, data: dict) -> list:
        """
        종목 데이터를 확인하고 조건에 맞으면 알림을 생성합니다.
        """
        alerts = []
        
        rsi = data.get('rsi')
        price = data.get('price')
        dcf = data.get('fair_value_dcf')
        
        # 지지선 정보
        ema20 = data.get('ema20')
        ema60 = data.get('ema60')
        ema200 = data.get('ema200')
        
        # 이전 데이터 가져오기
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        alert_key = f"{ticker}_{data.get('time', '')[:10]}"
        
        # --- 1. RSI 알림 (하루 한번) ---
        if rsi and rsi < 30:
            if f"{alert_key}_oversold" not in cls._sent_alerts:
                alerts.append(f"📉 **{ticker}** RSI 과매도! (RSI: {rsi}) - 현재가: ${price}")
                cls._sent_alerts.add(f"{alert_key}_oversold")
        
        if rsi and rsi > 70:
            if f"{alert_key}_overbought" not in cls._sent_alerts:
                alerts.append(f"📈 **{ticker}** RSI 과매수! (RSI: {rsi}) - 현재가: ${price}")
                cls._sent_alerts.add(f"{alert_key}_overbought")
        
        # --- 2. DCF 알림 (하루 한번) ---
        if dcf and price and price < dcf * 0.8:
            if f"{alert_key}_undervalued" not in cls._sent_alerts:
                upside = ((dcf - price) / price) * 100
                alerts.append(f"🎯 **{ticker}** DCF 저평가! 현재가 ${price} < 적정가 ${dcf:.2f} (상승여력 {upside:.1f}%)")
                cls._sent_alerts.add(f"{alert_key}_undervalued")

        # --- 3. 지지선 돌파/이탈 알림 (실시간 감지) ---
        if prev_price and price:
            for ema_val, name in [(ema20, "EMA20(단기)"), (ema60, "EMA60(중기)"), (ema200, "EMA200(장기)")]:
                if not ema_val: continue
                
                prev_ema = prev.get(name.split('(')[0].lower()) or ema_val
                
                # 골든크로스 (상향 돌파)
                if prev_price <= prev_ema and price > ema_val:
                    alerts.append(f"🚀 **{ticker}** {name} 상향 돌파! (지지선: ${ema_val:.2f}, 현재가: ${price})")
                
                # 데드크로스 (하향 이탈)
                elif prev_price >= prev_ema and price < ema_val:
                    alerts.append(f"⚠️ **{ticker}** {name} 하향 이탈! (지지선: ${ema_val:.2f}, 현재가: ${price})")

        # 현재 데이터를 이전 데이터로 저장
        cls._prev_data[ticker] = {
            'price': price,
            'ema20': ema20,
            'ema60': ema60,
            'ema200': ema200
        }
        
        return alerts
    
    @classmethod
    def generate_daily_summary(cls, price_cache: dict) -> str:
        """일일 요약 리포트를 생성합니다."""
        oversold = []
        overbought = []
        undervalued = []
        
        for ticker, data in price_cache.items():
            rsi = data.get('rsi')
            price = data.get('price')
            dcf = data.get('fair_value_dcf')
            
            if rsi and rsi < 30:
                oversold.append(f"{ticker} (RSI: {rsi})")
            if rsi and rsi > 70:
                overbought.append(f"{ticker} (RSI: {rsi})")
            if dcf and price and price < dcf * 0.8:
                upside = ((dcf - price) / price) * 100
                undervalued.append(f"{ticker} (+{upside:.0f}%)")
        
        summary = "📊 **일일 시장 요약**\n\n"
        
        if oversold:
            summary += f"📉 **과매도 종목**: {', '.join(oversold)}\n"
        if overbought:
            summary += f"📈 **과매수 종목**: {', '.join(overbought)}\n"
        if undervalued:
            summary += f"🎯 **저평가 종목**: {', '.join(undervalued)}\n"
        
        if not oversold and not overbought and not undervalued:
            summary += "특이사항 없음"
        
        return summary
