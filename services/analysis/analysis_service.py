"""Comprehensive analysis service. Integrates price, technical, fundamental, macro, and news data per ticker to generate reports."""
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
    """Comprehensive ticker analysis and report generation."""

    # ── Helper Methods ────────────────────────────────────────────

    @classmethod
    def _fetch_price_data(cls, token: str, ticker: str) -> Optional[dict]:
        """Fetch current price and basic data from KIS API."""
        if is_kr(ticker):
            return KisFetcher.fetch_domestic_price(token, ticker)
        return KisFetcher.fetch_overseas_price(token, ticker)

    @classmethod
    def _build_portfolio_summary(
        cls, ticker: str, current_price: float, user_id: str
    ) -> tuple:
        """Return holding status, avg cost, and return %. (holding_dto, avg_cost, return_pct)"""
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
        """Calculate RSI/EMA/Bollinger. Returns defaults if no data. (rsi, emas, bollinger)"""
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
        """Calculate DCF intrinsic value using DB settings DCF parameters. Returns 'N/A' if insufficient."""
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
        """Calculate trading score. Returns (None, []) on failure."""
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
                macro_snapshot, {}, cash,
            )
        except Exception:
            return None, []

    @staticmethod
    def _build_report_object(
        ticker, name, current_price, change_rate_pct, market_state,
        holding, avg_cost, return_pct, rsi, emas, bollinger,
        dcf_fair, upside_dcf, analyst_target, upside_analyst,
        macro_snapshot, news_summary, score, score_reasons,
    ) -> ComprehensiveReport:
        """Assemble the final ComprehensiveReport from pre-computed values."""
        return ComprehensiveReport(
            ticker=ticker,
            name=name,
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
                regime=getattr(macro_snapshot.get("market_regime"), 'status', ''),
                vix=macro_snapshot.get("vix"),
            ),
            news_summary=news_summary,
            score=score,
            score_reasons=score_reasons,
        )

    # ── Public Methods ────────────────────────────────────────────

    @classmethod
    def get_comprehensive_report(cls, ticker: str, user_id: str = "sean") -> Optional[ComprehensiveReport]:
        """Integrate price, portfolio, technical, fundamental, macro, and news for a ticker into a report model."""
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

            return cls._build_report_object(
                ticker=ticker, name=price_data.get("name", ticker),
                current_price=current_price, change_rate_pct=change_rate_pct,
                market_state=market_state, holding=holding, avg_cost=avg_cost,
                return_pct=return_pct, rsi=rsi, emas=emas, bollinger=bollinger,
                dcf_fair=dcf_fair, upside_dcf=upside_dcf,
                analyst_target=analyst_target, upside_analyst=upside_analyst,
                macro_snapshot=macro_snapshot, news_summary=news_summary,
                score=score, score_reasons=score_reasons,
            )
        except Exception as e:
            print(f"Error generating comprehensive report for {ticker}: {e}")
            import traceback
            traceback.print_exc()
            return None

    @classmethod
    def get_formatted_report(cls, ticker: str) -> str:
        """Generate comprehensive report data and return as formatted string."""
        report = cls.get_comprehensive_report(ticker)
        if report is None:
            return "Error: Failed to generate report"
        return ReportService.format_comprehensive_report(report)
