import requests
from typing import Optional, List
from stock_advisor.services.news_service import NewsService
from stock_advisor.models.schemas import PriceAlert
from stock_advisor.services.data_service import DataService

class AlertService:
    """
    슬랙 알림 및 사용자 알림 서비스 (Refactored)
    """
    _webhook_url: Optional[str] = None
    _sent_alerts = set()  # 중복 알림 방지
    _prev_data = {}  # {ticker: {price, ema20, ...}}
    _pending_alerts = [] # 에이전트 전송 대기열
    _user_alerts: List[PriceAlert] = [] # 사용자 설정 가격 알림
    
    @classmethod
    def set_webhook(cls, webhook_url: str):
        cls._webhook_url = webhook_url
    
    @classmethod
    def send_slack_alert(cls, message: str, channel: str = None) -> bool:
        """알림을 대기열에 추가합니다 (에이전트가 수거해감)"""
        print(f"[Alert Generated] {message}")
        cls._pending_alerts.append(message)
        return True

    @classmethod
    def get_pending_alerts(cls) -> list:
        """대기 중인 알림을 반환하고 비웁니다."""
        alerts = list(cls._pending_alerts)
        cls._pending_alerts.clear()
        return alerts

    @classmethod
    def add_user_alert(cls, alert: PriceAlert):
        """사용자 알림 추가"""
        cls._user_alerts.append(alert)

    @classmethod
    def check_user_alerts(cls) -> List[str]:
        """사용자 설정 알림 확인"""
        triggered = []
        for alert in cls._user_alerts:
            if not alert.is_active:
                continue
                
            current_price = DataService.get_current_price(alert.ticker)
            if current_price:
                if alert.condition == "above" and current_price >= alert.target_price:
                    triggered.append(f"🔔 {alert.ticker} 도달! 현재가: {current_price} >= 목표가: {alert.target_price}")
                elif alert.condition == "below" and current_price <= alert.target_price:
                    triggered.append(f"🔔 {alert.ticker} 도달! 현재가: {current_price} <= 목표가: {alert.target_price}")
        return triggered
    
    @classmethod
    def check_and_alert(cls, ticker: str, data: dict) -> list:
        """종목 데이터를 확인하고 조건에 맞으면 알림을 생성합니다."""
        alerts = []
        
        # 각 체크 로직을 독립 함수로 분리하여 호출
        alerts.extend(cls._check_volatility(ticker, data))
        alerts.extend(cls._check_rsi(ticker, data))
        alerts.extend(cls._check_undervalued(ticker, data))
        alerts.extend(cls._check_ma_crossover(ticker, data))
        
        # 현재 데이터를 이전 데이터로 저장 (다음 비교를 위해)
        cls._save_current_state(ticker, data)
        
        return alerts

    @classmethod
    def _check_volatility(cls, ticker: str, data: dict) -> list:
        """1. 급등/급락 알림 (Volatility)"""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        change_ratio = (price - prev_price) / prev_price * 100
        is_urgent = False
        msg = ""
        
        if change_ratio >= 2.5:
            msg = f"🔥 **{ticker}** 1분 내 폭풍 급등! (+{change_ratio:.1f}%) - 현재가: ${price}"
            is_urgent = True
        elif change_ratio <= -2.5:
            msg = f"🚨 **{ticker}** 긴급! 패닉 셀링 감지 (-{change_ratio:.1f}%) - 현재가: ${price}"
            is_urgent = True
            
        if is_urgent:
            try:
                news = NewsService.get_latest_news(ticker, limit=2)
                summary = NewsService.summarize_news(ticker, news)
                msg += f"\n\n🤔 **Why? (관련 뉴스)**\n{summary}"
            except:
                pass
            alerts.append(msg)
            
        return alerts

    @classmethod
    def _check_rsi(cls, ticker: str, data: dict) -> list:
        """2. RSI 과매수/과매도 알림"""
        alerts = []
        rsi = data.get('rsi')
        if not rsi: return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_rsi" # 시간당 1회
        
        if rsi < 30:
            if f"{alert_key}_oversold" not in cls._sent_alerts:
                alerts.append(f"🟢 **{ticker}** 줍줍 찬스! (RSI: {rsi:.1f}) - 저가 매수 구간")
                cls._sent_alerts.add(f"{alert_key}_oversold")
        elif rsi > 70:
            if f"{alert_key}_overbought" not in cls._sent_alerts:
                alerts.append(f"🔴 **{ticker}** 단기 과열! (RSI: {rsi:.1f}) - 익절 고려")
                cls._sent_alerts.add(f"{alert_key}_overbought")
        
        return alerts

    @classmethod
    def _check_undervalued(cls, ticker: str, data: dict) -> list:
        """3. DCF 저평가 알림"""
        alerts = []
        price = data.get('price')
        dcf = data.get('fair_value_dcf')
        
        if not (dcf and price and price < dcf * 0.8): return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_dcf"
        
        if f"{alert_key}_undervalued" not in cls._sent_alerts:
            upside = ((dcf - price) / price) * 100
            alerts.append(f"💎 **{ticker}** 저평가 우량주! 적정가 ${dcf:.2f} (상승여력 {upside:.1f}%)")
            cls._sent_alerts.add(f"{alert_key}_undervalued")
            
        return alerts

    @classmethod
    def _check_ma_crossover(cls, ticker: str, data: dict) -> list:
        """4. 지지선(EMA) 돌파/이탈 알림"""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        ema_list = [
            (data.get('ema5'), "EMA5(단기)"), 
            (data.get('ema10'), "EMA10(단기)"), 
            (data.get('ema20'), "EMA20(생명선)"), 
            (data.get('ema60'), "EMA60(수급선)"), 
            (data.get('ema120'), "EMA120(경기선)"), 
            (data.get('ema200'), "EMA200(추세선)")
        ]
        
        for ema_val, name in ema_list:
            if not ema_val: continue
            prev_ema = prev.get(name.split('(')[0].lower()) or ema_val
            
            # 골든크로스
            if prev_price <= prev_ema and price > ema_val:
                alerts.append(f"🚀 **{ticker}** {name} 상향 돌파! (지지선: ${ema_val:.2f}, 현재가: ${price})")
            
            # 데드크로스
            elif prev_price >= prev_ema and price < ema_val:
                alerts.append(f"⚠️ **{ticker}** {name} 하향 이탈! (지지선: ${ema_val:.2f}, 현재가: ${price})")
                
        return alerts

    @classmethod
    def _save_current_state(cls, ticker: str, data: dict):
        """현재 상태를 저장 (다음 틱 비교용)"""
        cls._prev_data[ticker] = {
            'price': data.get('price'),
            'ema5': data.get('ema5'),
            'ema10': data.get('ema10'),
            'ema20': data.get('ema20'),
            'ema60': data.get('ema60'),
            'ema120': data.get('ema120'),
            'ema200': data.get('ema200')
        }
