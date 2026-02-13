import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from datetime import datetime
from models.stock_meta import Base, StockMeta, Financials, ApiTrMeta, DcfOverride
from models.portfolio import Portfolio, PortfolioHolding
from models.trade_history import TradeHistory
from models.settings import Settings
from utils.logger import get_logger

logger = get_logger("stock_meta_service")

class StockMetaService:
    """
    주식 메타 정보 및 재무 데이터 DB 연동 서비스
    """
    DB_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
        "data", "stock_advisor.db"
    )
    engine = None
    Session = None

    @classmethod
    def init_db(cls):
        """데이터베이스 및 테이블 초기화"""
        if cls.engine:
            return
            
        os.makedirs(os.path.dirname(cls.DB_PATH), exist_ok=True)
        cls.engine = create_engine(
            f"sqlite:///{cls.DB_PATH}", 
            echo=False,
            pool_size=20,
            max_overflow=20
        )
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
                "name": "name", # 종목명 매핑 추가
                "per": "per", "pbr": "pbr", "roe": "roe", 
                "eps": "eps", "bps": "bps", 
                "dividend_yield": "dividend_yield",
                "current_price": "current_price",
                "market_cap": "market_cap",
                "high52": "high52",
                "low52": "low52",
                "volume": "volume",
                "amount": "amount",
                "rsi": "rsi",
                "dcf_value": "dcf_value"
            }
            
            for metric_key, db_field in mapping.items():
                if metric_key in metrics:
                    setattr(financial, db_field, metrics[metric_key])

            # EMA 필드 처리 (dict 형태인 경우 대비)
            if "ema" in metrics and isinstance(metrics["ema"], dict):
                for span, val in metrics["ema"].items():
                    field_name = f"ema{span}"
                    if hasattr(financial, field_name):
                        setattr(financial, field_name, val)
            elif "ema" in metrics: # 단일 값인 경우 ema20 혹은 기본 필드로 처리 시도 (확장성)
                pass

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
    def get_batch_latest_financials(cls, tickers: list):
        """여러 종목의 최신 재무 지표를 일괄 조회"""
        if not tickers:
            return {}
            
        session = cls.get_session()
        # SQLite에서 각 stock_id별 가장 최근의 base_date 행을 가져오는 쿼리 (서브쿼리 활용)
        from sqlalchemy import func
        
        # 1. 각 stock_id별 최신 base_date 찾기
        subquery = session.query(
            Financials.stock_id,
            func.max(Financials.base_date).label('max_date')
        ).group_by(Financials.stock_id).subquery()
        
        # 2. StockMeta와 조인하여 데이터 가져오기
        results = session.query(StockMeta.ticker, Financials)\
            .join(Financials, StockMeta.id == Financials.stock_id)\
            .join(subquery, (Financials.stock_id == subquery.c.stock_id) & (Financials.base_date == subquery.c.max_date))\
            .filter(StockMeta.ticker.in_(tickers))\
            .all()
            
        return {ticker: fin for ticker, fin in results}

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
    def upsert_dcf_override(cls, ticker: str, fcf_per_share: float, beta: float, growth_rate: float):
        """사용자 지정 DCF 입력값 저장/업데이트"""
        session = cls.get_session()
        try:
            row = session.query(DcfOverride).filter_by(ticker=ticker).first()
            if not row:
                row = DcfOverride(ticker=ticker)
                session.add(row)
            
            row.fcf_per_share = fcf_per_share
            row.beta = beta
            row.growth_rate = growth_rate
            row.updated_at = datetime.now()
            session.commit()
            return row
        except Exception as e:
            session.rollback()
            logger.error(f"Error upserting DCF override for {ticker}: {e}")
            return None

    @classmethod
    def get_dcf_override(cls, ticker: str):
        """사용자 지정 DCF 입력값 조회"""
        session = cls.get_session()
        return session.query(DcfOverride).filter_by(ticker=ticker).first()

    @classmethod
    def init_api_tr_meta(cls):
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
            {"category": "해외주식", "api_name": "해외주식_상세시세", "tr_id_real": "HHDFS70200200", "tr_id_vts": "HHDFS00000300", "api_path": "/uapi/overseas-price/v1/quotations/price-detail", "api_path_vts": "/uapi/overseas-price/v1/quotations/price"}, # VTS는 price-detail이 없으므로 price로 대체
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
    def get_api_meta(cls, api_name: str):
        """API명으로 메타 정보 전체 조회"""
        session = cls.get_session()
        return session.query(ApiTrMeta).filter_by(api_name=api_name).first()

    @classmethod
    def get_api_info(cls, api_name: str, is_vts: bool = None):
        """환경에 맞는 TR ID와 경로 조회"""
        if is_vts is None:
            from config import Config
            is_vts = Config.KIS_IS_VTS
            
        meta = cls.get_api_meta(api_name)
        if not meta:
            return None, None
            
        tr_id = meta.tr_id_vts if is_vts else meta.tr_id_real
        path = (meta.api_path_vts if is_vts and meta.api_path_vts else meta.api_path)
        return tr_id, path

    @classmethod
    def get_tr_id(cls, api_name: str, is_vts: bool = None):
        """환경에 맞는 TR ID 조회 (하위 호환)"""
        tr_id, _ = cls.get_api_info(api_name, is_vts)
        return tr_id
