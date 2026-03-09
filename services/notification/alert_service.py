from typing import Optional, List
import requests
from config import Config
from services.market.news_service import NewsService
from models.schemas import PriceAlert
from services.market.data_service import DataService
from services.market.market_data_service import MarketDataService
from utils.logger import get_logger

logger = get_logger("alert_service")

class AlertService:
    """
    Slack notification and user alert service (Refactored)
    """
    _webhook_url: Optional[str] = None
    _sent_alerts = set()  # Duplicate alert prevention
    _prev_data = {}  # {ticker: {price, ema20, ...}}
    _pending_alerts = [] # Agent send queue
    _user_alerts: List[PriceAlert] = [] # User-configured price alerts
    
    @classmethod
    def set_webhook(cls, webhook_url: str):
        cls._webhook_url = webhook_url
    
    # Keywords to block in dev mode (buy/sell execution alerts)
    _DEV_BLOCK_KEYWORDS = (
        "BUY Executed", "SELL Executed",  # trade execution alerts
        "Tick Trade",                      # tick trade alerts
        "split_buy", "split_sell",         # split orders
        "take_profit", "stop_loss",        # P&L execution
        "rebalance", "Rebalancing",        # sector rebalancing
        "KIS order",                       # KIS API orders
    )

    @classmethod
    def send_slack_alert(cls, message: str, channel: str = None) -> bool:
        """Send actual notification to Slack."""
        # Dev mode: block all Slack sends (including trades and reports)
        if Config.DEV_MODE:
            logger.info(f"[DEV MODE] Slack send blocked → {message[:80]}...")
            return False

        webhook_url = cls._webhook_url or Config.SLACK_WEBHOOK_URL
        if not webhook_url:
            print(f"⚠️ Slack Webhook URL not configured. Log: {message}")
            return False

        try:
            payload = {"text": message}
            response = requests.post(webhook_url, json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"✅ Slack message sent successfully.")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send Slack alert: {e}")
            return False

    @classmethod
    def get_pending_alerts(cls) -> list:
        """Return pending alerts and clear the queue."""
        alerts = list(cls._pending_alerts)
        cls._pending_alerts.clear()
        return alerts

    @classmethod
    def add_user_alert(cls, alert: PriceAlert):
        """Add user alert (includes automatic ticker name resolution)."""
        from services.market.ticker_service import TickerService
        resolved = TickerService.resolve_ticker(alert.ticker)
        if resolved:
            alert.ticker = resolved
        cls._user_alerts.append(alert)

    @classmethod
    def check_user_alerts(cls) -> List[str]:
        """Check user-configured alerts."""
        triggered = []
        all_states = MarketDataService.get_all_states()
        for alert in cls._user_alerts:
            if not alert.is_active:
                continue

            state = all_states.get(alert.ticker)
            current_price = getattr(state, 'current_price', None) if state else DataService.get_current_price(alert.ticker)
            if current_price:
                if alert.condition == "above" and current_price >= alert.target_price:
                    triggered.append(f"🔔 {alert.ticker} reached! Current: {current_price} >= Target: {alert.target_price}")
                elif alert.condition == "below" and current_price <= alert.target_price:
                    triggered.append(f"🔔 {alert.ticker} reached! Current: {current_price} <= Target: {alert.target_price}")
        return triggered
    
    @classmethod
    def check_and_alert(cls, ticker: str, data: dict) -> list:
        """Check ticker data and generate alerts if conditions are met."""
        alerts = []
        
        # Each check logic is separated into independent functions
        alerts.extend(cls._check_volatility(ticker, data))
        alerts.extend(cls._check_rsi(ticker, data))
        alerts.extend(cls._check_undervalued(ticker, data))
        alerts.extend(cls._check_ma_crossover(ticker, data))
        
        # Save current data as previous data (for next comparison)
        cls._save_current_state(ticker, data)
        
        return alerts

    @classmethod
    def generate_daily_summary(cls, data: dict) -> str:
        """Generate a current market summary report."""
        if not data:
            return "Analysis data has not been collected yet."
            
        summary = "📊 **Real-time Market Analysis Summary**\n\n"
        
        oversold_tickers = [ticker for ticker, info in data.items() if info.get("rsi", 50) < 35]
        if oversold_tickers:
            summary += "🔵 **RSI Oversold (Buy Opportunity)**:\n"
            for ticker in oversold_tickers[:5]:
                summary += f"- {ticker}: RSI {data[ticker]['rsi']:.1f}\n"
        overbought_tickers = [ticker for ticker, info in data.items() if info.get("rsi", 50) > 65]
        if overbought_tickers:
            summary += "\n🔴 **RSI Overbought (Short-term Overheated)**:\n"
            for ticker in overbought_tickers[:5]:
                summary += f"- {ticker}: RSI {data[ticker]['rsi']:.1f}\n"
        gainers = sorted(data.items(), key=lambda item: item[1].get("change_pct", 0), reverse=True)[:5]
        summary += "\n📈 **Real-time Top 5 Gainers**:\n"
        for ticker, info in gainers:
            summary += f"- {ticker}: {info['change_pct']:+.2f}% (${info['price']})\n"
            
        return summary

    @classmethod
    def _check_volatility(cls, ticker: str, data: dict) -> list:
        """1. Surge/plunge alert (Volatility)."""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        change_ratio = (price - prev_price) / prev_price * 100
        is_urgent = False
        msg = ""
        
        if change_ratio >= 2.5:
            msg = f"🚀 **{ticker}** surged in 1 min! (+{change_ratio:.1f}%) - Current: ${price}"
            is_urgent = True
        elif change_ratio <= -2.5:
            msg = f"📉 **{ticker}** Alert! Panic sell detected (-{change_ratio:.1f}%) - Current: ${price}"
            is_urgent = True
            
        if is_urgent:
            try:
                news = NewsService.get_latest_news(ticker, limit=2)
                summary = NewsService.summarize_news(ticker, news)
                msg += f"\n\n📰 **Why? (Related News)**\n{summary}"
            except:
                pass
            alerts.append(msg)
            
        return alerts

    @classmethod
    def _check_rsi(cls, ticker: str, data: dict) -> list:
        """2. RSI overbought/oversold alert."""
        alerts = []
        rsi = data.get('rsi')
        if not rsi: return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_rsi" # Once per hour
        
        if rsi < 30:
            if f"{alert_key}_oversold" not in cls._sent_alerts:
                alerts.append(f"💎 **{ticker}** Bargain opportunity! (RSI: {rsi:.1f}) - Buy zone")
                cls._sent_alerts.add(f"{alert_key}_oversold")
        elif rsi > 70:
            if f"{alert_key}_overbought" not in cls._sent_alerts:
                alerts.append(f"🔥 **{ticker}** Short-term overheated! (RSI: {rsi:.1f}) - Consider taking profit")
                cls._sent_alerts.add(f"{alert_key}_overbought")
        
        return alerts

    @classmethod
    def _check_undervalued(cls, ticker: str, data: dict) -> list:
        """3. DCF undervaluation alert."""
        alerts = []
        price = data.get('price')
        dcf = data.get('fair_value_dcf')
        
        if not (dcf and price and price < dcf * 0.8): return []
        
        alert_key = f"{ticker}_{data.get('time', '')[:13]}_dcf"
        
        if f"{alert_key}_undervalued" not in cls._sent_alerts:
            upside = ((dcf - price) / price) * 100
            alerts.append(f"🎁 **{ticker}** Undervalued quality stock! Fair value ${dcf:.2f} (upside {upside:.1f}%)")
            cls._sent_alerts.add(f"{alert_key}_undervalued")
            
        return alerts

    @classmethod
    def _check_ma_crossover(cls, ticker: str, data: dict) -> list:
        """4. Support line (EMA) breakout/breakdown alert."""
        alerts = []
        price = data.get('price')
        prev = cls._prev_data.get(ticker, {})
        prev_price = prev.get('price')
        
        if not (prev_price and price): return []
        
        ema_list = [
            (data.get('ema5'), "EMA5(short)"),
            (data.get('ema10'), "EMA10(short)"),
            (data.get('ema20'), "EMA20(lifeline)"),
            (data.get('ema60'), "EMA60(supply)"),
            (data.get('ema120'), "EMA120(cycle)"),
            (data.get('ema200'), "EMA200(trend)")
        ]
        
        for ema_val, name in ema_list:
            if not ema_val: continue
            prev_ema = prev.get(name.split('(')[0].lower()) or ema_val
            
            # Golden cross
            if prev_price <= prev_ema and price > ema_val:
                alerts.append(f"✨ **{ticker}** {name} breakout above! (Support: ${ema_val:.2f}, Current: ${price})")
            
            # Dead cross
            elif prev_price >= prev_ema and price < ema_val:
                alerts.append(f"🚨 **{ticker}** {name} breakdown below! (Support: ${ema_val:.2f}, Current: ${price})")
                
        return alerts

    @classmethod
    def _save_current_state(cls, ticker: str, data: dict):
        """Save current state (for next comparison)."""
        cls._prev_data[ticker] = {
            'price': data.get('price'),
            'ema5': data.get('ema5'),
            'ema10': data.get('ema10'),
            'ema20': data.get('ema20'),
            'ema60': data.get('ema60'),
            'ema120': data.get('ema120'),
            'ema200': data.get('ema200')
        }
