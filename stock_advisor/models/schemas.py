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
    target_price: Optional[float] = None # 적정 주가 (BPS * PBR or EPS * PER)
    rating: str  # Buy, Sell, Hold
    score: int # 0-100 종합 점수
    logic: str
    technical: dict # RSI, MA 등
    fundamental: FinancialMetrics # PER, PBR 등

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
