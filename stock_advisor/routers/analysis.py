from fastapi import APIRouter, HTTPException, Depends
from typing import List
from stock_advisor.services.analysis_service import AnalysisService
from stock_advisor.services.financial_service import FinancialService
from stock_advisor.services.ticker_service import TickerService
from stock_advisor.models.schemas import ValuationResult, ReturnAnalysis

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
    해당 종목(이름 또는 티커)의 기술적 지표(RSI, 이동평균)를 기반으로 매수/매도 의견을 제시합니다.
    예: '삼성전자', '테슬라', '005930', 'TSLA'
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.evaluate_stock(real_ticker)
    
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result

@router.get("/returns/{ticker_input}", response_model=ReturnAnalysis)
def get_returns(ticker_input: str):
    """
    2024년 1월 1일부터 현재까지의 수익률과 MDD(최대 낙폭)를 분석합니다.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    result = AnalysisService.analyze_returns(real_ticker)
    if not result:
        raise HTTPException(status_code=404, detail="Data not found")
    return result

@router.get("/metrics/{ticker_input}")
def get_financial_metrics(ticker_input: str):
    """
    종목의 재무 지표(PER, PBR, ROE, 시가총액 등)를 조회합니다.
    """
    real_ticker = resolve_ticker_or_404(ticker_input)
    metrics = FinancialService.get_metrics(real_ticker)
    return {
        "ticker": real_ticker,
        "metrics": metrics
    }
