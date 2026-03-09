from typing import Optional
from services.analysis.analyzer.dcf_analyzer import DcfAnalyzer
from services.analysis.financial_service import FinancialService

# DcfAnalyzer.calculate_fair_value return dict key
DCF_RESULT_KEY_VALUE = "value"


class DcfService:
    """
    DCF (Discounted Cash Flow) calculation service.
    - Delegates actual computation to the DcfAnalyzer helper.
    """
    
    @classmethod
    def calculate_dcf(cls, ticker: str) -> float:
        """Automatic DCF calculation by ticker (KIS data-based)."""
        try:
            # Fetch processed financial data via FinancialService
            dcf_input = FinancialService.get_dcf_data(ticker)
            if not dcf_input:
                return 0.0

            # Fallback when data is insufficient: EPS(1Y) * PER
            if dcf_input.fallback_fair_value is not None:
                return float(dcf_input.fallback_fair_value) or 0.0

            if dcf_input.fcf_per_share is None:
                return 0.0

            growth = dcf_input.growth_rate
            beta = dcf_input.beta
            discount_rate = dcf_input.discount_rate
            
            # Call helper method
            result = DcfAnalyzer.calculate_fair_value(
                fcf_per_share=dcf_input.fcf_per_share,
                growth_rate=growth,
                beta=beta,
                manual_discount=discount_rate
            )
            return float(result.get(DCF_RESULT_KEY_VALUE, 0.0) or 0.0)
            
        except Exception:
            return 0.0

    @classmethod
    def get_dcf_input(cls, ticker: str, growth_rate: Optional[float] = None) -> tuple:
        """Validate ticker and prepare DCF input data. Returns (real_ticker, dcf_data, calc_growth).
        Raises ValueError if ticker not found or FCF unavailable."""
        from services.market.ticker_service import TickerService
        real_ticker = TickerService.resolve_ticker(ticker)
        if not real_ticker:
            raise ValueError("Ticker not found")
        dcf_data = FinancialService.get_dcf_data(real_ticker)
        if not dcf_data or dcf_data.fcf_per_share is None:
            raise ValueError("FCF data not available")
        calc_growth = growth_rate if growth_rate is not None else dcf_data.growth_rate
        return real_ticker, dcf_data, calc_growth

    @classmethod
    def calculate_custom_dcf(
        cls,
        ticker: str,
        growth_rate: Optional[float] = None,
        discount_rate: Optional[float] = None,
        terminal_growth: Optional[float] = 0.03,
    ) -> dict:
        """Calculate DCF with user-specified parameters. Returns {ticker, dcf_data, calc_growth, result}."""
        real_ticker, dcf_data, calc_growth = cls.get_dcf_input(ticker, growth_rate)
        result = DcfAnalyzer.calculate_fair_value(
            fcf_per_share=dcf_data.fcf_per_share,
            growth_rate=calc_growth,
            beta=dcf_data.beta,
            risk_free_rate=0.04,
            terminal_growth=terminal_growth,
            manual_discount=discount_rate,
        )
        return {"ticker": real_ticker, "dcf_data": dcf_data, "calc_growth": calc_growth, "result": result}

    @classmethod
    def get_filtered_list(cls, market_type: Optional[str] = None, has_value: bool = False) -> dict:
        """Return all-stock DCF list (with filter and upside_pct descending sort)."""
        from services.market.stock_meta_service import StockMetaService
        rows = StockMetaService.get_all_latest_dcf()
        if market_type:
            rows = [r for r in rows if r["market_type"] == market_type.upper()]
        if has_value:
            rows = [r for r in rows if r["dcf_value"] and r["dcf_value"] > 0]
        rows.sort(
            key=lambda r: r["upside_pct"] if r["upside_pct"] is not None else float("-inf"),
            reverse=True,
        )
        return {"count": len(rows), "items": rows}

    @classmethod
    def save_override(
        cls,
        ticker: str,
        fcf_per_share=None,
        beta=None,
        growth_rate=None,
        fair_value=None,
    ):
        """Save DCF override (includes FinancialService cache invalidation)."""
        from services.market.stock_meta_service import StockMetaService
        FinancialService._dcf_input_by_ticker.pop(ticker, None)
        return StockMetaService.upsert_dcf_override(
            ticker=ticker,
            fcf_per_share=fcf_per_share,
            beta=beta,
            growth_rate=growth_rate,
            fair_value=fair_value,
        )
