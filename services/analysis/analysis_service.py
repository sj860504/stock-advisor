import pandas as pd
from datetime import datetime
from services.market.data_service import DataService
from services.analysis.financial_service import FinancialService
from services.market.macro_service import MacroService
from services.trading.portfolio_service import PortfolioService
from services.market.news_service import NewsService
from services.analysis.indicator_service import IndicatorService
from services.notification.report_service import ReportService
from services.kis.kis_service import KisService
from services.kis.fetch.kis_fetcher import KisFetcher

class AnalysisService:
    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> dict:
        """
        하나의 티커에 대한 모든 분석 기능을 통합하여 리포트를 생성합니다.
        (데이터 수집 및 계산 로직 집중)
        """
        print(f"📊 Generating comprehensive report for {ticker}...")
        
        try:
            # 1. 시세 및 기본 정보 (KIS API 사용)
            token = KisService.get_access_token()
            if ticker.isdigit():
                price_data = KisFetcher.fetch_domestic_price(token, ticker)
            else:
                price_data = KisFetcher.fetch_overseas_price(token, ticker)
            
            if not price_data:
                return {"error": f"Failed to fetch price data for {ticker}"}

            curr_price = price_data.get('price', 0)
            change_pct = price_data.get('change_rate', 0)
            
            # 2. 나의 포트폴리오 현황
            holdings = PortfolioService.load_portfolio(user_id)
            my_stock = next((h for h in holdings if h['ticker'] == ticker), None)
            
            # 3. 기술적 지표 (DataService + IndicatorService 사용)
            hist = DataService.get_price_history(ticker, days=365*2) # 2년치
            
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

            # 5. 기관 목표가 (KIS API에서 가져온 상세 데이터 활용)
            # KIS API에서 analyst target을 제공하지 않는 경우 N/A 처리
            analyst_target = price_data.get('raw', {}).get('target_mean_price') # API마다 다를 수 있음
            
            # 6. 최신 뉴스 요약
            news = NewsService.get_latest_news(ticker, limit=2)
            news_summary = NewsService.summarize_news(ticker, news)

            # 7. 종합 데이터 구성
            report = {
                "ticker": ticker,
                "name": price_data.get('name', ticker),
                "price_info": {
                    "current": round(curr_price, 2),
                    "change_pct": round(change_pct, 2),
                    "state": price_data.get('raw', {}).get('market_state', 'OPEN')
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
            print(f"Error generating comprehensive report for {ticker}: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    @classmethod
    def get_formatted_report(cls, ticker: str) -> str:
        """데이터를 생성하고 포맷팅까지 완료한 문자열을 반환합니다."""
        data = cls.get_comprehensive_report(ticker)
        if "error" in data:
            return f"Error: {data['error']}"
        return ReportService.format_comprehensive_report(data)
