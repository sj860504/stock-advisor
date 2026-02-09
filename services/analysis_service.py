import yfinance as yf
import pandas as pd
from datetime import datetime
from stock_advisor.services.data_service import DataService
from stock_advisor.services.financial_service import FinancialService
from stock_advisor.services.macro_service import MacroService
from stock_advisor.services.portfolio_service import PortfolioService
from stock_advisor.services.news_service import NewsService
from stock_advisor.services.indicator_service import IndicatorService
from stock_advisor.services.report_service import ReportService

class AnalysisService:
    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> dict:
        """
        하나의 티커에 대해 모든 분석 기능을 통합하여 리포트를 생성합니다.
        (데이터 수집 및 계산 로직 집중)
        """
        print(f"🚀 Generating comprehensive report for {ticker}...")
        
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 1. 시세 및 기본 정보 (Webull Style)
            reg_price = info.get('regularMarketPrice')
            pre_price = info.get('preMarketPrice')
            prev_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
            
            curr_price = pre_price if pre_price else reg_price
            change_pct = ((curr_price - prev_close) / prev_close * 100) if prev_close else 0
            
            # 2. 나의 포트폴리오 현황
            holdings = PortfolioService.load_portfolio(user_id)
            my_stock = next((h for h in holdings if h['ticker'] == ticker), None)
            
            # 3. 기술적 지표 (IndicatorService 활용)
            hist = stock.history(period="2y")
            
            if not hist.empty:
                indicators = IndicatorService.get_latest_indicators(hist['Close'])
                rsi = indicators.get('rsi')
                emas = {k: v for k, v in indicators.items() if k.startswith('ema')}
                
                # Bollinger Bands
                bb = IndicatorService.calculate_bollinger_bands(hist['Close'])
                bb_latest = {k: round(v.iloc[-1], 2) for k, v in bb.items()}
            else:
                rsi = 50
                emas = {}
                bb_latest = {}

            # 4. 가치 평가 (Macro 금리 반영 DCF)
            macro = MacroService.get_macro_data()
            risk_free = macro['us_10y_yield'] / 100
            
            dcf_data = FinancialService.get_dcf_data(ticker)
            fcf = dcf_data.get('fcf_per_share')
            dcf_fair = "N/A"
            if fcf and fcf > 0:
                growth = dcf_data.get('growth_rate', 0.05)
                beta = dcf_data.get('beta', 1.0)
                disc = max(0.06, risk_free + beta * 0.055)
                
                val = 0
                temp_fcf = fcf
                for i in range(1, 11):
                    temp_fcf *= (1+growth)
                    val += temp_fcf / ((1+disc)**i)
                term = (temp_fcf * 1.03) / (disc - 0.03)
                val += term / ((1+disc)**10)
                dcf_fair = round(val, 2)

            # 5. 기관 목표가
            analyst_target = info.get('targetMeanPrice')
            
            # 6. 최신 뉴스 요약
            news = NewsService.get_latest_news(ticker, limit=2)
            news_summary = NewsService.summarize_news(ticker, news)

            # 7. 종합 데이터 구성
            report = {
                "ticker": ticker,
                "name": info.get('shortName', ticker),
                "price_info": {
                    "current": round(curr_price, 2),
                    "change_pct": round(change_pct, 2),
                    "state": info.get('marketState')
                },
                "portfolio": {
                    "owned": True if my_stock else False,
                    "avg_cost": my_stock['buy_price'] if my_stock else 0,
                    "return_pct": round(((curr_price - my_stock['buy_price'])/my_stock['buy_price']*100), 2) if my_stock else 0
                },
                "technical": {
                    "rsi": rsi,
                    "emas": emas,
                    "bollinger": bb_latest
                },
                "fundamental": {
                    "dcf_fair": dcf_fair,
                    "upside_dcf": round((dcf_fair - curr_price)/curr_price*100, 1) if dcf_fair != "N/A" else 0,
                    "analyst_target": analyst_target,
                    "upside_analyst": round((analyst_target - curr_price)/curr_price*100, 1) if analyst_target else 0
                },
                "macro_context": {
                    "regime": macro['market_regime']['status'],
                    "vix": macro['vix']
                },
                "news_summary": news_summary
            }
            
            return report
            
        except Exception as e:
            print(f"Error generating comprehensive report: {e}")
            return {"error": str(e)}

    @classmethod
    def get_formatted_report(cls, ticker: str) -> str:
        """데이터를 생성하고 포맷팅까지 완료된 문자열을 반환합니다."""
        data = cls.get_comprehensive_report(ticker)
        return ReportService.format_comprehensive_report(data)
