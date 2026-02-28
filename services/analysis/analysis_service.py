"""종합 분석 서비스. 티커별 시세·기술·기본·매크로·뉴스를 통합해 리포트를 생성합니다."""
from typing import Optional, Union

import pandas as pd

from models.schemas import (
    ComprehensiveReport,
    FundamentalSummaryInReport,
    MacroContextInReport,
    PortfolioSummaryInReport,
    PriceInfoSummary,
    TechnicalSummaryInReport,
)
from services.analysis.financial_service import FinancialService
from services.analysis.indicator_service import IndicatorService
from utils.market import is_kr
from services.kis.fetch.kis_fetcher import KisFetcher
from services.kis.kis_service import KisService
from services.market.data_service import DataService
from services.market.macro_service import MacroService
from services.market.news_service import NewsService
from services.notification.report_service import ReportService
from services.trading.portfolio_service import PortfolioService

# DCF 단순 계산 상수 (Macro 금리 반영)
DCF_EQUITY_RISK_PREMIUM = 0.055
DCF_RATE_FLOOR = 0.06
DCF_TERMINAL_GROWTH = 0.03
DCF_STAGE1_YEARS = 10
REPORT_HISTORY_DAYS = 365 * 2
REPORT_NEWS_LIMIT = 2


class AnalysisService:
    """티커 종합 분석 및 리포트 생성."""

    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> Optional[ComprehensiveReport]:
        """
        하나의 티커에 대한 시세·보유·기술·기본·매크로·뉴스를 통합해 리포트 모델을 반환합니다.
        """
        try:
            token = KisService.get_access_token()
            if is_kr(ticker):
                price_data = KisFetcher.fetch_domestic_price(token, ticker)
            else:
                price_data = KisFetcher.fetch_overseas_price(token, ticker)
            if not price_data:
                return None

            current_price = round(float(price_data.get("price", 0) or 0), 2)
            change_rate_pct = round(float(price_data.get("change_rate", 0) or 0), 2)
            raw_payload = price_data.get("raw") or {}
            market_state = raw_payload.get("market_state", "OPEN")

            holdings = PortfolioService.load_portfolio_dtos(user_id)
            holding_for_ticker = next((h for h in holdings if h.ticker == ticker), None)
            avg_cost = holding_for_ticker.buy_price if holding_for_ticker else 0
            return_pct = (
                round((current_price - avg_cost) / avg_cost * 100, 2)
                if holding_for_ticker and avg_cost
                else 0
            )

            hist = DataService.get_price_history(ticker, days=REPORT_HISTORY_DAYS)
            indicators_snapshot = (
                IndicatorService.compute_latest_indicators_snapshot(hist["Close"])
                if not hist.empty else None
            )
            rsi = indicators_snapshot.rsi if indicators_snapshot else 50
            emas = indicators_snapshot.ema if indicators_snapshot else {}
            if not hist.empty:
                bb_result = IndicatorService.compute_bollinger_bands(hist["Close"])
                bb_latest = bb_result.to_latest()
                bollinger_dict = {
                    "middle": bb_latest.middle,
                    "upper": bb_latest.upper,
                    "lower": bb_latest.lower,
                }
            else:
                bollinger_dict = {}

            macro_snapshot = MacroService.get_macro_data()
            risk_free_rate = float(macro_snapshot.get("us_10y_yield", 0) or 0) / 100
            dcf_data = FinancialService.get_dcf_data(ticker)
            fcf = dcf_data.fcf_per_share if dcf_data else None
            dcf_fair: Union[float, str] = "N/A"
            if dcf_data and fcf and fcf > 0:
                growth_rate = dcf_data.growth_rate
                beta = dcf_data.beta
                discount_rate = max(DCF_RATE_FLOOR, risk_free_rate + beta * DCF_EQUITY_RISK_PREMIUM)
                dcf_sum = 0.0
                projected_fcf = fcf
                for i in range(1, DCF_STAGE1_YEARS + 1):
                    projected_fcf *= 1 + growth_rate
                    dcf_sum += projected_fcf / ((1 + discount_rate) ** i)
                terminal_value = (projected_fcf * (1 + DCF_TERMINAL_GROWTH)) / (
                    discount_rate - DCF_TERMINAL_GROWTH
                )
                dcf_sum += terminal_value / ((1 + discount_rate) ** DCF_STAGE1_YEARS)
                dcf_fair = round(dcf_sum, 2)

            analyst_target = raw_payload.get("target_mean_price")
            upside_dcf = (
                round((dcf_fair - current_price) / current_price * 100, 1)
                if dcf_fair != "N/A" and current_price else 0
            )
            upside_analyst = (
                round((analyst_target - current_price) / current_price * 100, 1)
                if analyst_target and current_price else 0
            )

            news_items = NewsService.get_latest_news(ticker, limit=REPORT_NEWS_LIMIT)
            news_summary = NewsService.summarize_news(ticker, news_items)
            regime_status = (macro_snapshot.get("market_regime") or {}).get("status", "")
            vix = macro_snapshot.get("vix")

            # 매매 점수 계산 — 이미 수집한 지표로 TickerState 구성 후 calculate_score 호출
            score, score_reasons = None, []
            try:
                from services.strategy.trading_strategy_service import TradingStrategyService
                from services.market.market_data_service import MarketDataService
                from models.ticker_state import TickerState
                # 인메모리 캐시 우선, 없으면 현재 데이터로 직접 구성
                state = MarketDataService.get_all_states().get(ticker)
                if not state:
                    state = TickerState(
                        ticker=ticker,
                        current_price=current_price,
                        change_rate=change_rate_pct,
                        rsi=rsi,
                        ema=emas,
                        bollinger=bollinger_dict,
                        dcf_value=dcf_fair if isinstance(dcf_fair, float) else 0.0,
                    )
                holding_dict = holding_for_ticker.model_dump() if holding_for_ticker else None
                cash = PortfolioService.load_cash(user_id)
                total_assets = cash
                score, score_reasons = TradingStrategyService.calculate_score(
                    ticker, state, holding_dict, macro_snapshot, {}, total_assets, cash
                )
            except Exception:
                pass

            return ComprehensiveReport(
                ticker=ticker,
                name=price_data.get("name", ticker),
                price_info=PriceInfoSummary(
                    current=current_price,
                    change_pct=change_rate_pct,
                    state=market_state,
                ),
                portfolio=PortfolioSummaryInReport(
                    owned=bool(holding_for_ticker),
                    avg_cost=avg_cost,
                    return_pct=return_pct,
                ),
                technical=TechnicalSummaryInReport(rsi=rsi, emas=emas, bollinger=bollinger_dict),
                fundamental=FundamentalSummaryInReport(
                    dcf_fair=dcf_fair,
                    upside_dcf=upside_dcf,
                    analyst_target=analyst_target,
                    upside_analyst=upside_analyst,
                ),
                macro_context=MacroContextInReport(regime=regime_status, vix=vix),
                news_summary=news_summary,
                score=score,
                score_reasons=score_reasons,
            )
        except Exception as e:
            print(f"Error generating comprehensive report for {ticker}: {e}")
            import traceback
            traceback.print_exc()
            return None

    @classmethod
    def get_formatted_report(cls, ticker: str) -> str:
        """종합 리포트 데이터를 생성한 뒤 포맷된 문자열로 반환합니다."""
        report = cls.get_comprehensive_report(ticker)
        if report is None:
            return "Error: Failed to generate report"
        return ReportService.format_comprehensive_report(report)
