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
    """
    주식 메타 정보 및 재무 데이터 DB 연동 서비스.
    DB 연결은 repositories.database 싱글톤에 위임합니다.
    실제 CRUD 로직은 StockMetaRepo 에 위임합니다.
    """

    @classmethod
    def init_db(cls) -> None:
        """데이터베이스 및 테이블 초기화 (repositories.database 위임)."""
        _db.init_db()

    @classmethod
    def get_session(cls) -> Session:
        return _db.get_session()

    @classmethod
    @contextmanager
    def session_scope(cls) -> Generator[Session, None, None]:
        """DB 세션 자동 관리 (commit/rollback/close).

        쓰기 작업에 사용:
            with StockMetaService.session_scope() as s:
                s.add(obj); ...
        """
        with _db.session_scope() as session:
            yield session

    @classmethod
    @contextmanager
    def session_ro(cls) -> Generator[Session, None, None]:
        """읽기 전용 세션 (commit 없음).

        조회 전용:
            with StockMetaService.session_ro() as s:
                return s.query(...).first()
        """
        with _db.session_ro() as session:
            yield session

    @classmethod
    def upsert_stock_meta(cls, ticker: str, **kwargs) -> Optional[StockMeta]:
        """종목 메타 정보 저장 또는 업데이트"""
        return StockMetaRepo.upsert_stock_meta(ticker, **kwargs)

    @classmethod
    def get_stock_meta(cls, ticker: str) -> Optional[StockMeta]:
        """종목 메타 정보 조회"""
        return StockMetaRepo.get_stock_meta(ticker)

    @classmethod
    def get_stock_meta_bulk(cls, tickers: list) -> list:
        """여러 종목 메타 정보 일괄 조회"""
        return StockMetaRepo.get_stock_meta_bulk(tickers)

    @classmethod
    def find_ticker_by_name(cls, name: str) -> Optional[str]:
        """종목명으로 티커 조회 (name_ko 또는 name_en 대소문자 무시 검색)."""
        return StockMetaRepo.find_ticker_by_name(name)

    @classmethod
    def save_financials(cls, ticker: str, metrics: dict, base_date: datetime = None) -> Optional[Financials]:
        """재무 지표 저장 (최신 데이터 갱신 또는 이력 추가)"""
        return StockMetaRepo.save_financials(ticker, metrics, base_date)

    @classmethod
    def initialize_default_meta(cls, ticker: str) -> Optional[StockMeta]:
        """기본 메타 정보 초기화. TR ID/Path는 DB api_tr_meta 에서 환경(VTS/실전)에 맞게 조회."""
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
        """가장 최근 재무 지표 조회"""
        return StockMetaRepo.get_latest_financials(ticker)

    @classmethod
    def get_all_latest_dcf(cls, limit: int = 1000) -> list:
        """전 종목 최신 DCF 값 및 관련 지표 일괄 조회 (dcf_overrides 병합 포함)."""
        return StockMetaRepo.get_all_latest_dcf(limit=limit)

    @classmethod
    def get_financials_history(cls, ticker: str, limit: int = 2500) -> list:
        """종목 재무 지표 이력 조회 (최신순)"""
        return StockMetaRepo.get_financials_history(ticker, limit)

    @classmethod
    def get_batch_latest_financials(cls, tickers: list) -> dict:
        """여러 종목의 최신 재무 지표를 일괄 조회"""
        return StockMetaRepo.get_batch_latest_financials(tickers)

    @classmethod
    def upsert_api_tr_meta(cls, api_name: str, **kwargs) -> Optional[ApiTrMeta]:
        """API별 TR ID 정보 저장"""
        return StockMetaRepo.upsert_api_tr_meta(api_name, **kwargs)

    @classmethod
    def init_api_tr_meta(cls) -> int:
        """KIS API TR ID 및 경로 정보 초기 설정 및 업데이트"""
        tr_data = [
            # 1. 국내주식
            {"category": "국내주식", "api_name": "주식주문_매도", "tr_id_real": "TTTC0801U", "tr_id_vts": "VTTC0801U", "api_path": "/uapi/domestic-stock/v1/trading/order-cash"},
            {"category": "국내주식", "api_name": "주식주문_매수", "tr_id_real": "TTTC0802U", "tr_id_vts": "VTTC0802U", "api_path": "/uapi/domestic-stock/v1/trading/order-cash"},
            {"category": "국내주식", "api_name": "주식잔고조회", "tr_id_real": "TTTC8434R", "tr_id_vts": "VTTC8434R", "api_path": "/uapi/domestic-stock/v1/trading/inquire-balance"},
            {"category": "국내주식", "api_name": "주식현재가_시세", "tr_id_real": "FHKST01010100", "tr_id_vts": "FHKST01010100", "api_path": "/uapi/domestic-stock/v1/quotations/inquire-price"},
            {"category": "국내주식", "api_name": "국내주식_시가총액순위", "tr_id_real": "FHPST01700000", "tr_id_vts": "FHPST01700000", "api_path": "/uapi/domestic-stock/v1/ranking/market-cap"},

            # 2. 해외주식
            {"category": "해외주식", "api_name": "해외주식_미국매수", "tr_id_real": "TTTT1002U", "tr_id_vts": "VTTT1002U", "api_path": "/uapi/overseas-stock/v1/trading/order"},
            {"category": "해외주식", "api_name": "해외주식_미국매도", "tr_id_real": "TTTT1006U", "tr_id_vts": "VTTT1006U", "api_path": "/uapi/overseas-stock/v1/trading/order"},
            {"category": "해외주식", "api_name": "해외주식_현재가", "tr_id_real": "HHDFS00000300", "tr_id_vts": "HHDFS00000300", "api_path": "/uapi/overseas-price/v1/quotations/price", "api_path_vts": "/uapi/overseas-price/v1/quotations/price"},
            {"category": "해외주식", "api_name": "해외주식_상세시세", "tr_id_real": "HHDFS70200200", "tr_id_vts": "HHDFS00000300", "api_path": "/uapi/overseas-price/v1/quotations/price-detail", "api_path_vts": "/uapi/overseas-price/v1/quotations/price"},
            {"category": "해외주식", "api_name": "해외주식_시가총액순위", "tr_id_real": "HHDFS76350100", "tr_id_vts": "HHDFS76350100", "api_path": "/uapi/overseas-stock/v1/ranking/market-cap"},
            {"category": "해외주식", "api_name": "해외주식_기간별시세", "tr_id_real": "HHDFS76240000", "tr_id_vts": "HHDFS76240000", "api_path": "/uapi/overseas-price/v1/quotations/dailyprice", "api_path_vts": "/uapi/overseas-price/v1/quotations/dailyprice"},
            {"category": "해외주식", "api_name": "해외주식_종목지수환율기간별", "tr_id_real": "FHKST03030100", "tr_id_vts": "FHKST03030100", "api_path": "/uapi/overseas-stock/v1/quotations/inquire-daily-chartprice"},

            # 3. 공통/인증
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
        """API명으로 메타 정보 전체 조회"""
        return StockMetaRepo.get_api_meta(api_name)

    @classmethod
    def get_api_info(cls, api_name: str, is_vts: bool = None) -> tuple[Optional[str], Optional[str]]:
        """환경에 맞는 TR ID와 경로 조회"""
        if is_vts is None:
            from config import Config
            is_vts = Config.KIS_IS_VTS

        meta = cls.get_api_meta(api_name)
        # DB 복구 직후 api_tr_meta가 비어있을 수 있어 1회 자동 초기화
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
        """환경에 맞는 TR ID 조회 (하위 호환)"""
        tr_id, _ = cls.get_api_info(api_name, is_vts)
        return tr_id

    @classmethod
    def upsert_dcf_override(
        cls,
        ticker: str,
        fcf_per_share: float = None,
        beta: float = None,
        growth_rate: float = None,
        fair_value: float = None,
    ) -> Optional[DcfOverride]:
        """사용자 지정 DCF 입력값 저장/업데이트.
        fair_value 를 지정하면 FCF 계산 없이 해당 값을 DCF 적정가로 직접 사용.
        """
        return StockMetaRepo.upsert_dcf_override(
            ticker, fcf_per_share=fcf_per_share, beta=beta,
            growth_rate=growth_rate, fair_value=fair_value,
        )

    @classmethod
    def get_dcf_override(cls, ticker: str) -> Optional[DcfOverride]:
        """사용자 지정 DCF 입력값 조회"""
        return StockMetaRepo.get_dcf_override(ticker)

    @classmethod
    def get_all_dcf_overrides(cls, limit: int = 1000) -> dict:
        """DcfOverride 전체 조회. {ticker: {fcf_per_share, beta, growth_rate, updated_at}}"""
        return StockMetaRepo.get_all_dcf_overrides(limit=limit)

    @classmethod
    def get_kr_individual_stocks(cls, existing: set, limit: int) -> list:
        """KR 시장 개별 종목 티커 목록 조회 (ETF/펀드성 제외, 6자리 숫자 필터)."""
        return StockMetaRepo.get_kr_individual_stocks(existing=existing, limit=limit)

    # ── Market Regime History ──────────────────────────────────────────────

    @classmethod
    def save_market_regime(cls, date_str: str, regime_data: dict, vix: float, fear_greed: int) -> bool:
        """일별 시장 국면 스냅샷을 DB에 저장 (이미 있으면 업데이트)."""
        return StockMetaRepo.save_market_regime(date_str, regime_data, vix, fear_greed)

    @classmethod
    def get_market_regime_history(cls, days: int = 30) -> list:
        """최근 N일 레짐 이력 반환 (최신순)."""
        return StockMetaRepo.get_market_regime_history(days)

    @classmethod
    def get_regime_for_date(cls, date_str: str) -> dict | None:
        """특정 날짜 레짐 반환."""
        return StockMetaRepo.get_regime_for_date(date_str)
