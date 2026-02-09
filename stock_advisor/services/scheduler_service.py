import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from stock_advisor.services.data_service import DataService
from stock_advisor.services.financial_service import FinancialService
from stock_advisor.services.ticker_service import TickerService
from stock_advisor.services.alert_service import AlertService
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.macro_service import MacroService
from stock_advisor.services.indicator_service import IndicatorService
from stock_advisor.services.dcf_service import DcfService

class SchedulerService:
    _scheduler = None
    _top_20_tickers = []
    _price_cache = {}
    _dcf_cache = {}

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            cls._scheduler.add_job(cls.update_top_20_list, 'interval', hours=24, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.update_prices, 'interval', minutes=1, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.update_dcf_valuations, 'interval', minutes=30, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', minutes=60, next_run_time=datetime.now())
            cls._scheduler.start()
            print("📅 Scheduler started.")

    @classmethod
    def get_all_cached_prices(cls):
        """캐시된 모든 시세 데이터를 반환"""
        return cls._price_cache

    @classmethod
    def update_top_20_list(cls):
        try:
            cls._top_20_tickers = [
                'AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'AVGO', 'LLY',
                'JPM', 'XOM', 'V', 'UNH', 'MA', 'PG', 'COST', 'JNJ', 'HD', 'WMT'
            ]
        except: pass

    @classmethod
    def update_prices(cls):
        """실시간 시세 및 지표 업데이트"""
        if not cls._top_20_tickers: return
        
        for ticker in cls._top_20_tickers:
            try:
                df = DataService.get_price_data(ticker, start_date="2025-01-01")
                if df is None or df.empty: continue
                
                current_price = float(df['Close'].iloc[-1])
                
                # IndicatorService를 사용하여 지표 계산 위임
                indicators = IndicatorService.get_latest_indicators(df['Close'])
                
                dcf_data = cls._dcf_cache.get(ticker, {})
                fair_value_dcf = dcf_data.get('dcf_price')
                
                # 데이터 통합
                price_data = {
                    "price": current_price,
                    "fair_value_dcf": fair_value_dcf,
                    "change_pct": 0,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    **indicators
                }
                
                cls._price_cache[ticker] = price_data
                
                # 알림 체크
                alerts = AlertService.check_and_alert(ticker, price_data)
                for alert_msg in alerts:
                    AlertService.send_slack_alert(alert_msg)

            except Exception as e:
                print(f"Error fetching {ticker}: {e}")

    @classmethod
    def check_portfolio_hourly(cls):
        """보유 종목 중 상승 종목 리포트 (Webull 스타일)"""
        print("⏰ Checking portfolio gainers...")
        try:
            macro = MacroService.get_macro_data()
            holdings = PortfolioService.load_portfolio('sean')
            if not holdings: return

            gainers = []
            
            for item in holdings:
                ticker = item['ticker']
                if not ticker: continue
                if not ticker.isascii() or any(x in ticker for x in ['ACE', 'TIGER', 'KODEX']):
                    continue
                
                current_price = 0
                change_pct = 0
                market_state = "Regular"
                company_name = item.get('name') or ticker
                
                try:
                    import yfinance as yf
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    
                    market_state = info.get('marketState', 'REGULAR')
                    
                    reg_price = info.get('regularMarketPrice') or stock.fast_info.last_price
                    pre_price = info.get('preMarketPrice')
                    
                    if (market_state in ['PRE', 'POST', 'PREPRE']) and pre_price and reg_price:
                        current_price = pre_price
                        market_state = "Pre-market"
                        change_pct = ((pre_price - reg_price) / reg_price) * 100
                    else:
                        current_price = reg_price
                        prev_close = info.get('regularMarketPreviousClose') or stock.fast_info.previous_close
                        if prev_close:
                            change_pct = ((current_price - prev_close) / prev_close) * 100

                    if company_name == ticker:
                        company_name = info.get('shortName') or info.get('longName') or ticker
                        
                except:
                    continue
                
                if change_pct > 0:
                    gainers.append({
                        'ticker': ticker,
                        'name': company_name,
                        'price': current_price,
                        'change': change_pct,
                        'market': market_state
                    })
            
            if gainers:
                gainers.sort(key=lambda x: x['change'], reverse=True)
                
                # 리포트 포맷팅 위임
                from stock_advisor.services.report_service import ReportService
                msg = ReportService.format_hourly_gainers(gainers, macro)
                
                AlertService.send_slack_alert(msg)
                print(f"✅ Sent report for {len(gainers)} gainers.")
                
        except Exception as e:
            print(f"❌ Portfolio check error: {e}")

    @classmethod
    def update_dcf_valuations(cls):
        if not cls._top_20_tickers: return
        print(f"💰 Calculating DCF for {len(cls._top_20_tickers)} stocks...")
        
        macro = MacroService.get_macro_data()
        risk_free = macro['us_10y_yield'] / 100
        
        for ticker in cls._top_20_tickers:
            try:
                yahoo_ticker = TickerService.get_yahoo_ticker(ticker) if ticker.isdigit() else ticker
                data = FinancialService.get_dcf_data(yahoo_ticker)
                
                fcf = data.get('fcf_per_share')
                if not fcf or fcf < 0: continue
                
                result = DcfService.calculate_fair_value(
                    fcf_per_share=fcf,
                    growth_rate=data.get('growth_rate', 0.05),
                    beta=data.get('beta', 1.0),
                    risk_free_rate=risk_free
                )
                
                cls._dcf_cache[ticker] = {
                    "dcf_price": result['value'], 
                    "method": f"DCF(Rf {risk_free*100:.1f}%)"
                } 
            except: pass
