from typing import Optional, List
import requests
from config import Config
from services.market.news_service import NewsService
from models.schemas import PriceAlert
from services.market.data_service import DataService
from utils.logger import get_logger

logger = get_logger("alert_service")

class AlertService:
    """
    ?щ옓 ?뚮┝ 諛??ъ슜???뚮┝ ?쒕퉬??(Refactored)
    """
    _webhook_url: Optional[str] = None
    _sent_alerts = set()  # 以묐났 ?뚮┝ 諛⑹?
    _prev_data = {}  # {ticker: {price, ema20, ...}}
    _pending_alerts = [] # ?먯씠?꾪듃 ?꾩넚 ?湲곗뿴
    _user_alerts: List[PriceAlert] = [] # ?ъ슜???ㅼ젙 媛寃??뚮┝
    
    @classmethod
    def set_webhook(cls, webhook_url: str):
        cls._webhook_url = webhook_url
    
    @classmethod
    def send_slack_alert(cls, message: str, channel: str = None) -> bool:
        """?щ옓?쇰줈 ?ㅼ젣 ?뚮┝???꾩넚?⑸땲??"""
        webhook_url = cls._webhook_url or Config.SLACK_WEBHOOK_URL
        if not webhook_url:
            print(f"?좑툘 Slack Webhook URL not configured. Log: {message}")
            return False
            
        try:
            payload = {"text": message}
            response = requests.post(webhook_url, json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"??Slack message sent successfully.")
            return True
        except Exception as e:
            logger.error(f"??Failed to send Slack alert: {e}")
            return False

    @classmethod
    def get_pending_alerts(cls) -> list:
        """?湲?以묒씤 ?뚮┝??諛섑솚?섍퀬 鍮꾩썎?덈떎."""
        alerts = list(cls._pending_alerts)
        cls._pending_alerts.clear()
        return alerts

    @classmethod
    def add_user_alert(cls, alert: PriceAlert):
        """?ъ슜???뚮┝ 異붽?"""
        cls._user_alerts.append(alert)

    @classmethod
    def check_user_alerts(cls) -> List[str]:
        """?ъ슜???ㅼ젙 ?뚮┝ ?뺤씤"""
        triggered = []
        for alert in cls._user_alerts:
            if not alert.is_active:
                continue
                
            current_price = DataService.get_current_price(alert.ticker)
            if current_price:
                if alert.condition == "above" and current_price >= alert.target_price:
                    triggered.append(f"?뵒 {alert.ticker} ?꾨떖! ?꾩옱媛: {current_price} >= 紐⑺몴媛: {alert.target_price}")
                elif alert.condition == "below" and current_price <= alert.target_price:
                    triggered.append(f"?뵒 {alert.ticker} ?꾨떖! ?꾩옱媛: {current_price} <= 紐⑺몴媛: {alert.target_price}")
        return triggered
    
    @classmethod
    def check_and_alert(cls, ticker: str, data: dict) -> list:
        """醫낅ぉ ?곗씠?곕? ?뺤씤?섍퀬 議곌굔??留욎쑝硫??뚮┝???앹꽦?⑸땲??"""
        alerts = []
        
        # 媛?泥댄겕 濡쒖쭅???낅┰ ?⑥닔濡?遺꾨━?섏뿬 ?몄텧
        alerts.extend(cls._check_volatility(ticker, data))
        alerts.extend(cls._check_rsi(ticker, data))
        alerts.extend(cls._check_undervalued(ticker, data))
        alerts.extend(cls._check_ma_crossover(ticker, data))
        
        # ?꾩옱 ?곗씠?곕? ?댁쟾 ?곗씠?곕줈 ???(?ㅼ쓬 鍮꾧탳瑜??꾪빐)
        cls._save_current_state(ticker, data)
        
        return alerts

    @classmethod
    def _check_volatility(cls, ticker: str, data: dict) -> list:
        """1. 湲됰벑/湲됰씫 ?뚮┝ (Volatility)"""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        change_ratio = (price - prev_price) / prev_price * 100
        is_urgent = False
        msg = ""
        
        if change_ratio >= 2.5:
            msg = f"?뵦 **{ticker}** 1遺?????뭾 湲됰벑! (+{change_ratio:.1f}%) - ?꾩옱媛: ${price}"
            is_urgent = True
        elif change_ratio <= -2.5:
            msg = f"?슚 **{ticker}** 湲닿툒! ?⑤땳 ?留?媛먯? (-{change_ratio:.1f}%) - ?꾩옱媛: ${price}"
            is_urgent = True
            
        if is_urgent:
            try:
                news = NewsService.get_latest_news(ticker, limit=2)
                summary = NewsService.summarize_news(ticker, news)
                msg += f"\n\n?쨺 **Why? (愿???댁뒪)**\n{summary}"
            except:
                pass
            alerts.append(msg)
            
        return alerts

    @classmethod
    def _check_rsi(cls, ticker: str, data: dict) -> list:
        """2. RSI 怨쇰ℓ??怨쇰ℓ???뚮┝"""
        alerts = []
        rsi = data.get('rsi')
        if not rsi: return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_rsi" # ?쒓컙??1??
        
        if rsi < 30:
            if f"{alert_key}_oversold" not in cls._sent_alerts:
                alerts.append(f"?윟 **{ticker}** 以띿쨳 李ъ뒪! (RSI: {rsi:.1f}) - ?媛 留ㅼ닔 援ш컙")
                cls._sent_alerts.add(f"{alert_key}_oversold")
        elif rsi > 70:
            if f"{alert_key}_overbought" not in cls._sent_alerts:
                alerts.append(f"?뵶 **{ticker}** ?④린 怨쇱뿴! (RSI: {rsi:.1f}) - ?듭젅 怨좊젮")
                cls._sent_alerts.add(f"{alert_key}_overbought")
        
        return alerts

    @classmethod
    def _check_undervalued(cls, ticker: str, data: dict) -> list:
        """3. DCF ??됯? ?뚮┝"""
        alerts = []
        price = data.get('price')
        dcf = data.get('fair_value_dcf')
        
        if not (dcf and price and price < dcf * 0.8): return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_dcf"
        
        if f"{alert_key}_undervalued" not in cls._sent_alerts:
            upside = ((dcf - price) / price) * 100
            alerts.append(f"?뭿 **{ticker}** ??됯? ?곕웾二? ?곸젙媛 ${dcf:.2f} (?곸듅?щ젰 {upside:.1f}%)")
            cls._sent_alerts.add(f"{alert_key}_undervalued")
            
        return alerts

    @classmethod
    def _check_ma_crossover(cls, ticker: str, data: dict) -> list:
        """4. 吏吏??EMA) ?뚰뙆/?댄깉 ?뚮┝"""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        ema_list = [
            (data.get('ema5'), "EMA5(?④린)"), 
            (data.get('ema10'), "EMA10(?④린)"), 
            (data.get('ema20'), "EMA20(?앸챸??"), 
            (data.get('ema60'), "EMA60(?섍툒??"), 
            (data.get('ema120'), "EMA120(寃쎄린??"), 
            (data.get('ema200'), "EMA200(異붿꽭??")
        ]
        
        for ema_val, name in ema_list:
            if not ema_val: continue
            prev_ema = prev.get(name.split('(')[0].lower()) or ema_val
            
            # 怨⑤뱺?щ줈??
            if prev_price <= prev_ema and price > ema_val:
                alerts.append(f"?? **{ticker}** {name} ?곹뼢 ?뚰뙆! (吏吏?? ${ema_val:.2f}, ?꾩옱媛: ${price})")
            
            # ?곕뱶?щ줈??
            elif prev_price >= prev_ema and price < ema_val:
                alerts.append(f"?좑툘 **{ticker}** {name} ?섑뼢 ?댄깉! (吏吏?? ${ema_val:.2f}, ?꾩옱媛: ${price})")
                
        return alerts

    @classmethod
    def _save_current_state(cls, ticker: str, data: dict):
        """?꾩옱 ?곹깭瑜????(?ㅼ쓬 ??鍮꾧탳??"""
        cls._prev_data[ticker] = {
            'price': data.get('price'),
            'ema5': data.get('ema5'),
            'ema10': data.get('ema10'),
            'ema20': data.get('ema20'),
            'ema60': data.get('ema60'),
            'ema120': data.get('ema120'),
            'ema200': data.get('ema200')
        }
