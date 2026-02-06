import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from .data_service import DataService
from .financial_service import FinancialService
from .ticker_service import TickerService

class SchedulerService:
    _scheduler = None
    _top_20_tickers = []
    _price_cache = {}
    _dcf_cache = {}  # DCF는 별도 캐시 (느리므로)

    @classmethod
    def start(cls):
        if cls._scheduler is None:
            cls._scheduler = BackgroundScheduler()
            cls._scheduler.add_job(cls.update_top_20_list, 'interval', hours=24, next_run_time=datetime.now())
            cls._scheduler.add_job(cls.update_prices, 'interval', minutes=1, next_run_time=datetime.now())
            # DCF는 30분마다 (크롤링이 느리므로)
            cls._scheduler.add_job(cls.update_dcf_valuations, 'interval', minutes=30, next_run_time=datetime.now())
            cls._scheduler.start()
            print("📅 Scheduler started: Top 20 monitoring active.")


    @classmethod
    def update_top_20_list(cls):
        print("🔄 Updating US Top 20 Market Cap list...")
        try:
            cls._top_20_tickers = [
                'AAPL', 'NVDA', 'MSFT', 'AMZN', 'GOOGL', 'META', 'TSLA', 'BRK-B', 'AVGO', 'LLY',
                'JPM', 'XOM', 'V', 'UNH', 'MA', 'PG', 'COST', 'JNJ', 'HD', 'WMT'
            ]
            print(f"✅ Top 20 list updated: {cls._top_20_tickers}")
        except Exception as e:
            print(f"❌ Failed to update Top 20 list: {e}")

    @classmethod
    def calculate_ema(cls, series, period):
        return series.ewm(span=period, adjust=False).mean()

    @classmethod
    def calculate_rsi(cls, series, period=14):
        delta = series.diff(1)
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @classmethod
    def update_prices(cls):
        if not cls._top_20_tickers:
            return
            
        print(f"⏳ Fetching prices & indicators for {len(cls._top_20_tickers)} stocks...")
        for ticker in cls._top_20_tickers:
            try:
                # 최근 250일 데이터 (EMA200 계산 위해)
                df = DataService.get_price_data(ticker, start_date="2025-01-01")
                if df is None or df.empty:
                    continue
                
                # 기본 가격 정보
                current_price = float(df['Close'].iloc[-1])
                prev_open = float(df['Open'].iloc[-1])
                prev_close = float(df['Close'].iloc[-2]) if len(df) > 1 else current_price
                prev_high = float(df['High'].iloc[-1])
                prev_low = float(df['Low'].iloc[-1])
                
                # RSI 계산
                rsi = cls.calculate_rsi(df['Close']).iloc[-1]
                
                # EMA 계산
                ema5 = cls.calculate_ema(df['Close'], 5).iloc[-1]
                ema10 = cls.calculate_ema(df['Close'], 10).iloc[-1]
                ema20 = cls.calculate_ema(df['Close'], 20).iloc[-1]
                ema60 = cls.calculate_ema(df['Close'], 60).iloc[-1]
                ema100 = cls.calculate_ema(df['Close'], 100).iloc[-1]
                ema200 = cls.calculate_ema(df['Close'], 200).iloc[-1] if len(df) >= 200 else None
                
                # 적정주가: DCF 캐시가 있으면 사용, 없으면 EMA200
                dcf_data = cls._dcf_cache.get(ticker, {})
                fair_value_dcf = dcf_data.get('dcf_price')
                fair_value_ema = ema200 if ema200 else ema100
                
                cls._price_cache[ticker] = {
                    "price": round(current_price, 2),
                    "open": round(prev_open, 2),
                    "prev_close": round(prev_close, 2),
                    "high": round(prev_high, 2),
                    "low": round(prev_low, 2),
                    "fair_value_ema200": round(fair_value_ema, 2) if fair_value_ema else None,
                    "fair_value_dcf": round(fair_value_dcf, 2) if fair_value_dcf else None,
                    "dcf_method": dcf_data.get('method'),
                    "rsi": round(rsi, 2) if not np.isnan(rsi) else None,
                    "ema5": round(ema5, 2),
                    "ema10": round(ema10, 2),
                    "ema20": round(ema20, 2),
                    "ema60": round(ema60, 2) if len(df) >= 60 else None,
                    "ema100": round(ema100, 2) if len(df) >= 100 else None,
                    "ema200": round(ema200, 2) if ema200 else None,
                    "change_pct": round(((current_price - prev_close) / prev_close) * 100, 2),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

            except Exception as e:
                print(f"  Error fetching {ticker}: {e}")
        print("✅ Price & indicator update complete.")

    @classmethod
    def get_cached_price(cls, ticker):
        return cls._price_cache.get(ticker)
    
    @classmethod
    def get_all_cached_prices(cls):
        return cls._price_cache

    @classmethod
    def update_dcf_valuations(cls):
        """DCF 기반 적정주가 계산 (30분마다)"""
        if not cls._top_20_tickers:
            return
            
        print(f"💰 Calculating DCF valuations for {len(cls._top_20_tickers)} stocks...")
        for ticker in cls._top_20_tickers:
            try:
                # Yahoo Finance용 티커로 변환
                yahoo_ticker = TickerService.get_yahoo_ticker(ticker) if ticker.isdigit() else ticker
                
                # DCF 데이터 수집
                data = FinancialService.get_dcf_data(yahoo_ticker)
                fcf_per_share = data.get('fcf_per_share')
                beta = data.get('beta')
                growth_rate = data.get('growth_rate', 0.05)
                
                if not fcf_per_share or fcf_per_share < 0:
                    cls._dcf_cache[ticker] = {
                        "dcf_price": None,
                        "method": "N/A (데이터 부족 또는 적자)"
                    }
                    continue
                
                # DCF 계산 (10년 예측 + Gordon Growth Model)
                risk_free_rate = 0.045  # 10년 국채 수익률 4.5%
                equity_risk_premium = 0.055  # 주식 리스크 프리미엄 5.5%
                
                if beta:
                    # CAPM: Cost of Equity
                    discount_rate = risk_free_rate + beta * equity_risk_premium
                else:
                    discount_rate = 0.10
                
                # 할인율 최소/최대 제한 (8% ~ 15%)
                discount_rate = max(0.08, min(0.15, discount_rate))
                    
                terminal_growth_rate = 0.03  # 영구 성장률 3%
                
                # Stage 1: 10년 고성장 (주당 FCF 기준)
                future_fcf = []
                current_fcf = fcf_per_share
                for i in range(1, 11):
                    # 성장률 점진적 감소 (10년차에는 terminal growth에 수렴)
                    year_growth = growth_rate - (growth_rate - terminal_growth_rate) * (i / 10)
                    current_fcf = current_fcf * (1 + year_growth)
                    discounted_fcf = current_fcf / ((1 + discount_rate) ** i)
                    future_fcf.append(discounted_fcf)
                    
                # Stage 2: Terminal Value (Gordon Growth Model)
                terminal_value = (current_fcf * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
                discounted_terminal_value = terminal_value / ((1 + discount_rate) ** 10)
                
                dcf_price = sum(future_fcf) + discounted_terminal_value


                
                cls._dcf_cache[ticker] = {
                    "dcf_price": dcf_price,
                    "method": f"DCF(성장률 {growth_rate*100:.1f}%, 할인율 {discount_rate*100:.1f}%)",
                    "fcf_per_share": fcf_per_share,
                    "beta": beta,
                    "growth_rate": growth_rate
                }
                print(f"  ✅ {ticker}: DCF ${dcf_price:.2f}")
                
            except Exception as e:
                print(f"  ❌ {ticker} DCF error: {e}")
                cls._dcf_cache[ticker] = {"dcf_price": None, "method": f"Error: {str(e)[:50]}"}
                
        print("💰 DCF valuation update complete.")


