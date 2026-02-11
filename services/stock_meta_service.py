import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from datetime import datetime
from models.stock_meta import Base, StockMeta, Financials, ApiTrMeta
from utils.logger import get_logger

logger = get_logger("stock_meta_service")

class StockMetaService:
    """
    주식 메타 정보 및 재무 데이터 DB 연동 서비스
    """
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_advisor.db")
    engine = None
    Session = None

    @classmethod
    def init_db(cls):
        """데이터베이스 및 테이블 초기화"""
        if cls.engine:
            return
            
        os.makedirs(os.path.dirname(cls.DB_PATH), exist_ok=True)
        cls.engine = create_engine(f"sqlite:///{cls.DB_PATH}", echo=False)
        Base.metadata.create_all(cls.engine)
        cls.Session = scoped_session(sessionmaker(bind=cls.engine))
        logger.info(f"📁 Database initialized at: {cls.DB_PATH}")

    @classmethod
    def get_session(cls):
        if not cls.Session:
            cls.init_db()
        return cls.Session()

    @classmethod
    def upsert_stock_meta(cls, ticker: str, **kwargs):
        """종목 메타 정보 저장 또는 업데이트"""
        session = cls.get_session()
        try:
            stock = session.query(StockMeta).filter_by(ticker=ticker).first()
            if not stock:
                stock = StockMeta(ticker=ticker)
                session.add(stock)
            
            for key, value in kwargs.items():
                if hasattr(stock, key) and value is not None:
                    setattr(stock, key, value)
            
            session.commit()
            return stock
        except Exception as e:
            session.rollback()
            logger.error(f"Error upserting stock meta for {ticker}: {e}")
            return None

    @classmethod
    def get_stock_meta(cls, ticker: str):
        """종목 메타 정보 조회"""
        session = cls.get_session()
        return session.query(StockMeta).filter_by(ticker=ticker).first()

    @classmethod
    def save_financials(cls, ticker: str, metrics: dict, base_date: datetime = None):
        """재무 지표 저장 (최신 데이터 갱신 또는 이력 추가)"""
        if not metrics:
            return None
            
        session = cls.get_session()
        try:
            stock = session.query(StockMeta).filter_by(ticker=ticker).first()
            if not stock:
                logger.warning(f"Stock meta not found for {ticker}. Creating basic meta first.")
                stock = cls.upsert_stock_meta(ticker, market_type="KR" if ticker.isdigit() else "US")

            if base_date is None:
                base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

            # 해당 날짜의 데이터가 이미 있는지 확인
            existing = session.query(Financials).filter_by(stock_id=stock.id, base_date=base_date).first()
            if existing:
                financial = existing
            else:
                financial = Financials(stock_id=stock.id, base_date=base_date)
                session.add(financial)

            # 지표 매핑
            mapping = {
                "per": "per", "pbr": "pbr", "roe": "roe", 
                "eps": "eps", "bps": "bps", 
                "dividend_yield": "dividend_yield",
                "current_price": "current_price",
                "market_cap": "market_cap"
            }
            
            for metric_key, db_field in mapping.items():
                if metric_key in metrics:
                    setattr(financial, db_field, metrics[metric_key])

            financial.updated_at = datetime.now()
            session.commit()
            return financial
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving financials for {ticker}: {e}")
            return None

    @classmethod
    def initialize_default_meta(cls, ticker: str):
        """기본 메타 정보 초기화 (404 방지용 기본 경로 설정)"""
        if ticker.isdigit(): # 국내
            return cls.upsert_stock_meta(
                ticker, 
                market_type="KR",
                api_path="/uapi/domestic-stock/v1/quotations/inquire-price",
                api_tr_id="FHKST01010100",
                api_market_code="J"
            )
        else: # 해외
            return cls.upsert_stock_meta(
                ticker,
                market_type="US",
                api_path="/uapi/overseas-stock/v1/quotations/price-detail",
                api_tr_id="HHDFS70200200",
                api_market_code="NAS" # 기본 NAS
            )

    @classmethod
    def get_latest_financials(cls, ticker: str):
        """가장 최근 재무 지표 조회"""
        session = cls.get_session()
        stock = session.query(StockMeta).filter_by(ticker=ticker).first()
        if not stock:
            return None
            
        return session.query(Financials).filter(Financials.stock_id == stock.id)\
                      .order_by(Financials.base_date.desc()).first()

    @classmethod
    def upsert_api_tr_meta(cls, api_name: str, **kwargs):
        """API별 TR ID 정보 저장"""
        session = cls.get_session()
        try:
            meta = session.query(ApiTrMeta).filter_by(api_name=api_name).first()
            if not meta:
                meta = ApiTrMeta(api_name=api_name)
                session.add(meta)
            
            for key, value in kwargs.items():
                if hasattr(meta, key):
                    setattr(meta, key, value)
            
            session.commit()
            return meta
        except Exception as e:
            session.rollback()
            logger.error(f"Error upserting api tr meta for {api_name}: {e}")
            return None

    @classmethod
    def get_tr_id(cls, api_name: str, is_vts: bool = True):
        """환경에 맞는 TR ID 조회"""
        session = cls.get_session()
        meta = session.query(ApiTrMeta).filter_by(api_name=api_name).first()
        if not meta:
            return None
        return meta.tr_id_vts if is_vts else meta.tr_id_real
