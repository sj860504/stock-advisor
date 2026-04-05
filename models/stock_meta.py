from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class StockMeta(Base):
    """
    Stock metadata model.
    """
    __tablename__ = 'stock_meta'
    
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), unique=True, nullable=False, index=True)
    name_ko = Column(String(100))
    name_en = Column(String(100))
    market_type = Column(String(20))  # KR, US
    exchange_code = Column(String(20)) # NASD, NYSE, KRX, etc.
    sector = Column(String(100))
    industry = Column(String(100))
    
    # Metadata for API calls (VTS/Real environment)
    api_path = Column(String(200)) # e.g.: /uapi/overseas-stock/v1/quotations/price-detail
    api_tr_id = Column(String(50)) # e.g.: HHDFS70200200
    api_market_code = Column(String(20)) # e.g.: NAS, NYS (overseas), J (domestic)
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 1:N relationship with Financials
    financials = relationship("Financials", back_populates="stock_meta", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<StockMeta(ticker='{self.ticker}', name='{self.name_ko or self.name_en}')>"

class Financials(Base):
    """
    Per-stock financial metrics history model.
    """
    __tablename__ = 'financials'
    
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey('stock_meta.id'), nullable=False, index=True)
    name = Column(String(100)) # Stock name field added for quick lookup without join
    base_date = Column(DateTime, nullable=False, index=True) # Base/query date
    
    # Key metrics (mapped to KIS API fields)
    per = Column(Float)
    pbr = Column(Float)
    roe = Column(Float)
    eps = Column(Float)
    bps = Column(Float)
    dividend_yield = Column(Float)
    current_price = Column(Float)
    market_cap = Column(Float)
    
    # 52-week and price details
    high52 = Column(Float) # 52-week high
    low52 = Column(Float)  # 52-week low
    volume = Column(Float) # Volume
    amount = Column(Float) # Trading amount
    
    # Additional indicators (technical/fundamental)
    rsi = Column(Float)
    ema5 = Column(Float)
    ema10 = Column(Float)
    ema20 = Column(Float)
    ema60 = Column(Float)
    ema100 = Column(Float)
    ema120 = Column(Float)
    ema200 = Column(Float)
    dcf_value = Column(Float)
    
    updated_at = Column(DateTime, default=datetime.now)
    
    stock_meta = relationship("StockMeta", back_populates="financials")

    def __repr__(self):
        return f"<Financials(ticker_id={self.stock_id}, date='{self.base_date}')>"

class ApiTrMeta(Base):
    """
    KIS API TR ID metadata (real/VTS environment).
    """
    __tablename__ = 'api_tr_meta'
    
    id = Column(Integer, primary_key=True)
    category = Column(String(50), index=True) # Domestic stocks, overseas stocks, etc.
    api_name = Column(String(100), unique=True, index=True)
    tr_id_real = Column(String(50))
    tr_id_vts = Column(String(50))
    api_path = Column(String(200)) # Default/Real path
    api_path_vts = Column(String(200)) # VTS-specific path (if needed)
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f"<ApiTrMeta(api='{self.api_name}', real='{self.tr_id_real}', vts='{self.tr_id_vts}')>"

class DcfOverride(Base):
    """
    User-specified DCF input overrides.
    When fair_value is set, it is used directly as DCF fair value without FCF calculation.
    """
    __tablename__ = 'dcf_overrides'

    ticker = Column(String(20), primary_key=True)
    fcf_per_share = Column(Float)
    beta = Column(Float)
    growth_rate = Column(Float)
    fair_value = Column(Float)   # Directly specified fair value (overrides FCF calculation)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f"<DcfOverride(ticker='{self.ticker}')>"


class MarketRegimeHistory(Base):
    """Daily market regime snapshot history."""
    __tablename__ = 'market_regime_history'

    id           = Column(Integer, primary_key=True, autoincrement=True)
    date         = Column(String(10), nullable=False, unique=True, index=True)  # YYYY-MM-DD
    status       = Column(String(10))           # Bull / Bear / Neutral
    regime_score = Column(Integer)              # 0~100
    vix          = Column(Float)
    fear_greed   = Column(Integer)
    us_10y_yield = Column(Float)
    spx_price    = Column(Float)
    spx_ma200    = Column(Float)
    spx_diff_pct = Column(Float)
    forward_pe   = Column(Float)                # S&P 500 Forward P/E (현재값)
    components_json = Column(String)            # JSON: {technical, vix, fear_greed, economic, other, other_detail}
    created_at   = Column(DateTime, default=datetime.now)

    def __repr__(self):
        return f"<MarketRegimeHistory(date='{self.date}', status='{self.status}', score={self.regime_score})>"
