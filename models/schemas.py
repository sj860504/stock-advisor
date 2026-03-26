from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Dict, List, Optional, Any
from datetime import datetime

class RegimeComponents(BaseModel):
    """_calculate_all_regime_components() 결과 — 순수 계산값, I/O 없음."""
    technical_20: int
    vix_20: int
    fng_20: int
    econ_20: int
    other_20: int
    extreme_fear: bool = False
    ema_map: Dict[int, Any] = Field(default_factory=dict)
    tech_detail: Dict[str, Any] = Field(default_factory=dict)
    other_scores: Dict[str, Any] = Field(default_factory=dict)
    inflation_pressure: int = 0
    inflation_detail: Any = None
    growth_signal: int = 0
    economic_phase: str = "unknown"
    phase_modifier: int = 0


class PortfolioContext(BaseModel):
    """_load_portfolio_context()가 반환하는 설정/환율/KIS 요약값 스냅샷."""
    initial_principal: float
    usd_cash: float
    exchange_rate: float
    kis_scts_evlu: float = 0.0
    kis_pchs_amt: float = 0.0
    kis_evlu_amt: float = 0.0
    kis_evlu_pfls: float = 0.0
    kis_tot_evlu: float = 0.0
    kis_nass: float = 0.0


class KrPortfolio(BaseModel):
    """_calc_kr_portfolio() 결과."""
    stock_val: float
    invested: float
    profit: float
    profit_pct: float
    cash_krw: float
    total: float


class UsPortfolio(BaseModel):
    """_calc_us_portfolio() 결과."""
    stock_usd: float
    invested_usd: float
    profit_usd: float
    profit_pct: float
    total_usd: float
    total_krw: float
    cash_krw: float


class UnpackedSignal(BaseModel):
    """_unpack_signal()이 sig dict에서 추출한 정형화된 신호 데이터."""
    ticker: str
    score: int
    reason_str: str
    profit_pct: float = 0.0
    market_total: float = 0.0
    forced_sell: bool = False

    model_config = {"arbitrary_types_allowed": True}
    state: Any = None      # TickerState (순환 import 방지로 Any)
    holding: Optional[Any] = None  # HoldingSchema or dict


class ExecutionConfig(BaseModel):
    """Signal execution loop 설정값 스냅샷. _load_execution_config()가 생성."""
    buy_max: int
    sell_min: int
    take_profit_pct: float
    stop_loss_pct: float
    add_rsi_limit: float
    add_score_limit: int
    exchange_rate: float
    today: str


class TradeResult(BaseModel):
    executed: bool
    spent_krw: float = 0.0
    spent_usd: float = 0.0

    @classmethod
    def no_op(cls) -> "TradeResult":
        return cls(executed=False)


class UnfilledOrder(BaseModel):
    """KIS 미체결 주문 1건."""
    ticker: str
    order_type: str  # 'buy' or 'sell'
    order_qty: int = 0
    filled_qty: int = 0
    remaining_qty: int = 0
    order_price: float = 0.0
    order_date: str = ""
    order_time: str = ""


class UnfilledOrdersResult(BaseModel):
    """KIS 미체결 조회 결과."""
    orders: List[UnfilledOrder] = Field(default_factory=list)
    error: Optional[str] = None

    def has_pending(self, ticker: str, order_type: str = None) -> bool:
        """특정 종목에 미체결 주문이 있는지 확인."""
        for o in self.orders:
            if o.ticker == ticker and o.remaining_qty > 0:
                if order_type is None or o.order_type == order_type:
                    return True
        return False

    def pending_qty(self, ticker: str, order_type: str = None) -> int:
        """특정 종목의 미체결 잔여수량 합계."""
        total = 0
        for o in self.orders:
            if o.ticker == ticker and o.remaining_qty > 0:
                if order_type is None or o.order_type == order_type:
                    total += o.remaining_qty
        return total


class OrderVerificationResult(BaseModel):
    """주문 체결 확인 결과."""
    ticker: str
    order_type: str
    is_filled: bool
    remaining_qty: int = 0
    message: str = ""


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
    """Model returned by FinancialAnalyzer. Contains PER/PBR/ROE/EPS/BPS/dividend/price/market_cap."""
    per: float = 0.0
    pbr: float = 0.0
    roe: float = 0.0
    eps: float = 0.0
    bps: float = 0.0
    dividend_yield: float = 0.0
    current_price: float = 0.0
    market_cap: float = 0.0


class KisFinancialsMeta(BaseModel):
    """Meta DTO for KIS financials/price API requests (api_path, tr_id, market_code)."""
    api_path: str = ""
    api_tr_id: str = ""
    api_market_code: str = ""


class KisFinancialsResponse(BaseModel):
    """KIS financials/price API response DTO. output is the API output or fetcher raw payload."""
    output: Dict[str, Any] = Field(default_factory=dict)


class DcfInputData(BaseModel):
    """DCF calculation input data. Returned by get_dcf_data."""
    fcf_per_share: Optional[float] = None
    beta: float = 1.0
    growth_rate: float = 0.0
    discount_rate: Optional[float] = None
    timestamp: float = 0.0
    source: str = ""
    years_used: Optional[List[int]] = None
    fallback_fair_value: Optional[float] = None
    analyst_count: int = 0


class TechnicalIndicatorsSnapshot(BaseModel):
    """Latest technical indicators snapshot (RSI, EMA by span). Returned by IndicatorService."""
    rsi: float = 50.0
    ema: Dict[int, Optional[float]] = Field(default_factory=dict)

    def to_storage_payload(self) -> "IndicatorsForStorage":
        """Convert to storage payload for DB (Financials)."""
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
        """Return rsi/ema dict for save_financials (backward compat). Includes None values and flat keys ema5~ema200."""
        ema_dict = dict(self.ema)
        d: Dict[str, Any] = {"rsi": self.rsi, "ema": ema_dict}
        for span, val in self.ema.items():
            d[f"ema{span}"] = val
        return d


class IndicatorsForStorage(BaseModel):
    """Technical indicators storage model for DB (Financials). rsi, ema5~ema200."""
    rsi: Optional[float] = None
    ema5: Optional[float] = None
    ema10: Optional[float] = None
    ema20: Optional[float] = None
    ema60: Optional[float] = None
    ema120: Optional[float] = None
    ema200: Optional[float] = None

    def to_financials_metrics_dict(self) -> Dict[str, Any]:
        """Metrics dict to merge into StockMetaService.save_financials (rsi + ema dict)."""
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
    """Latest Bollinger Bands values (upper/middle/lower)."""
    middle: float = 0.0
    upper: float = 0.0
    lower: float = 0.0


class ValuationResult(BaseModel):
    ticker: str
    current_price: float
    target_price: Optional[float] = None # Fair value (BPS * PBR or EPS * PER)
    rating: str  # Buy, Sell, Hold
    score: int # 0-100 composite score
    logic: str
    technical: "TechnicalSummaryInReport" = Field(default_factory=lambda: TechnicalSummaryInReport())
    fundamental: FinancialMetrics # PER, PBR etc.

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
    fcf_per_share: Optional[float] = None
    beta: Optional[float] = None
    growth_rate: Optional[float] = None
    fair_value: Optional[float] = None   # Directly specified fair value (overrides FCF calculation)

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


# ----- Comprehensive report / macro / portfolio / order result DTOs -----


class PriceInfoSummary(BaseModel):
    """Price summary (current price, change rate, state)."""
    current: float = 0.0
    change_pct: float = 0.0
    state: str = ""


class PortfolioSummaryInReport(BaseModel):
    """Holdings summary within report."""
    owned: bool = False
    avg_cost: float = 0.0
    return_pct: float = 0.0


class TechnicalSummaryInReport(BaseModel):
    """Technical indicators summary within report."""
    rsi: float = 50.0
    emas: Dict[int, Optional[float]] = Field(default_factory=dict)
    bollinger: Dict[str, float] = Field(default_factory=dict)


class FundamentalSummaryInReport(BaseModel):
    """Fundamental indicators summary within report (DCF, target price)."""
    dcf_fair: Any = "N/A"  # float or "N/A"
    upside_dcf: float = 0.0
    analyst_target: Optional[float] = None
    upside_analyst: float = 0.0


class MacroContextInReport(BaseModel):
    """Macro context within report."""
    regime: str = ""
    vix: Optional[float] = None


class ComprehensiveReport(BaseModel):
    """Single comprehensive analysis report. Returned by get_comprehensive_report."""
    ticker: str = ""
    name: str = ""
    price_info: PriceInfoSummary = Field(default_factory=PriceInfoSummary)
    portfolio: PortfolioSummaryInReport = Field(default_factory=PortfolioSummaryInReport)
    technical: TechnicalSummaryInReport = Field(default_factory=TechnicalSummaryInReport)
    fundamental: FundamentalSummaryInReport = Field(default_factory=FundamentalSummaryInReport)
    macro_context: MacroContextInReport = Field(default_factory=MacroContextInReport)
    news_summary: str = ""
    score: Optional[int] = None
    score_reasons: List[str] = Field(default_factory=list)

    def to_report_dict(self) -> Dict[str, Any]:
        """Dict compatible with ReportService.format_comprehensive_report."""
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
    """Single holding (API/service response DTO)."""
    ticker: str = ""
    name: Optional[str] = None
    quantity: int = 0
    buy_price: float = 0.0
    current_price: Optional[float] = None
    sector: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class MacroDataSnapshot(BaseModel):
    """Macro data snapshot returned by get_macro_data."""
    us_10y_yield: float = 0.0
    market_regime: "MarketRegimeSchema" = Field(default_factory=lambda: MarketRegimeSchema())
    vix: Optional[float] = None
    fear_greed: Optional[float] = None
    indices: Dict[str, "IndexQuote"] = Field(default_factory=dict)
    economic_indicators: "EconomicIndicatorsSnapshot" = Field(default_factory=lambda: EconomicIndicatorsSnapshot())
    exchange_rate: float = 1350.0

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class TradeRecordDto(BaseModel):
    """Single trade record (API response DTO)."""
    id: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v):
        if v is None:
            return None
        return str(v)

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
    profit_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SettingItem(BaseModel):
    """Single setting entry (key, value, description)."""
    key: str = ""
    value: str = ""
    description: str = ""


# ----- Scanner opportunity result DTOs -----


class OversoldCandidate(BaseModel):
    """Oversold blue-chip candidate."""
    ticker: str = ""
    price: float = 0.0
    rsi: float = 0.0
    pbr: float = 0.0
    name: str = ""


class TrendBreakoutCandidate(BaseModel):
    """Trend breakout (EMA200 golden cross) candidate."""
    ticker: str = ""
    price: float = 0.0
    ema200: float = 0.0
    change: float = 0.0


class AnalystStrongBuyCandidate(BaseModel):
    """Analyst strong-buy (target price gap) candidate."""
    ticker: str = ""
    price: float = 0.0
    target: float = 0.0
    upside: float = 0.0
    name: str = ""


class ScanOpportunitiesResult(BaseModel):
    """Scan opportunities result. Returned by ScannerService.scan_market."""
    oversold_bluechip: List[OversoldCandidate] = Field(default_factory=list)
    trend_breakout: List[TrendBreakoutCandidate] = Field(default_factory=list)
    analyst_strong_buy: List[AnalystStrongBuyCandidate] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """API-compatible dict (legacy opportunities format)."""
        return {
            "oversold_bluechip": [c.model_dump() for c in self.oversold_bluechip],
            "trend_breakout": [c.model_dump() for c in self.trend_breakout],
            "analyst_strong_buy": [c.model_dump() for c in self.analyst_strong_buy],
        }


# ----- Market Regime Schemas -----


class EconomicPhaseDetail(BaseModel):
    """Economic phase detail within market regime."""
    phase: str = "Neutral"
    modifier: int = 0
    inflation_pressure: int = 0
    growth_signal: int = 0
    oil_1m_ret: Optional[float] = None
    cpi_mom: Optional[float] = None
    ppi_mom: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class OtherDetailScores(BaseModel):
    """Other composite scores detail."""
    us_10y_yield: float = 0.0
    yield_spread_10y2y: Optional[float] = None
    vix_1m_chg: Optional[float] = None
    btc_1m_ret: Optional[float] = None
    dxy_1m_ret: Optional[float] = None
    gold_1m_ret: Optional[float] = None
    oil_1m_ret: Optional[float] = None
    yield_score: int = 0
    curve_score: int = 0
    dxy_score: int = 0
    btc_score: int = 0
    gold_score: int = 0
    oil_score: int = 0

    model_config = ConfigDict(extra="allow")


class MarketRegimeComponents(BaseModel):
    """Component scores of market regime."""
    technical: int = 10
    technical_detail: Dict[str, Optional[float]] = Field(default_factory=dict)
    vix: int = 10
    fear_greed: int = 10
    economic: int = 10
    other: int = 10
    other_detail: OtherDetailScores = Field(default_factory=OtherDetailScores)
    economic_phase_detail: EconomicPhaseDetail = Field(default_factory=EconomicPhaseDetail)

    model_config = ConfigDict(extra="allow")


class MarketRegimeSchema(BaseModel):
    """Market regime snapshot (Bull/Bear/Neutral)."""
    status: str = "Unknown"
    current: float = 0.0
    ma200: float = 0.0
    diff_pct: float = 0.0
    regime_score: int = -1
    bear_threshold: int = 40
    economic_phase: str = "Neutral"
    phase_modifier: int = 0
    ema: Dict[str, float] = Field(default_factory=dict)
    components: MarketRegimeComponents = Field(default_factory=MarketRegimeComponents)
    _fetch_failed: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


# ----- Economic Indicators Schemas -----


class EconomicIndicatorEntry(BaseModel):
    """Single economic indicator score entry."""
    name: str = ""
    series_id: str = ""
    weight: float = 1.0
    latest: Optional[float] = None
    previous: Optional[float] = None
    delta: Optional[float] = None
    score: int = 0
    weighted_score: float = 0.0
    status: str = "no_data"


class EconomicIndicatorsSummary(BaseModel):
    """Summary of economic indicators scoring."""
    total_weighted_score: float = 0.0
    max_weighted_score: float = 0.0
    total_score: float = 0.0
    max_score: float = 0.0
    sentiment_ratio: float = 0.0
    available_count: int = 0
    total_count: int = 0


class EconomicIndicatorsSnapshot(BaseModel):
    """Full economic indicators result."""
    indicators: Dict[str, EconomicIndicatorEntry] = Field(default_factory=dict)
    summary: EconomicIndicatorsSummary = Field(default_factory=EconomicIndicatorsSummary)


# ----- Index / Commodity / Crypto Quote Schemas -----


class IndexQuote(BaseModel):
    """Index quote (price + change %)."""
    price: float = 0.0
    change: float = 0.0
    source: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CryptoQuote(BaseModel):
    """Crypto asset quote."""
    price: float = 0.0
    change: float = 0.0

    model_config = ConfigDict(extra="allow")


class CommodityQuote(BaseModel):
    """Commodity quote."""
    price: float = 0.0
    change: float = 0.0

    model_config = ConfigDict(extra="allow")


# ----- Split Order / Cooldown Schemas -----


class SplitOrderState(BaseModel):
    """State tracking for split buy orders."""
    total_qty: int = 0
    remaining_qty: int = 0
    splits_done: int = 0
    split_count: int = 3
    start_date: str = ""
    entry_price: float = 0.0


class BuyCooldownEntry(BaseModel):
    """Buy cooldown tracking entry."""
    date: str = ""
    price: float = 0.0


class SplitSellOrderState(BaseModel):
    """State tracking for split sell orders."""
    total_qty: int = 0
    remaining_qty: int = 0
    splits_done: int = 0
    split_count: int = 5
    start_date: str = ""
    tranche_qty: int = 0  # 트리거 시점 고정 수량 (ceil(total_qty/split_count)), 0=미초기화


class UserState(BaseModel):
    """Per-user strategy state (panic_locks, cooldowns, split orders, trailing high)."""
    user_id: str = "sean"
    panic_locks: Dict[str, Any] = Field(default_factory=dict)
    sell_cooldown: Dict[str, Any] = Field(default_factory=dict)
    add_buy_cooldown: Dict[str, Any] = Field(default_factory=dict)
    split_orders: Dict[str, Any] = Field(default_factory=dict)
    sell_split_orders: Dict[str, Any] = Field(default_factory=dict)
    trailing_high: Dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SignalSchema(BaseModel):
    """Trading signal for a single ticker (output of _collect_trading_signals)."""
    ticker: str
    state: Any  # TickerState object
    holding: Optional[Any] = None  # HoldingSchema or None
    score: int = 50
    reasons: List[str] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ----- Sector Rebalance Schemas -----


class RebalanceSoldEntry(BaseModel):
    """Entry for a sold position during rebalancing."""
    ticker: str = ""
    group: str = ""
    dev: float = 0.0
    profit_pct: float = 0.0


class RebalanceBoughtEntry(BaseModel):
    """Entry for a bought position during rebalancing."""
    ticker: str = ""
    group: str = ""
    dev: float = 0.0
    score: int = 0


class RebalanceSkippedEntry(BaseModel):
    """Entry for a skipped rebalance action."""
    ticker: str = ""
    reason: str = ""


class SectorRebalanceResult(BaseModel):
    """Result of sector rebalancing operation."""
    sold: List[RebalanceSoldEntry] = Field(default_factory=list)
    bought: List[RebalanceBoughtEntry] = Field(default_factory=list)
    skipped: List[RebalanceSkippedEntry] = Field(default_factory=list)
    weights_before: Dict[str, Any] = Field(default_factory=dict)
    weights_after: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


# ----- Calendar Event Schema -----


class CalendarEvent(BaseModel):
    """Economic calendar event."""
    date: str = ""
    time_et: str = ""
    time_kst: str = ""
    date_kst: str = ""
    datetime_utc: str = ""
    datetime_kst: str = ""
    release_id: str = ""
    series_ids: List[str] = Field(default_factory=list)
    names: List[str] = Field(default_factory=list)
    total_weight: int = 0
    is_past: bool = False


# Rebuild models that use forward references
ValuationResult.model_rebuild()
MacroDataSnapshot.model_rebuild()


# ---- API Response Schemas ----

class MessageResponse(BaseModel):
    """Simple message response."""
    message: str


class StatusMessageResponse(BaseModel):
    """Status + message response."""
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


class DcfListItem(BaseModel):
    ticker: str
    name: Optional[str] = None
    market_type: Optional[str] = None
    current_price: Optional[float] = None
    dcf_value: Optional[float] = None
    upside_pct: Optional[float] = None
    is_override: bool = False
    base_date: Optional[str] = None


class DcfListResponse(BaseModel):
    count: int
    items: List[DcfListItem]


class DcfDetailResponse(BaseModel):
    ticker: str
    dcf_value: Optional[float] = None
    source: Optional[str] = None
    fcf_per_share: Optional[float] = None
    growth_rate: Optional[float] = None
    beta: Optional[float] = None
    fallback_fair_value: Optional[float] = None


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
    """Stock buy/sell order request."""
    ticker: str
    quantity: int
    price: int = 0
    order_type: str = "buy"


class TickTradingSettingsRequest(BaseModel):
    """Tick trading settings update request."""
    enabled: Optional[bool] = None
    ticker: Optional[str] = None
    cash_ratio: Optional[float] = None
    entry_pct: Optional[float] = None
    add_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    close_minutes: Optional[int] = None


# ── Watchlist ──────────────────────────────────────────────────────────────

class WatchlistItem(BaseModel):
    ticker: str
    added_at: datetime


class WatchlistResponse(BaseModel):
    user_id: str
    tickers: list[WatchlistItem]


class WatchlistUpdateResponse(BaseModel):
    status: str   # "added" | "removed" | "already_exists" | "not_found"
    ticker: str


class StrategyModeResponse(BaseModel):
    kr_strategy_mode: str  # "top100" | "watchlist"
    us_strategy_mode: str


class StrategyModeRequest(BaseModel):
    market: str  # "kr" | "us"
    mode: str    # "top100" | "watchlist"
