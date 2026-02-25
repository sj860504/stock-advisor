from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any

class StockRequest(BaseModel):
    ticker: str
    market: str = "KRX"  # KRX, NASDAQ, etc.

class FinancialMetrics(BaseModel):
    market_cap: Optional[str] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    dividend_yield: Optional[float] = None


class AnalyzedFinancialMetrics(BaseModel):
    """재무 분석기(FinancialAnalyzer) 반환용 모델. per/pbr/roe/eps/bps/배당/가격/시총."""
    per: float = 0.0
    pbr: float = 0.0
    roe: float = 0.0
    eps: float = 0.0
    bps: float = 0.0
    dividend_yield: float = 0.0
    current_price: float = 0.0
    market_cap: float = 0.0


class KisFinancialsMeta(BaseModel):
    """KIS 재무/시세 조회 요청 시 사용하는 메타 DTO (api_path, tr_id, market_code)."""
    api_path: str = ""
    api_tr_id: str = ""
    api_market_code: str = ""


class KisFinancialsResponse(BaseModel):
    """KIS 재무/시세 조회 응답 DTO. output은 API output 또는 fetcher의 raw 페이로드."""
    output: Dict[str, Any] = Field(default_factory=dict)


class DcfInputData(BaseModel):
    """DCF 계산 입력 데이터. get_dcf_data 반환용."""
    fcf_per_share: Optional[float] = None
    beta: float = 1.0
    growth_rate: float = 0.0
    discount_rate: Optional[float] = None
    timestamp: float = 0.0
    source: str = ""
    years_used: Optional[List[int]] = None
    fallback_fair_value: Optional[float] = None


class TechnicalIndicatorsSnapshot(BaseModel):
    """최신 시점 기술적 지표 스냅샷 (RSI, EMA by span). IndicatorService 반환용."""
    rsi: float = 50.0
    ema: Dict[int, Optional[float]] = Field(default_factory=dict)

    def to_storage_payload(self) -> "IndicatorsForStorage":
        """DB(Financials) 저장용 페이로드로 변환."""
        return IndicatorsForStorage(
            rsi=self.rsi,
            ema5=self.ema.get(5),
            ema10=self.ema.get(10),
            ema20=self.ema.get(20),
            ema60=self.ema.get(60),
            ema120=self.ema.get(120),
            ema200=self.ema.get(200),
        )

    def to_metrics_dict(self) -> Dict[str, Any]:
        """save_financials 등에 넘길 rsi/ema dict 형태 (기존 호환). ema는 None 포함, flat 키 ema5~ema200 포함."""
        ema_dict = dict(self.ema)
        d: Dict[str, Any] = {"rsi": self.rsi, "ema": ema_dict}
        for span, val in self.ema.items():
            d[f"ema{span}"] = val
        return d


class IndicatorsForStorage(BaseModel):
    """DB(Financials) 기술적 지표 저장용 모델. rsi, ema5~ema200."""
    rsi: Optional[float] = None
    ema5: Optional[float] = None
    ema10: Optional[float] = None
    ema20: Optional[float] = None
    ema60: Optional[float] = None
    ema120: Optional[float] = None
    ema200: Optional[float] = None

    def to_financials_metrics_dict(self) -> Dict[str, Any]:
        """StockMetaService.save_financials에 병합할 metrics dict (rsi + ema dict)."""
        ema_dict = {}
        if self.ema5 is not None:
            ema_dict[5] = self.ema5
        if self.ema10 is not None:
            ema_dict[10] = self.ema10
        if self.ema20 is not None:
            ema_dict[20] = self.ema20
        if self.ema60 is not None:
            ema_dict[60] = self.ema60
        if self.ema120 is not None:
            ema_dict[120] = self.ema120
        if self.ema200 is not None:
            ema_dict[200] = self.ema200
        return {"rsi": self.rsi, "ema": ema_dict}


class BollingerBandsLatest(BaseModel):
    """볼린저 밴드 최신값 (상단/중간/하단)."""
    middle: float = 0.0
    upper: float = 0.0
    lower: float = 0.0


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

class DcfOverrideRequest(BaseModel):
    ticker: str
    fcf_per_share: float
    beta: float
    growth_rate: float

class StrategyWeightOverrideRequest(BaseModel):
    weights: Dict[str, int]

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

class WatchItem(BaseModel):
    ticker: str
    price: float
    change: float
    change_rate: float
    volume: float
    rsi: Optional[float] = None
    ma20: Optional[float] = None


# ----- 종합 리포트 / 매크로 / 포트폴리오 / 주문 결과용 DTO -----


class PriceInfoSummary(BaseModel):
    """시세 요약 (현재가, 등락률, 상태)."""
    current: float = 0.0
    change_pct: float = 0.0
    state: str = ""


class PortfolioSummaryInReport(BaseModel):
    """리포트 내 보유 요약."""
    owned: bool = False
    avg_cost: float = 0.0
    return_pct: float = 0.0


class TechnicalSummaryInReport(BaseModel):
    """리포트 내 기술적 지표 요약."""
    rsi: float = 50.0
    emas: Dict[int, Optional[float]] = Field(default_factory=dict)
    bollinger: Dict[str, float] = Field(default_factory=dict)


class FundamentalSummaryInReport(BaseModel):
    """리포트 내 기본적 지표 요약 (DCF, 목표가)."""
    dcf_fair: Any = "N/A"  # float or "N/A"
    upside_dcf: float = 0.0
    analyst_target: Optional[float] = None
    upside_analyst: float = 0.0


class MacroContextInReport(BaseModel):
    """리포트 내 매크로 컨텍스트."""
    regime: str = ""
    vix: Optional[float] = None


class ComprehensiveReport(BaseModel):
    """종합 분석 리포트 한 건. get_comprehensive_report 반환용."""
    ticker: str = ""
    name: str = ""
    price_info: PriceInfoSummary = Field(default_factory=PriceInfoSummary)
    portfolio: PortfolioSummaryInReport = Field(default_factory=PortfolioSummaryInReport)
    technical: TechnicalSummaryInReport = Field(default_factory=TechnicalSummaryInReport)
    fundamental: FundamentalSummaryInReport = Field(default_factory=FundamentalSummaryInReport)
    macro_context: MacroContextInReport = Field(default_factory=MacroContextInReport)
    news_summary: str = ""

    def to_report_dict(self) -> Dict[str, Any]:
        """ReportService.format_comprehensive_report 호환 dict."""
        return {
            "ticker": self.ticker,
            "name": self.name,
            "price_info": self.price_info.model_dump(),
            "portfolio": self.portfolio.model_dump(),
            "technical": {
                "rsi": self.technical.rsi,
                "emas": self.technical.emas,
                "bollinger": self.technical.bollinger,
            },
            "fundamental": self.fundamental.model_dump(),
            "macro_context": self.macro_context.model_dump(),
            "news_summary": self.news_summary,
        }


class PortfolioHoldingDto(BaseModel):
    """보유 종목 한 건 (API/서비스 반환용)."""
    ticker: str = ""
    name: Optional[str] = None
    quantity: int = 0
    buy_price: float = 0.0
    current_price: Optional[float] = None
    sector: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class MacroDataSnapshot(BaseModel):
    """매크로 데이터 스냅샷. get_macro_data 반환용."""
    us_10y_yield: float = 0.0
    market_regime: Dict[str, Any] = Field(default_factory=dict)
    vix: Optional[float] = None
    fear_greed: Optional[float] = None
    indices: Dict[str, Any] = Field(default_factory=dict)
    economic_indicators: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "us_10y_yield": self.us_10y_yield,
            "market_regime": self.market_regime,
            "vix": self.vix,
            "fear_greed": self.fear_greed,
            "indices": self.indices,
            "economic_indicators": self.economic_indicators,
        }


class TradeRecordDto(BaseModel):
    """거래 기록 한 건 (API 반환용)."""
    id: Optional[int] = None
    ticker: str = ""
    order_type: str = ""
    quantity: int = 0
    price: float = 0.0
    result_msg: Optional[str] = None
    timestamp: Optional[str] = None
    strategy_name: str = ""
    name: Optional[str] = None
    buy_price: Optional[float] = None
    profit: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SettingItem(BaseModel):
    """설정 한 건 (key, value, description)."""
    key: str = ""
    value: str = ""
    description: str = ""


# ----- 스캐너 기회 결과용 DTO -----


class OversoldCandidate(BaseModel):
    """과매도 우량주 후보."""
    ticker: str = ""
    price: float = 0.0
    rsi: float = 0.0
    pbr: float = 0.0
    name: str = ""


class TrendBreakoutCandidate(BaseModel):
    """추세 돌파(EMA200 골든크로스) 후보."""
    ticker: str = ""
    price: float = 0.0
    ema200: float = 0.0
    change: float = 0.0


class AnalystStrongBuyCandidate(BaseModel):
    """기관 강력 매수(목표가 괴리) 후보."""
    ticker: str = ""
    price: float = 0.0
    target: float = 0.0
    upside: float = 0.0
    name: str = ""


class ScanOpportunitiesResult(BaseModel):
    """스캔 기회 결과. ScannerService.scan_market 반환용."""
    oversold_bluechip: List[OversoldCandidate] = Field(default_factory=list)
    trend_breakout: List[TrendBreakoutCandidate] = Field(default_factory=list)
    analyst_strong_buy: List[AnalystStrongBuyCandidate] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """API 호환 dict (기존 opportunities 형태)."""
        return {
            "oversold_bluechip": [c.model_dump() for c in self.oversold_bluechip],
            "trend_breakout": [c.model_dump() for c in self.trend_breakout],
            "analyst_strong_buy": [c.model_dump() for c in self.analyst_strong_buy],
        }


# ---- API Response Schemas ----

class MessageResponse(BaseModel):
    """단순 메시지 응답."""
    message: str


class StatusMessageResponse(BaseModel):
    """상태 + 메시지 응답."""
    status: str
    message: str


class TriggeredAlertsResponse(BaseModel):
    triggered_alerts: List[Any]


class PendingAlertsResponse(BaseModel):
    alerts: List[Any]


class FinancialMetricsResponse(BaseModel):
    ticker: str
    metrics: Optional[Dict[str, Any]] = None


class CustomDcfParameters(BaseModel):
    growth_rate: Optional[float] = None
    discount_rate: Optional[float] = None
    terminal_growth: Optional[float] = None


class CustomDcfResponse(BaseModel):
    ticker: str
    parameters: CustomDcfParameters
    fair_value: Optional[float] = None
    error: Optional[str] = None


class DcfOverrideResponse(BaseModel):
    ticker: str
    override: Optional[Dict[str, Any]] = None


class StrategyWeightsResponse(BaseModel):
    overrides: Optional[Dict[str, Any]] = None


class TradingSignalItem(BaseModel):
    ticker: str
    rsi: Optional[float] = None
    price: Optional[float] = None
    signal: str


class DcfSignalItem(BaseModel):
    ticker: str
    price: Optional[float] = None
    dcf: Optional[float] = None
    upside_pct: Optional[float] = None
    signal: str


class Ema200SignalItem(BaseModel):
    ticker: str
    price: Optional[float] = None
    ema200: Optional[float] = None
    signal: str


class TradingSignalsResponse(BaseModel):
    oversold: List[TradingSignalItem] = Field(default_factory=list)
    overbought: List[TradingSignalItem] = Field(default_factory=list)
    undervalued: List[DcfSignalItem] = Field(default_factory=list)
    ema200_support: List[Ema200SignalItem] = Field(default_factory=list)
    message: Optional[str] = None


class PortfolioUploadResponse(BaseModel):
    message: str
    holdings: List[Dict[str, Any]] = Field(default_factory=list)


class PortfolioListResponse(BaseModel):
    holdings: List[Dict[str, Any]] = Field(default_factory=list)
    message: Optional[str] = None


class HoldingActionResponse(BaseModel):
    message: str
    holdings: List[Dict[str, Any]] = Field(default_factory=list)


class SettingUpdateResponse(BaseModel):
    status: str
    key: str
    value: str


class TickSettingsResponse(BaseModel):
    enabled: bool
    ticker: str
    cash_ratio: float
    entry_pct: float
    add_pct: float
    take_profit_pct: float
    stop_loss_pct: float
    close_minutes: int


class TickSettingsUpdateResponse(BaseModel):
    status: str
    updated: Dict[str, str] = Field(default_factory=dict)


class SellAllRebuResponse(BaseModel):
    status: str
    message: str
    sold: int
    failed: int
    failed_tickers: Optional[List[str]] = None
    strategy_error: Optional[str] = None


class OrderRequest(BaseModel):
    """주식 매수/매도 주문 요청."""
    ticker: str
    quantity: int
    price: int = 0
    order_type: str = "buy"


class TickTradingSettingsRequest(BaseModel):
    """틱매매 설정 변경 요청."""
    enabled: Optional[bool] = None
    ticker: Optional[str] = None
    cash_ratio: Optional[float] = None
    entry_pct: Optional[float] = None
    add_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    close_minutes: Optional[int] = None
