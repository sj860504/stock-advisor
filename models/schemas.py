from pydantic import BaseModel
from typing import List, Optional

class StockRequest(BaseModel):
    ticker: str
    market: str = "KRX"  # KRX, NASDAQ, etc.

class FinancialMetrics(BaseModel):
    market_cap: Optional[str] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    dividend_yield: Optional[float] = None

class ValuationResult(BaseModel):
    ticker: str
    current_price: float
    target_price: Optional[float] = None # ?곸젙 二쇨? (BPS * PBR or EPS * PER)
    rating: str  # Buy, Sell, Hold
    score: int # 0-100 醫낇빀 ?먯닔
    logic: str
    technical: dict # RSI, MA ??
    fundamental: FinancialMetrics # PER, PBR ??

class ReturnAnalysis(BaseModel):
    ticker: str
    period: str
    return_percentage: float
    max_drawdown: float

class PriceAlert(BaseModel):
    ticker: str
    target_price: float
    condition: str  # above, below
    is_active: bool = True

class NewsItem(BaseModel):
    title: str
    link: str
    source: str
    published_at: Optional[str] = None

class HoldingSchema(BaseModel):
    ticker: str
    name: Optional[str] = None
    quantity: int
    buy_price: float
    current_price: Optional[float] = None
    sector: Optional[str] = None

class PortfolioSchema(BaseModel):
    user_id: str
    cash_balance: float
    holdings: List[HoldingSchema]
