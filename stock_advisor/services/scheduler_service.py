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
from stock_advisor.utils.logger import get_logger
from stock_advisor.services.dcf_service import DcfService

logger = get_logger("scheduler")

class SchedulerService:
    _scheduler = None
    _top_20_tickers = []
    _price_cache = {}
    _dcf_cache = {}

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            # 서버 시작 시 즉시 Top 20 업데이트
            cls.update_top_20_list()
            
            cls._scheduler.add_job(cls.update_top_20_list, 'interval', hours=24)
            cls._scheduler.add_job(cls.update_prices, 'interval', minutes=1, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.update_dcf_valuations, 'interval', minutes=30, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.check_portfolio_hourly, 'interval', minutes=60, next_run_time=datetime.now())
            cls._scheduler.start()
            logger.info("📅 Scheduler started.")

    @classmethod
    def get_all_cached_prices(cls):
        """캐시된 모든 시세 데이터를 반환"""
        return cls._price_cache

    @classmethod
    def update_top_20_list(cls):
        """시가총액 상위 종목 리스트 갱신"""
        try:
            tickers = DataService.get_top_market_cap_tickers(limit=20)
            if tickers:
                cls._top_20_tickers = tickers
                logger.info(f"✅ Top 20 list updated: {tickers}")
            else:
                logger.warning("⚠️ Failed to update Top 20 list, keeping old list.")
        except Exception as e:
            logger.error(f"❌ Error updating top 20 list: {e}")

    @classmethod
    def update_prices(cls):
        """실시간 시세 및 지표 업데이트"""
        # Top 20 + 포트폴리오 보유 종목 합치기
        targets = set(cls._top_20_tickers)
        try:
            holdings = PortfolioService.load_portfolio('sean')
            for item in holdings:
                if item.get('ticker'):
                    targets.add(item['ticker'])
        except: pass
        
        if not targets: return
        
        import yfinance as yf
        
        for ticker in list(targets):
            try:
                # 1. 과거 데이터로 지표 계산 (DataService 사용)
                # EMA200 계산을 위해 최소 300일 이전 데이터부터 가져옴
                df = DataService.get_price_data(ticker, start_date="2024-01-01")
                indicators = {}
                if df is not None and not df.empty:
                    indicators = IndicatorService.get_latest_indicators(df['Close'])

                # 2. 실시간 데이터 (yfinance 사용)
                # DataService에서 가져온 값은 지연되거나 종가 기준일 수 있으므로 yfinance 실시간 데이터 우선 사용
                stock = yf.Ticker(ticker)
                
                # fast_info가 더 빠르고 정확할 때가 많음
                current_price = stock.fast_info.last_price
                prev_close = stock.fast_info.previous_close
                
                # 상세 정보 (프리장 등)
                info = stock.info
                market_state = info.get('marketState', 'REGULAR')
                
                change = 0
                change_pct = 0
                
                if current_price and prev_close:
                    change = current_price - prev_close
                    change_pct = ((current_price - prev_close) / prev_close) * 100

                # 프리장 데이터
                pre_price = info.get('preMarketPrice')
                pre_change_pct = 0
                
                if pre_price:
                    # 프리장 등락률은 정규장 종가 대비로 계산
                    reg_close = info.get('regularMarketPreviousClose') or prev_close
                    if reg_close:
                        pre_change_pct = ((pre_price - reg_close) / reg_close) * 100

                dcf_data = cls._dcf_cache.get(ticker, {})
                fair_value_dcf = dcf_data.get('dcf_price')
                
                # 데이터 통합
                price_data = {
                    "price": current_price,
                    "change": round(change, 2),
                    "change_pct": round(change_pct, 2),
                    "pre_price": pre_price,
                    "pre_change_pct": round(pre_change_pct, 2) if pre_price else None,
                    "market_state": market_state,
                    "fair_value_dcf": fair_value_dcf,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    **indicators
                }
                
                cls._price_cache[ticker] = price_data
                
                # 알림 체크
                alerts = AlertService.check_and_alert(ticker, price_data)
                for alert_msg in alerts:
                    AlertService.send_slack_alert(alert_msg)

            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")

    @classmethod
    def check_portfolio_hourly(cls):
        """보유 종목 중 상승 종목 리포트 (Webull 스타일)"""
        logger.info("⏰ Checking portfolio gainers...")
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
                logger.info(f"✅ Sent report for {len(gainers)} gainers.")
                
        except Exception as e:
            logger.error(f"❌ Portfolio check error: {e}")

    @classmethod
    def update_dcf_valuations(cls):
        if not cls._top_20_tickers: return
        logger.info(f"💰 Calculating DCF for {len(cls._top_20_tickers)} stocks...")
        
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
