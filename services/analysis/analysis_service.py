"""종합 분석 서비스. 티커별 시세·기술·기본·매크로·뉴스를 통합해 리포트를 생성합니다."""
from typing import Optional, Union

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

REPORT_HISTORY_DAYS = 365 * 2
REPORT_NEWS_LIMIT = 2


class AnalysisService:
    """티커 종합 분석 및 리포트 생성."""

    # ── 헬퍼 메서드 ────────────────────────────────────────────

    @classmethod
    def _fetch_price_data(cls, token: str, ticker: str) -> Optional[dict]:
        """KIS API에서 현재가 및 기초 데이터 조회."""
        if is_kr(ticker):
            return KisFetcher.fetch_domestic_price(token, ticker)
        return KisFetcher.fetch_overseas_price(token, ticker)

    @classmethod
    def _build_portfolio_summary(
        cls, ticker: str, current_price: float, user_id: str
    ) -> tuple:
        """보유 여부·평단가·수익률 반환. (holding_dto, avg_cost, return_pct)"""
        holdings = PortfolioService.load_portfolio_dtos(user_id)
        holding = next((h for h in holdings if h.ticker == ticker), None)
        avg_cost = holding.buy_price if holding else 0
        return_pct = (
            round((current_price - avg_cost) / avg_cost * 100, 2)
            if holding and avg_cost else 0
        )
        return holding, avg_cost, return_pct

    @classmethod
    def _build_technical_context(cls, ticker: str) -> tuple:
        """RSI·EMA·Bollinger 계산. 데이터 없으면 기본값 반환. (rsi, emas, bollinger)"""
        hist = DataService.get_price_history(ticker, days=REPORT_HISTORY_DAYS)
        if hist.empty:
            return 50, {}, {}
        snapshot = IndicatorService.compute_latest_indicators_snapshot(hist["Close"])
        rsi = snapshot.rsi if snapshot else 50
        emas = snapshot.ema if snapshot else {}
        bb = IndicatorService.compute_bollinger_bands(hist["Close"]).to_latest()
        bollinger = {"middle": bb.middle, "upper": bb.upper, "lower": bb.lower}
        return rsi, emas, bollinger

    @classmethod
    def _calculate_dcf_fair(cls, ticker: str, risk_free_rate: float) -> Union[float, str]:
        """DCF 내재가치 계산. DB settings의 DCF 파라미터 사용. 불충분 시 'N/A' 반환."""
        dcf_data = FinancialService.get_dcf_data(ticker)
        fcf = dcf_data.fcf_per_share if dcf_data else None
        if not dcf_data or not fcf or fcf <= 0:
            return "N/A"

        from services.config.settings_service import SettingsService
        erp          = SettingsService.get_float("DCF_EQUITY_RISK_PREMIUM", 0.055)
        rate_floor   = SettingsService.get_float("DCF_DISCOUNT_RATE_FLOOR", 0.06)
        term_growth  = SettingsService.get_float("DCF_TERMINAL_GROWTH", 0.03)
        stage1_years = SettingsService.get_int("DCF_STAGE1_YEARS", 10)

        discount_rate = max(rate_floor, risk_free_rate + dcf_data.beta * erp)
        dcf_sum, projected_fcf = 0.0, fcf
        for i in range(1, stage1_years + 1):
            projected_fcf *= 1 + dcf_data.growth_rate
            dcf_sum += projected_fcf / ((1 + discount_rate) ** i)
        terminal_value = projected_fcf * (1 + term_growth) / (discount_rate - term_growth)
        dcf_sum += terminal_value / ((1 + discount_rate) ** stage1_years)
        return round(dcf_sum, 2)

    @classmethod
    def _calculate_trade_score(
        cls,
        ticker: str,
        current_price: float,
        change_rate: float,
        rsi: float,
        emas: dict,
        bollinger: dict,
        dcf_fair: Union[float, str],
        user_id: str,
        macro_snapshot: dict,
    ) -> tuple:
        """매매 점수 계산. 실패 시 (None, []) 반환."""
        try:
            from services.strategy.trading_strategy_service import TradingStrategyService
            from services.market.market_data_service import MarketDataService
            from models.ticker_state import TickerState
            state = MarketDataService.get_all_states().get(ticker)
            if not state:
                state = TickerState(
                    ticker=ticker,
                    current_price=current_price,
                    change_rate=change_rate,
                    rsi=rsi,
                    ema=emas,
                    bollinger=bollinger,
                    dcf_value=dcf_fair if isinstance(dcf_fair, float) else 0.0,
                )
            holdings = PortfolioService.load_portfolio_dtos(user_id)
            holding = next((h for h in holdings if h.ticker == ticker), None)
            cash = PortfolioService.load_cash(user_id)
            return TradingStrategyService.calculate_score(
                ticker, state, holding.model_dump() if holding else None,
                macro_snapshot, {}, cash, cash,
            )
        except Exception:
            return None, []

    # ── 공개 메서드 ────────────────────────────────────────────

    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> Optional[ComprehensiveReport]:
        """하나의 티커에 대한 시세·보유·기술·기본·매크로·뉴스를 통합해 리포트 모델을 반환합니다."""
        try:
            token      = KisService.get_access_token()
            price_data = cls._fetch_price_data(token, ticker)
            if not price_data:
                return None

            raw_payload     = price_data.get("raw") or {}
            current_price   = round(float(price_data.get("price", 0) or 0), 2)
            change_rate_pct = round(float(price_data.get("change_rate", 0) or 0), 2)
            market_state    = raw_payload.get("market_state", "OPEN")
            analyst_target  = raw_payload.get("target_mean_price")

            holding, avg_cost, return_pct = cls._build_portfolio_summary(ticker, current_price, user_id)
            rsi, emas, bollinger          = cls._build_technical_context(ticker)

            macro_snapshot = MacroService.get_macro_data()
            risk_free_rate = float(macro_snapshot.get("us_10y_yield", 0) or 0) / 100
            dcf_fair       = cls._calculate_dcf_fair(ticker, risk_free_rate)
            upside_dcf     = (
                round((dcf_fair - current_price) / current_price * 100, 1)
                if dcf_fair != "N/A" and current_price else 0
            )
            upside_analyst = (
                round((analyst_target - current_price) / current_price * 100, 1)
                if analyst_target and current_price else 0
            )

            news_items   = NewsService.get_latest_news(ticker, limit=REPORT_NEWS_LIMIT)
            news_summary = NewsService.summarize_news(ticker, news_items)

            score, score_reasons = cls._calculate_trade_score(
                ticker, current_price, change_rate_pct,
                rsi, emas, bollinger, dcf_fair, user_id, macro_snapshot,
            )

            return ComprehensiveReport(
                ticker=ticker,
                name=price_data.get("name", ticker),
                price_info=PriceInfoSummary(
                    current=current_price,
                    change_pct=change_rate_pct,
                    state=market_state,
                ),
                portfolio=PortfolioSummaryInReport(
                    owned=bool(holding),
                    avg_cost=avg_cost,
                    return_pct=return_pct,
                ),
                technical=TechnicalSummaryInReport(rsi=rsi, emas=emas, bollinger=bollinger),
                fundamental=FundamentalSummaryInReport(
                    dcf_fair=dcf_fair,
                    upside_dcf=upside_dcf,
                    analyst_target=analyst_target,
                    upside_analyst=upside_analyst,
                ),
                macro_context=MacroContextInReport(
                    regime=(macro_snapshot.get("market_regime") or {}).get("status", ""),
                    vix=macro_snapshot.get("vix"),
                ),
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
