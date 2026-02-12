from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from services.analysis.analysis_service import AnalysisService
from services.analysis.financial_service import FinancialService
from services.market.ticker_service import TickerService
from models.schemas import ValuationResult, ReturnAnalysis

router = APIRouter(
    prefix="/analysis",
    tags=["Analysis"]
)

def resolve_ticker_or_404(ticker_input: str) -> str:
    resolved = TickerService.resolve_ticker(ticker_input)
    if not resolved:
         raise HTTPException(status_code=404, detail=f"Could not find ticker for: {ticker_input}")
    return resolved

@router.get("/valuation/{ticker_input}", response_model=ValuationResult)
def get_valuation(ticker_input: str):
    """
    ?대떦 醫낅ぉ(?대쫫 ?먮뒗 ?곗빱)??湲곗닠??吏??RSI, ?대룞?됯퇏)瑜?湲곕컲?쇰줈 留ㅼ닔/留ㅻ룄 ?섍껄???쒖떆?⑸땲??
    ?? '?쇱꽦?꾩옄', '?뚯뒳??, '005930', 'TSLA'
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.evaluate_stock(real_ticker)
    
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result

@router.get("/returns/{ticker_input}", response_model=ReturnAnalysis)
def get_returns(ticker_input: str):
    """
    2024??1??1?쇰????꾩옱源뚯????섏씡瑜좉낵 MDD(理쒕? ?숉룺)瑜?遺꾩꽍?⑸땲??
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.analyze_returns(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail="Data not found")
    return result

@router.get("/metrics/{ticker_input}")
def get_financial_metrics(ticker_input: str):
    """
    醫낅ぉ???щТ 吏??PER, PBR, ROE, ?쒓?珥앹븸 ??瑜?議고쉶?⑸땲??
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    metrics = FinancialService.get_metrics(real_ticker)
    return {
        "ticker": real_ticker,
        "metrics": metrics
    }

@router.get("/dcf-custom")
def get_custom_dcf(
    ticker: str,
    growth_rate: Optional[float] = None,
    discount_rate: Optional[float] = None,
    terminal_growth: Optional[float] = 0.03
):
    """
    ?ъ슜???뺤쓽 蹂?섎? ?ъ슜?섏뿬 DCF ?곸젙 媛移섎? 怨꾩궛?⑸땲??
    """
    real_ticker = TickerService.resolve_ticker(ticker)
    
    # 湲곕낯 ?щТ ?곗씠??媛?몄삤湲?
    metrics = FinancialService.get_metrics(real_ticker)
    fcf_per_share = metrics.get('fcf_per_share')
    
    if not fcf_per_share:
        raise HTTPException(status_code=400, detail="FCF data not available")

    calc_growth = growth_rate if growth_rate is not None else metrics.get('growth_5y', 0.10)
    calc_beta = metrics.get('beta', 1.0)
    
    from services.analysis.dcf_service import DcfService
    result = DcfService.calculate_fair_value(
        fcf_per_share=fcf_per_share,
        growth_rate=calc_growth,
        beta=calc_beta,
        risk_free_rate=0.04,
        terminal_growth=terminal_growth,
        manual_discount=discount_rate
    )
    
    return {
        "ticker": real_ticker,
        "parameters": {
            "growth_rate": calc_growth,
            "discount_rate": result.get('discount_rate'),
            "terminal_growth": terminal_growth
        },
        "fair_value": result.get('value'),
        "error": result.get('error')
    }
