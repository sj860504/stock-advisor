from contextlib import contextmanager
from typing import Optional, Generator
from datetime import datetime
from sqlalchemy.orm import Session
from models.stock_meta import Base, StockMeta, Financials, ApiTrMeta, DcfOverride, MarketRegimeHistory
from utils.logger import get_logger
from utils.market import is_kr
import repositories.database as _db
from repositories.stock_meta_repo import StockMetaRepo

logger = get_logger("stock_meta_service")

class StockMetaService:
    """Stock meta info and financial data DB integration service.
    DB connection is delegated to repositories.database singleton.
    Actual CRUD logic is delegated to StockMetaRepo.
    """

    @classmethod
    def init_db(cls) -> None:
        """Initialize database and tables (delegated to repositories.database)."""
        _db.init_db()

    @classmethod
    def get_session(cls) -> Session:
        return _db.get_session()

    @classmethod
    @contextmanager
    def session_scope(cls) -> Generator[Session, None, None]:
        """Auto-managed DB session (commit/rollback/close).

        For write operations:
            with StockMetaService.session_scope() as s:
                s.add(obj); ...
        """
        with _db.session_scope() as session:
            yield session

    @classmethod
    @contextmanager
    def session_ro(cls) -> Generator[Session, None, None]:
        """Read-only session (no commit).

        For queries:
            with StockMetaService.session_ro() as s:
                return s.query(...).first()
        """
        with _db.session_ro() as session:
            yield session

    @classmethod
    def upsert_stock_meta(cls, ticker: str, **kwargs) -> Optional[StockMeta]:
        """Save or update stock meta info."""
        return StockMetaRepo.upsert_stock_meta(ticker, **kwargs)

    @classmethod
    def get_stock_meta(cls, ticker: str) -> Optional[StockMeta]:
        """Get stock meta info."""
        return StockMetaRepo.get_stock_meta(ticker)

    @classmethod
    def get_exchange_code(cls, ticker: str) -> str:
        """Return exchange code for ticker (NASD, NYSE, etc.). Defaults to NASD."""
        meta = cls.get_stock_meta(ticker)
        if meta and meta.exchange_code:
            return meta.exchange_code
        return "NASD"

    @classmethod
    def get_stock_meta_bulk(cls, tickers: list) -> list:
        """Batch get stock meta info for multiple tickers."""
        return StockMetaRepo.get_stock_meta_bulk(tickers)

    @classmethod
    def find_ticker_by_name(cls, name: str) -> Optional[str]:
        """Find ticker by stock name (case-insensitive search on name_ko or name_en)."""
        return StockMetaRepo.find_ticker_by_name(name)

    @classmethod
    def save_financials(cls, ticker: str, metrics: dict, base_date: datetime = None) -> Optional[Financials]:
        """Save financial metrics (update latest data or append history)."""
        return StockMetaRepo.save_financials(ticker, metrics, base_date)

    @classmethod
    def initialize_default_meta(cls, ticker: str) -> Optional[StockMeta]:
        """Initialize default meta info. TR ID/Path from DB api_tr_meta (auto-selects VTS/live)."""
        if is_kr(ticker):
            tr_id, api_path = cls.get_api_info("주식현재가_시세")
            return cls.upsert_stock_meta(
                ticker,
                market_type="KR",
                api_path=api_path,
                api_tr_id=tr_id,
                api_market_code="J",
            )
        else:
            tr_id, api_path = cls.get_api_info("해외주식_상세시세")
            return cls.upsert_stock_meta(
                ticker,
                market_type="US",
                api_path=api_path,
                api_tr_id=tr_id,
                api_market_code="NAS",
            )

    @classmethod
    def get_latest_financials(cls, ticker: str) -> Optional[Financials]:
        """Get most recent financial metrics."""
        return StockMetaRepo.get_latest_financials(ticker)

    @classmethod
    def get_all_latest_dcf(cls, limit: int = 1000) -> list:
        """Batch get latest DCF values and related metrics for all tickers (incl. dcf_overrides merge)."""
        return StockMetaRepo.get_all_latest_dcf(limit=limit)

    @classmethod
    def get_financials_history(cls, ticker: str, limit: int = 2500) -> list:
        """Get financial metrics history for a ticker (newest first)."""
        return StockMetaRepo.get_financials_history(ticker, limit)

    @classmethod
    def get_batch_latest_financials(cls, tickers: list) -> dict:
        """Batch get latest financial metrics for multiple tickers."""
        return StockMetaRepo.get_batch_latest_financials(tickers)

    @classmethod
    def upsert_api_tr_meta(cls, api_name: str, **kwargs) -> Optional[ApiTrMeta]:
        """Save TR ID info per API."""
        return StockMetaRepo.upsert_api_tr_meta(api_name, **kwargs)

    @classmethod
    def init_api_tr_meta(cls) -> int:
        """Initialize and update KIS API TR ID and path info."""
        tr_data = [
            # 1. Domestic stocks
            {"category": "국내주식", "api_name": "주식주문_매도", "tr_id_real": "TTTC0801U", "tr_id_vts": "VTTC0801U", "api_path": "/uapi/domestic-stock/v1/trading/order-cash"},
            {"category": "국내주식", "api_name": "주식주문_매수", "tr_id_real": "TTTC0802U", "tr_id_vts": "VTTC0802U", "api_path": "/uapi/domestic-stock/v1/trading/order-cash"},
            {"category": "국내주식", "api_name": "주식잔고조회", "tr_id_real": "TTTC8434R", "tr_id_vts": "VTTC8434R", "api_path": "/uapi/domestic-stock/v1/trading/inquire-balance"},
            {"category": "국내주식", "api_name": "주식현재가_시세", "tr_id_real": "FHKST01010100", "tr_id_vts": "FHKST01010100", "api_path": "/uapi/domestic-stock/v1/quotations/inquire-price"},
            {"category": "국내주식", "api_name": "국내주식_시가총액순위", "tr_id_real": "FHPST01700000", "tr_id_vts": "FHPST01700000", "api_path": "/uapi/domestic-stock/v1/ranking/market-cap"},

            # 2. Overseas stocks
            {"category": "해외주식", "api_name": "해외주식_미국매수", "tr_id_real": "TTTT1002U", "tr_id_vts": "VTTT1002U", "api_path": "/uapi/overseas-stock/v1/trading/order"},
            {"category": "해외주식", "api_name": "해외주식_미국매도", "tr_id_real": "TTTT1006U", "tr_id_vts": "VTTT1001U", "api_path": "/uapi/overseas-stock/v1/trading/order"},
            {"category": "해외주식", "api_name": "해외주식_현재가", "tr_id_real": "HHDFS00000300", "tr_id_vts": "HHDFS00000300", "api_path": "/uapi/overseas-price/v1/quotations/price", "api_path_vts": "/uapi/overseas-price/v1/quotations/price"},
            {"category": "해외주식", "api_name": "해외주식_상세시세", "tr_id_real": "HHDFS70200200", "tr_id_vts": "HHDFS00000300", "api_path": "/uapi/overseas-price/v1/quotations/price-detail", "api_path_vts": "/uapi/overseas-price/v1/quotations/price"},
            {"category": "해외주식", "api_name": "해외주식_시가총액순위", "tr_id_real": "HHDFS76350100", "tr_id_vts": "HHDFS76350100", "api_path": "/uapi/overseas-stock/v1/ranking/market-cap"},
            {"category": "해외주식", "api_name": "해외주식_기간별시세", "tr_id_real": "HHDFS76240000", "tr_id_vts": "HHDFS76240000", "api_path": "/uapi/overseas-price/v1/quotations/dailyprice", "api_path_vts": "/uapi/overseas-price/v1/quotations/dailyprice"},
            {"category": "해외주식", "api_name": "해외주식_종목지수환율기간별", "tr_id_real": "FHKST03030100", "tr_id_vts": "FHKST03030100", "api_path": "/uapi/overseas-stock/v1/quotations/inquire-daily-chartprice"},
            {"category": "해외주식", "api_name": "해외주식_잔고조회", "tr_id_real": "TTTS3012R", "tr_id_vts": "VTTS3012R", "api_path": "/uapi/overseas-stock/v1/trading/inquire-balance"},
            {"category": "해외주식", "api_name": "해외주식_잔고조회_종합", "tr_id_real": "TTTT3012R", "tr_id_vts": "VTTT3012R", "api_path": "/uapi/overseas-stock/v1/trading/inquire-balance"},
            {"category": "해외주식", "api_name": "해외주식_가용현금조회", "tr_id_real": "TTTS3007R", "tr_id_vts": "VTTS3007R", "api_path": "/uapi/overseas-stock/v1/trading/inquire-present-balance"},

            # 3. Trade history / unfilled order query
            {"category": "국내주식", "api_name": "국내주식_체결조회", "tr_id_real": "TTTC0081R", "tr_id_vts": "VTTC0081R", "api_path": "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"},
            {"category": "해외주식", "api_name": "해외주식_체결조회", "tr_id_real": "JTTT3001R", "tr_id_vts": "VTTT3001R", "api_path": "/uapi/overseas-stock/v1/trading/inquire-ccnl"},
            {"category": "국내주식", "api_name": "국내주식_미체결조회", "tr_id_real": "TTTC0081R", "tr_id_vts": "VTTC0081R", "api_path": "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"},
            {"category": "해외주식", "api_name": "해외주식_미체결조회", "tr_id_real": "JTTT3001R", "tr_id_vts": "VTTT3001R", "api_path": "/uapi/overseas-stock/v1/trading/inquire-ccnl"},

            # 4. Common/auth
            {"category": "공통", "api_name": "접근토큰발급", "tr_id_real": "tokenP", "tr_id_vts": "tokenP", "api_path": "/oauth2/tokenP"},
            {"category": "공통", "api_name": "접근토큰폐기", "tr_id_real": "revokeP", "tr_id_vts": "revokeP", "api_path": "/oauth2/revokeP"},
            {"category": "공통", "api_name": "Hashkey", "tr_id_real": "hashkey", "tr_id_vts": "hashkey", "api_path": "/uapi/hashkey"},
            {"category": "국내주식", "api_name": "국내주식_일자별시세", "tr_id_real": "FHKST03010100", "tr_id_vts": "FHKST03010100", "api_path": "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"},
        ]

        count = 0
        for data in tr_data:
            if cls.upsert_api_tr_meta(**data):
                count += 1
        return count

    @classmethod
    def get_api_meta(cls, api_name: str) -> Optional[ApiTrMeta]:
        """Get full API meta info by API name."""
        return StockMetaRepo.get_api_meta(api_name)

    @classmethod
    def get_api_info(cls, api_name: str, is_vts: bool = None) -> tuple[Optional[str], Optional[str]]:
        """Get TR ID and path for current environment."""
        if is_vts is None:
            from config import Config
            is_vts = Config.KIS_IS_VTS

        meta = cls.get_api_meta(api_name)
        # api_tr_meta may be empty right after DB recovery; auto-initialize once
        if not meta:
            try:
                cls.init_api_tr_meta()
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize api_tr_meta automatically: {e}")
            meta = cls.get_api_meta(api_name)
        if not meta:
            return None, None

        tr_id = meta.tr_id_vts if is_vts else meta.tr_id_real
        path = (meta.api_path_vts if is_vts and meta.api_path_vts else meta.api_path)
        return tr_id, path

    @classmethod
    def get_tr_id(cls, api_name: str, is_vts: bool = None) -> Optional[str]:
        """Get TR ID for current environment (backward compatible)."""
        tr_id, _ = cls.get_api_info(api_name, is_vts)
        return tr_id

    @classmethod
    def update_market_code(cls, ticker: str, market_code: str) -> None:
        """Update api_market_code in stock_meta."""
        from repositories.stock_meta_repo import StockMetaRepo
        StockMetaRepo.upsert_stock_meta(ticker, api_market_code=market_code)

    @classmethod
    def upsert_dcf_override(
        cls,
        ticker: str,
        fcf_per_share: float = None,
        beta: float = None,
        growth_rate: float = None,
        fair_value: float = None,
    ) -> Optional[DcfOverride]:
        """Save/update user-specified DCF input values.
        If fair_value is set, it is used directly as DCF fair value without FCF calculation.
        """
        return StockMetaRepo.upsert_dcf_override(
            ticker, fcf_per_share=fcf_per_share, beta=beta,
            growth_rate=growth_rate, fair_value=fair_value,
        )

    @classmethod
    def get_dcf_override(cls, ticker: str) -> Optional[DcfOverride]:
        """Get user-specified DCF input values."""
        return StockMetaRepo.get_dcf_override(ticker)

    @classmethod
    def get_all_dcf_overrides(cls, limit: int = 1000) -> dict:
        """Get all DCF overrides. {ticker: {fcf_per_share, beta, growth_rate, updated_at}}"""
        return StockMetaRepo.get_all_dcf_overrides(limit=limit)

    @classmethod
    def get_kr_individual_stocks(cls, existing: set, limit: int) -> list:
        """Get KR market individual stock tickers (excluding ETF/funds, 6-digit filter)."""
        return StockMetaRepo.get_kr_individual_stocks(existing=existing, limit=limit)

    # ── Market Regime History ──────────────────────────────────────────────

    @classmethod
    def save_market_regime(cls, date_str: str, regime_data: dict, vix: float, fear_greed: int) -> bool:
        """Save daily market regime snapshot to DB (update if exists)."""
        return StockMetaRepo.save_market_regime(date_str, regime_data, vix, fear_greed)

    @classmethod
    def get_market_regime_history(cls, days: int = 30) -> list:
        """Return last N days of regime history (newest first)."""
        return StockMetaRepo.get_market_regime_history(days)

    @classmethod
    def get_regime_for_date(cls, date_str: str) -> dict | None:
        """Return regime for a specific date."""
        return StockMetaRepo.get_regime_for_date(date_str)
