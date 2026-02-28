from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any
from services.analysis.analysis_service import AnalysisService
from services.strategy.trading_strategy_service import TradingStrategyService
from services.analysis.financial_service import FinancialService
from services.analysis.dcf_service import DcfService
from services.market.ticker_service import TickerService
from models.schemas import (
    ValuationResult, ReturnAnalysis, DcfOverrideRequest, StrategyWeightOverrideRequest,
    FinancialMetricsResponse, CustomDcfResponse, CustomDcfParameters,
    DcfOverrideResponse, StrategyWeightsResponse, DcfInputData, ComprehensiveReport,
    DcfListResponse, DcfDetailResponse,
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


@router.get("/valuation/{ticker_input}", response_model=ComprehensiveReport)
def get_valuation(ticker_input: str) -> ComprehensiveReport:
    """해당 종목(한글명 또는 티커)의 종합 분석 리포트를 반환합니다."""
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.get_comprehensive_report(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail=f"No data available for {real_ticker}")
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


@router.get("/dcf", response_model=DcfListResponse)
def get_all_dcf(market_type: Optional[str] = None, has_value: bool = False):
    """
    전 종목 최신 DCF 적정가 목록 반환.
    - market_type: 'KR' 또는 'US' 필터 (미입력 시 전체)
    - has_value: true 이면 dcf_value > 0 인 종목만 반환
    upside_pct 기준 내림차순 정렬 (저평가 종목 우선).
    """
    return DcfService.get_filtered_list(market_type=market_type, has_value=has_value)


@router.get("/dcf/{ticker_input}", response_model=DcfDetailResponse)
def get_dcf(ticker_input: str):
    """
    종목의 현재 DCF 적정가 조회.
    오버라이드 설정 → yfinance FCF → EPS*PER 폴백 순으로 자동 선택.
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
    """사용자 지정 파라미터로 DCF 적정가 계산."""
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
    """종목별 DCF 오버라이드 저장.
    - fair_value 만 지정하면 해당 값을 적정가로 직접 사용.
    - fcf_per_share + beta + growth_rate 조합으로 2단계 DCF 계산도 가능.
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
    """종목별 점수 가중치 오버라이드를 설정합니다."""
    overrides = TradingStrategyService.set_top_weight_overrides(payload.weights)
    return StrategyWeightsResponse(overrides=overrides)


@router.get("/sector-weights", response_model=Dict[str, Any])
def get_sector_weights(user_id: str = "sean") -> Dict[str, Any]:
    """섹터 그룹(기술주/가치주/금융주) 현재 비중 및 목표 대비 리밸런싱 현황."""
    return TradingStrategyService.get_sector_rebalance_status(user_id=user_id)
