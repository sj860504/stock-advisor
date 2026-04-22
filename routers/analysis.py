from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any
from services.analysis.analysis_service import AnalysisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.analysis.financial_service import FinancialService
from services.market.market_data_service import MarketDataService
from services.trading.portfolio_service import PortfolioService
from services.market.macro_service import MacroService
from services.strategy.execution_service_v2 import TradeExecutorService
from utils.market import is_kr
from services.analysis.dcf_service import DcfService
from services.market.ticker_service import TickerService
from models.schemas import (
    ValuationResult, ReturnAnalysis, DcfOverrideRequest, StrategyWeightOverrideRequest,
    FinancialMetricsResponse, CustomDcfResponse, CustomDcfParameters,
    DcfOverrideResponse, StrategyWeightsResponse, DcfInputData, ComprehensiveReport,
    DcfListResponse, DcfDetailResponse, MacroDataSnapshot, UserState,
)

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)


def resolve_ticker_or_404(ticker_input: str) -> str:
    """Resolve ticker input to actual ticker, returning 404 if not found."""
    resolved = TickerService.resolve_ticker(ticker_input)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Could not find ticker for: {ticker_input}")
    return resolved


@router.get("/valuation/{ticker_input}", response_model=ComprehensiveReport)
def get_valuation(ticker_input: str) -> ComprehensiveReport:
    """Return comprehensive analysis report for the given ticker (name or ticker code)."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.get_comprehensive_report(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail=f"No data available for {real_ticker}")
    return result


@router.get("/returns/{ticker_input}", response_model=ReturnAnalysis)
def get_returns(ticker_input: str) -> ReturnAnalysis:
    """Analyze returns and MDD (max drawdown) from 2024-01-01 to present."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.analyze_returns(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail="Data not found")
    return result


@router.get("/metrics/{ticker_input}", response_model=FinancialMetricsResponse)
def get_financial_metrics(ticker_input: str) -> FinancialMetricsResponse:
    """Get key financial metrics (PER, PBR, ROE, dividend yield, etc.)."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    metrics = FinancialService.get_metrics(real_ticker)
    return FinancialMetricsResponse(
        ticker=real_ticker,
        metrics=metrics.model_dump() if metrics else None,
    )


@router.get("/dcf", response_model=DcfListResponse)
def get_all_dcf(market_type: Optional[str] = None, has_value: bool = False) -> DcfListResponse:
    """
    Return latest DCF fair value list for all tickers.
    - market_type: 'KR' or 'US' filter (all if unspecified)
    - has_value: if true, return only tickers with dcf_value > 0
    Sorted by upside_pct descending (undervalued tickers first).
    """
    return DcfService.get_filtered_list(market_type=market_type, has_value=has_value)


@router.get("/dcf/{ticker_input}", response_model=DcfDetailResponse)
def get_dcf(ticker_input: str) -> DcfDetailResponse:
    """
    Get current DCF fair value for a ticker.
    Auto-selects: override -> yfinance FCF -> EPS*PER fallback.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    dcf_input = FinancialService.get_dcf_data(real_ticker)
    dcf_val = DcfService.calculate_dcf(real_ticker)
    return {
        "ticker": real_ticker,
        "dcf_value": round(dcf_val, 2) if dcf_val else None,
        "source": dcf_input.source if dcf_input else None,
        "fcf_per_share": dcf_input.fcf_per_share if dcf_input else None,
        "growth_rate": round(dcf_input.growth_rate, 4) if dcf_input else None,
        "beta": dcf_input.beta if dcf_input else None,
        "fallback_fair_value": dcf_input.fallback_fair_value if dcf_input else None,
    }


@router.get("/dcf-custom", response_model=CustomDcfResponse)
def get_custom_dcf(
    ticker: str,
    growth_rate: Optional[float] = None,
    discount_rate: Optional[float] = None,
    terminal_growth: Optional[float] = 0.03,
) -> CustomDcfResponse:
    """Calculate DCF fair value with custom parameters."""
    try:
        data = DcfService.calculate_custom_dcf(ticker, growth_rate, discount_rate, terminal_growth)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = data["result"]
    return CustomDcfResponse(
        ticker=data["ticker"],
        parameters=CustomDcfParameters(
            growth_rate=data["calc_growth"],
            discount_rate=result.get("discount_rate"),
            terminal_growth=terminal_growth,
        ),
        fair_value=result.get("value"),
        error=result.get("error"),
    )


@router.put("/dcf-override", response_model=DcfOverrideResponse)
def update_dcf_override(payload: DcfOverrideRequest) -> DcfOverrideResponse:
    """Save per-ticker DCF override.
    - Specify fair_value only to use that value directly as fair price.
    - Combination of fcf_per_share + beta + growth_rate enables 2-stage DCF calculation.
    """
    real_ticker = resolve_ticker_or_404(payload.ticker)
    override = DcfService.save_override(
        ticker=real_ticker,
        fcf_per_share=payload.fcf_per_share,
        beta=payload.beta,
        growth_rate=payload.growth_rate,
        fair_value=payload.fair_value,
    )
    if not override:
        raise HTTPException(status_code=500, detail="Failed to save DCF override")
    return DcfOverrideResponse(ticker=real_ticker, override={
        "fcf_per_share": override.fcf_per_share,
        "beta": override.beta,
        "growth_rate": override.growth_rate,
        "fair_value": override.fair_value,
        "updated_at": override.updated_at.isoformat() if override.updated_at else None,
    })


@router.put("/strategy/weights", response_model=StrategyWeightsResponse)
def update_strategy_weights(payload: StrategyWeightOverrideRequest) -> StrategyWeightsResponse:
    """Set per-ticker score weight overrides."""
    overrides = TradingStrategyService.set_top_weight_overrides(payload.weights)
    return StrategyWeightsResponse(overrides=overrides)


@router.get("/score/{ticker_input}")
def get_ticker_score(ticker_input: str, user_id: str = "sean") -> Dict[str, Any]:
    """Calculate and return strategy score, recommendation, and reasons for a ticker."""
    real_ticker = resolve_ticker_or_404(ticker_input)

    # Ensure ticker is registered and has market data
    state = MarketDataService.get_state(real_ticker)
    if not state:
        from models.ticker_state import TickerState
        state = TickerState(ticker=real_ticker)
        MarketDataService._states[real_ticker] = state
    # 항상 force warm-up — RSI 포함 최신 데이터 갱신
    MarketDataService._warm_up_data(real_ticker, _force=True)
    state = MarketDataService.get_state(real_ticker)
    if not state or getattr(state, 'current_price', 0) <= 0:
        raise HTTPException(status_code=404, detail=f"No market data available for {real_ticker}")

    # Assemble data (same pattern as get_waiting_list)
    holdings = PortfolioService.load_portfolio(user_id)
    raw_macro = MacroService.get_macro_data()
    macro_snapshot = MacroDataSnapshot(**{k: v for k, v in raw_macro.items() if k != "timestamp"})
    user_state_map = TradingStrategyService._load_state(user_id)
    user_state = user_state_map.get(user_id, UserState())
    cash_balance = PortfolioService.load_cash(user_id)
    kr_total, us_total_krw, _, _ = TradeExecutorService._calculate_total_assets(holdings, cash_balance, macro_snapshot)
    exchange_rate = MacroService.get_exchange_rate()

    market_total = kr_total if is_kr(real_ticker) else us_total_krw
    holdings_map = {h.ticker: h for h in holdings}
    holding = holdings_map.get(real_ticker)

    result = TradingStrategyService.analyze_ticker(
        real_ticker, state, holding, macro_snapshot, user_state,
        cash_balance, exchange_rate, market_total_krw=market_total,
    )
    result["name"] = getattr(state, 'name', None) or real_ticker
    return result
