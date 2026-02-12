from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class StockMeta(Base):
    """
    주식 종목 메타 정보 모델
    """
    __tablename__ = 'stock_meta'
    
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), unique=True, nullable=False, index=True)
    name_ko = Column(String(100))
    name_en = Column(String(100))
    market_type = Column(String(20))  # KR, US
    exchange_code = Column(String(20)) # NASD, NYSE, KRX 등
    sector = Column(String(100))
    industry = Column(String(100))
    
    # API 호출을 위한 메타데이터 (VTS/Real 환경 대응)
    api_path = Column(String(200)) # 예: /uapi/overseas-stock/v1/quotations/price-detail
    api_tr_id = Column(String(50)) # 예: HHDFS70200200
    api_market_code = Column(String(20)) # 예: NAS, NYS (해외), J (국내)
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # Financials와 1:N 관계
    financials = relationship("Financials", back_populates="stock_meta", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<StockMeta(ticker='{self.ticker}', name='{self.name_ko or self.name_en}')>"

class Financials(Base):
    """
    종목별 재무 지표 이력 모델
    """
    __tablename__ = 'financials'
    
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stock_meta.id'), nullable=False)
    name = Column(String(100)) # 조인 없이 확인하기 위한 종목명 필드 추가
    base_date = Column(DateTime, nullable=False, index=True) # 조회/기준 일자
    
    # 주요 지표 (KIS API 필드 매핑 고려)
    per = Column(Float)
    pbr = Column(Float)
    roe = Column(Float)
    eps = Column(Float)
    bps = Column(Float)
    dividend_yield = Column(Float)
    current_price = Column(Float)
    market_cap = Column(Float)
    
    # 52주 및 시세 상세
    high52 = Column(Float) # 52주 최고가
    low52 = Column(Float)  # 52주 최저가
    volume = Column(Float) # 거래량
    amount = Column(Float) # 거래대금
    
    # 추가 지표 (기술적/기본적)
    rsi = Column(Float)
    ema5 = Column(Float)
    ema10 = Column(Float)
    ema20 = Column(Float)
    ema60 = Column(Float)
    ema120 = Column(Float)
    ema200 = Column(Float)
    dcf_value = Column(Float)
    
    updated_at = Column(DateTime, default=datetime.now)
    
    stock_meta = relationship("StockMeta", back_populates="financials")

    def __repr__(self):
        return f"<Financials(ticker_id={self.stock_id}, date='{self.base_date}')>"

class ApiTrMeta(Base):
    """
    KIS API TR ID 메타 정보 (실전/모의 구분)
    """
    __tablename__ = 'api_tr_meta'
    
    id = Column(Integer, primary_key=True)
    category = Column(String(50), index=True) # 국내주식, 해외주식 등
    api_name = Column(String(100), unique=True, index=True)
    tr_id_real = Column(String(50))
    tr_id_vts = Column(String(50))
    api_path = Column(String(200)) # 기본/Real 경로
    api_path_vts = Column(String(200)) # VTS 전용 경로 (필요시)
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f"<ApiTrMeta(api='{self.api_name}', real='{self.tr_id_real}', vts='{self.tr_id_vts}')>"
