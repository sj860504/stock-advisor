import requests
from typing import Optional

class AlertService:
    """
    슬랙 알림 서비스
    """
    _webhook_url: Optional[str] = None
    _sent_alerts = set()  # 중복 알림 방지
    
    @classmethod
    def set_webhook(cls, webhook_url: str):
        cls._webhook_url = webhook_url
    
    @classmethod
    def send_slack_alert(cls, message: str, channel: str = None) -> bool:
        """슬랙으로 알림을 보냅니다."""
        if not cls._webhook_url:
            print(f"[Alert] No webhook configured: {message}")
            return False
        
        try:
            payload = {"text": message}
            if channel:
                payload["channel"] = channel
            
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
        ema200 = data.get('ema200')
        
        alert_key = f"{ticker}_{data.get('time', '')[:10]}"  # 하루에 한번만 알림
        
        # 1. RSI 과매도 (30 미만)
        if rsi and rsi < 30:
            alert = f"📉 **{ticker}** RSI 과매도! (RSI: {rsi}) - 현재가: ${price}"
            if f"{alert_key}_oversold" not in cls._sent_alerts:
                alerts.append(alert)
                cls._sent_alerts.add(f"{alert_key}_oversold")
        
        # 2. RSI 과매수 (70 초과)
        if rsi and rsi > 70:
            alert = f"📈 **{ticker}** RSI 과매수! (RSI: {rsi}) - 현재가: ${price}"
            if f"{alert_key}_overbought" not in cls._sent_alerts:
                alerts.append(alert)
                cls._sent_alerts.add(f"{alert_key}_overbought")
        
        # 3. DCF 저평가 (현재가 < DCF의 80%)
        if dcf and price and price < dcf * 0.8:
            upside = ((dcf - price) / price) * 100
            alert = f"🎯 **{ticker}** DCF 저평가! 현재가 ${price} < 적정가 ${dcf:.2f} (상승여력 {upside:.1f}%)"
            if f"{alert_key}_undervalued" not in cls._sent_alerts:
                alerts.append(alert)
                cls._sent_alerts.add(f"{alert_key}_undervalued")
        
        # 4. EMA200 지지선 터치
        if ema200 and price and abs(price - ema200) / ema200 < 0.02:
            alert = f"📊 **{ticker}** EMA200 지지선 터치! (EMA200: ${ema200:.2f}, 현재가: ${price})"
            if f"{alert_key}_ema200" not in cls._sent_alerts:
                alerts.append(alert)
                cls._sent_alerts.add(f"{alert_key}_ema200")
        
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
