from fastapi import APIRouter, HTTPException
from typing import Optional
from services.analysis.analysis_service import AnalysisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.analysis.financial_service import FinancialService
from services.market.ticker_service import TickerService
from models.schemas import (
    ValuationResult, ReturnAnalysis, DcfOverrideRequest, StrategyWeightOverrideRequest,
    FinancialMetricsResponse, CustomDcfResponse, CustomDcfParameters,
    DcfOverrideResponse, StrategyWeightsResponse, DcfInputData,
)

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)


def resolve_ticker_or_404(ticker_input: str) -> str:
    """티커 입력값을 실제 티커로 변환하고, 없으면 404를 반환합니다."""
    resolved = TickerService.resolve_ticker(ticker_input)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Could not find ticker for: {ticker_input}")
    return resolved


def _run_dcf_calculation(
    dcf_data: DcfInputData,
    growth_rate: float,
    discount_rate: Optional[float],
    terminal_growth: Optional[float],
) -> dict:
    """DcfAnalyzer를 호출하여 적정가 계산 결과를 반환합니다."""
    from services.analysis.analyzer.dcf_analyzer import DcfAnalyzer
    return DcfAnalyzer.calculate_fair_value(
        fcf_per_share=dcf_data.fcf_per_share,
        growth_rate=growth_rate,
        beta=dcf_data.beta,
        risk_free_rate=0.04,
        terminal_growth=terminal_growth,
        manual_discount=discount_rate,
    )


@router.get("/valuation/{ticker_input}", response_model=ValuationResult)
def get_valuation(ticker_input: str) -> ValuationResult:
    """
    해당 종목(한글명 또는 티커)의 기술적 지표(RSI, 이동평균)를 기반으로
    매수/매도 신호를 제시합니다.
    예: '삼성전자', '테슬라', '005930', 'TSLA'
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.evaluate_stock(real_ticker)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/returns/{ticker_input}", response_model=ReturnAnalysis)
def get_returns(ticker_input: str) -> ReturnAnalysis:
    """2024-01-01부터 현재까지 수익률과 MDD(최대 낙폭)을 분석합니다."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.analyze_returns(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail="Data not found")
    return result


@router.get("/metrics/{ticker_input}", response_model=FinancialMetricsResponse)
def get_financial_metrics(ticker_input: str) -> FinancialMetricsResponse:
    """종목 핵심 재무 지표(PER, PBR, ROE, 배당수익률 등) 조회."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    metrics = FinancialService.get_metrics(real_ticker)
    return FinancialMetricsResponse(
        ticker=real_ticker,
        metrics=metrics.model_dump() if metrics else None,
    )


def _resolve_dcf_input(ticker: str, growth_rate: Optional[float]) -> tuple:
    """티커 검증 및 DCF 입력 데이터를 준비합니다. (real_ticker, dcf_data, calc_growth) 반환."""
    real_ticker = TickerService.resolve_ticker(ticker)
    if not real_ticker:
        raise HTTPException(status_code=404, detail="Ticker not found")
    dcf_data = FinancialService.get_dcf_data(real_ticker)
    if not dcf_data or dcf_data.fcf_per_share is None:
        raise HTTPException(status_code=400, detail="FCF data not available")
    calc_growth = growth_rate if growth_rate is not None else dcf_data.growth_rate
    return real_ticker, dcf_data, calc_growth


@router.get("/dcf-custom", response_model=CustomDcfResponse)
def get_custom_dcf(
    ticker: str,
    growth_rate: Optional[float] = None,
    discount_rate: Optional[float] = None,
    terminal_growth: Optional[float] = 0.03,
) -> CustomDcfResponse:
    """사용자 지정 파라미터로 DCF 적정가 계산."""
    real_ticker, dcf_data, calc_growth = _resolve_dcf_input(ticker, growth_rate)
    result = _run_dcf_calculation(dcf_data, calc_growth, discount_rate, terminal_growth)
    return CustomDcfResponse(
        ticker=real_ticker,
        parameters=CustomDcfParameters(
            growth_rate=calc_growth,
            discount_rate=result.get("discount_rate"),
            terminal_growth=terminal_growth,
        ),
        fair_value=result.get("value"),
        error=result.get("error"),
    )


@router.put("/dcf-override", response_model=DcfOverrideResponse)
def update_dcf_override(payload: DcfOverrideRequest) -> DcfOverrideResponse:
    """종목별 DCF 입력값(FCF, Beta, 성장률)을 사용자 설정으로 저장합니다."""
    real_ticker = resolve_ticker_or_404(payload.ticker)
    override = FinancialService.update_dcf_override(
        real_ticker, payload.fcf_per_share, payload.beta, payload.growth_rate
    )
    if not override:
        raise HTTPException(status_code=500, detail="Failed to save DCF override")
    return DcfOverrideResponse(ticker=real_ticker, override=override)


@router.put("/strategy/weights", response_model=StrategyWeightsResponse)
def update_strategy_weights(payload: StrategyWeightOverrideRequest) -> StrategyWeightsResponse:
    """종목별 점수 가중치 오버라이드를 설정합니다."""
    overrides = TradingStrategyService.set_top_weight_overrides(payload.weights)
    return StrategyWeightsResponse(overrides=overrides)
