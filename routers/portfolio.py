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


def _build_holding_report_row(holding: Dict[str, Any], cached: Dict[str, Any]) -> Dict[str, Any]:
    """보유 종목 한 건의 분석 리포트 row를 생성합니다."""
    price = cached.get("price") or holding.get("buy_price")
    buy_price = holding.get("buy_price") or 0
    profit_pct = ((price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
    dcf = cached.get("fair_value_dcf")
    upside = ((dcf - price) / price) * 100 if (dcf and price) else 0
    return {
        "ticker": holding.get("ticker"),
        "name": holding.get("name"),
        "price": price,
        "change": cached.get("change", 0),
        "change_pct": cached.get("change_pct", 0),
        "pre_price": cached.get("pre_price"),
        "pre_change_pct": cached.get("pre_change_pct"),
        "return_pct": round(profit_pct, 2),
        "rsi": cached.get("rsi"),
        "ema5": cached.get("ema5"),
        "ema10": cached.get("ema10"),
        "ema20": cached.get("ema20"),
        "ema60": cached.get("ema60"),
        "ema120": cached.get("ema120"),
        "ema200": cached.get("ema200"),
        "dcf_fair": dcf,
        "dcf_upside": round(upside, 1) if dcf else None,
    }


def _apply_buy_to_holdings(
    holdings: List[Dict[str, Any]],
    resolved_ticker: str,
    quantity: float,
    price: float,
) -> List[Dict[str, Any]]:
    """매수: 기존 보유 시 평단 계산, 없으면 신규 추가합니다."""
    target = next((h for h in holdings if h.get("ticker") == resolved_ticker), None)
    if target:
        total_qty = target["quantity"] + quantity
        avg_price = ((target["quantity"] * target["buy_price"]) + (quantity * price)) / total_qty
        target["quantity"] = total_qty
        target["buy_price"] = avg_price
    else:
        holdings.append({
            "ticker": resolved_ticker, "name": resolved_ticker,
            "quantity": quantity, "buy_price": price,
            "current_price": price, "sector": "Unknown",
        })
    return holdings


def _apply_sell_to_holdings(
    holdings: List[Dict[str, Any]],
    resolved_ticker: str,
    quantity: float,
) -> List[Dict[str, Any]]:
    """매도: 수량 차감 후 0 이하이면 목록에서 제거합니다."""
    target = next((h for h in holdings if h.get("ticker") == resolved_ticker), None)
    if not target:
        raise ValueError("보유하지 않은 종목입니다.")
    if target["quantity"] < quantity:
        raise ValueError("매도 수량이 보유 수량보다 많습니다.")
    target["quantity"] -= quantity
    if target["quantity"] <= 0:
        holdings = [h for h in holdings if h.get("ticker") != resolved_ticker]
    return holdings


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
    holdings = PortfolioService.load_portfolio(user_id)
    price_cache = SchedulerService.get_all_cached_prices()
    report = [
        _build_holding_report_row(h, price_cache.get(h.get("ticker"), {}))
        for h in holdings if h.get("ticker")
    ]
    report.sort(key=lambda row: row["return_pct"], reverse=True)
    return report


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
    holdings = PortfolioService.load_portfolio(user_id)
    holdings = _apply_buy_to_holdings(holdings, resolved_ticker, quantity, buy_price)
    # 신규 추가 시 이름 반영
    target = next((h for h in holdings if h.get("ticker") == resolved_ticker), None)
    if target and name and target.get("name") == resolved_ticker:
        target["name"] = name
    PortfolioService.save_portfolio(user_id, holdings)
    return HoldingActionResponse(message=f"{resolved_ticker} 추가 완료", holdings=holdings)


@router.delete("/{user_id}/{ticker}", response_model=HoldingActionResponse)
def remove_holding(user_id: str, ticker: str) -> HoldingActionResponse:
    """보유 종목을 제거합니다."""
    holdings = PortfolioService.load_portfolio(user_id)
    new_holdings = [h for h in holdings if h.get("ticker") != ticker]
    PortfolioService.save_portfolio(user_id, new_holdings)
    return HoldingActionResponse(message=f"{ticker} 제거 완료", holdings=new_holdings)


def _apply_trade_action(
    holdings: List[Dict[str, Any]], resolved_ticker: str, action: str, quantity: float, price: float
) -> List[Dict[str, Any]]:
    """매수/매도 액션을 검증하고 holdings를 반환합니다."""
    if action.lower() == "buy":
        return _apply_buy_to_holdings(holdings, resolved_ticker, quantity, price)
    elif action.lower() == "sell":
        return _apply_sell_to_holdings(holdings, resolved_ticker, quantity)
    raise HTTPException(status_code=400, detail="Invalid action. Use 'buy' or 'sell'.")


@router.post("/{user_id}/trade", response_model=HoldingActionResponse)
def trade_holding(
    user_id: str, ticker: str, action: str, quantity: float, price: float,
) -> HoldingActionResponse:
    """주식 매수/매도 통합 처리. action: 'buy' 또는 'sell'."""
    resolved_ticker = TickerService.resolve_ticker(ticker)
    holdings = PortfolioService.load_portfolio(user_id)
    try:
        holdings = _apply_trade_action(holdings, resolved_ticker, action, quantity, price)
        PortfolioService.save_portfolio(user_id, holdings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return HoldingActionResponse(
        message=f"{resolved_ticker} {action.upper()} completed", holdings=holdings,
    )
