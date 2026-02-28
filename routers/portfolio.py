from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from typing import Optional, List, Dict, Any
from services.trading.portfolio_service import PortfolioService
from services.market.ticker_service import TickerService
from services.base.scheduler_service import SchedulerService
from models.schemas import PortfolioUploadResponse, PortfolioListResponse, HoldingActionResponse

router = APIRouter(
    prefix="/portfolio",
    tags=["Portfolio"]
)


@router.post("/upload", response_model=PortfolioUploadResponse)
async def upload_portfolio(
    file: UploadFile = File(...),
    user_id: str = Form(default="default"),
) -> PortfolioUploadResponse:
    """엑셀 파일을 업로드하여 포트폴리오를 등록합니다."""
    try:
        content = await file.read()
        holdings = PortfolioService.upload_portfolio(content, file.filename, user_id)
        return PortfolioUploadResponse(
            message=f"포트폴리오 업로드 성공! {len(holdings)}개 종목 등록됨",
            holdings=holdings,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{user_id}", response_model=PortfolioListResponse)
def get_portfolio(user_id: str = "default") -> PortfolioListResponse:
    """저장된 포트폴리오를 조회합니다."""
    holdings = PortfolioService.load_portfolio(user_id)
    if not holdings:
        return PortfolioListResponse(message="등록된 포트폴리오가 없습니다.", holdings=[])
    return PortfolioListResponse(holdings=holdings)


@router.get("/{user_id}/analysis", response_model=Dict[str, Any])
def analyze_portfolio(user_id: str = "default") -> Dict[str, Any]:
    """포트폴리오 수익률을 분석합니다."""
    price_cache = SchedulerService.get_all_cached_prices()
    return PortfolioService.analyze_portfolio(user_id, price_cache)


@router.get("/{user_id}/full-report", response_model=List[Dict[str, Any]])
def get_full_portfolio_report(user_id: str = "default") -> List[Dict[str, Any]]:
    """보유 종목 전체에 대한 상세 분석 데이터를 반환합니다."""
    price_cache = SchedulerService.get_all_cached_prices()
    return PortfolioService.build_full_report(user_id, price_cache)


@router.post("/{user_id}/add", response_model=HoldingActionResponse)
def add_holding(
    user_id: str,
    ticker: str,
    quantity: float,
    buy_price: float,
    name: Optional[str] = None,
) -> HoldingActionResponse:
    """수동으로 보유 종목을 추가합니다."""
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.add_holding_manual(user_id, resolved_ticker, quantity, buy_price, name)
    return HoldingActionResponse(message=f"{resolved_ticker} 추가 완료", holdings=holdings)


@router.delete("/{user_id}/{ticker}", response_model=HoldingActionResponse)
def remove_holding(user_id: str, ticker: str) -> HoldingActionResponse:
    """보유 종목을 제거합니다."""
    holdings = PortfolioService.load_portfolio(user_id)
    new_holdings = [h for h in holdings if h.get("ticker") != ticker]
    PortfolioService.save_portfolio(user_id, new_holdings)
    return HoldingActionResponse(message=f"{ticker} 제거 완료", holdings=new_holdings)


@router.post("/{user_id}/trade", response_model=HoldingActionResponse)
def trade_holding(
    user_id: str, ticker: str, action: str, quantity: float, price: float,
) -> HoldingActionResponse:
    """주식 매수/매도 통합 처리. action: 'buy' 또는 'sell'."""
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.load_portfolio(user_id)
    try:
        holdings = PortfolioService.apply_trade_action(holdings, resolved_ticker, action, quantity, price)
        PortfolioService.save_portfolio(user_id, holdings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return HoldingActionResponse(
        message=f"{resolved_ticker} {action.upper()} completed", holdings=holdings,
    )
