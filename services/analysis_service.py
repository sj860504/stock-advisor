import yfinance as yf
import pandas as pd
from datetime import datetime
from services.data_service import DataService
from services.financial_service import FinancialService
from services.macro_service import MacroService
from services.portfolio_service import PortfolioService
from services.news_service import NewsService
from services.indicator_service import IndicatorService
from services.report_service import ReportService

class AnalysisService:
    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> dict:
        """
        ?섎굹???곗빱?????紐⑤뱺 遺꾩꽍 湲곕뒫???듯빀?섏뿬 由ы룷?몃? ?앹꽦?⑸땲??
        (?곗씠???섏쭛 諛?怨꾩궛 濡쒖쭅 吏묒쨷)
        """
        print(f"?? Generating comprehensive report for {ticker}...")
        
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 1. ?쒖꽭 諛?湲곕낯 ?뺣낫 (Webull Style)
            reg_price = info.get('regularMarketPrice')
            pre_price = info.get('preMarketPrice')
            prev_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
            
            curr_price = pre_price if pre_price else reg_price
            change_pct = ((curr_price - prev_close) / prev_close * 100) if prev_close else 0
            
            # 2. ?섏쓽 ?ы듃?대━???꾪솴
            holdings = PortfolioService.load_portfolio(user_id)
            my_stock = next((h for h in holdings if h['ticker'] == ticker), None)
            
            # 3. 湲곗닠??吏??(IndicatorService ?쒖슜)
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

            # 4. 媛移??됯? (Macro 湲덈━ 諛섏쁺 DCF)
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

            # 5. 湲곌? 紐⑺몴媛
            analyst_target = info.get('targetMeanPrice')
            
            # 6. 理쒖떊 ?댁뒪 ?붿빟
            news = NewsService.get_latest_news(ticker, limit=2)
            news_summary = NewsService.summarize_news(ticker, news)

            # 7. 醫낇빀 ?곗씠??援ъ꽦
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
        """?곗씠?곕? ?앹꽦?섍퀬 ?щ㎎?낃퉴吏 ?꾨즺??臾몄옄?댁쓣 諛섑솚?⑸땲??"""
        data = cls.get_comprehensive_report(ticker)
        return ReportService.format_comprehensive_report(data)
