from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from .stock_meta import Base

class Portfolio(Base):
    """
    Per-user portfolio information.
    """
    __tablename__ = 'portfolios'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(String(50), unique=True, index=True)
    cash_balance = Column(Float, default=0.0)
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    holdings = relationship("PortfolioHolding", back_populates="portfolio", cascade="all, delete-orphan")

class PortfolioHolding(Base):
    """
    Individual holding within portfolio (quantity and average cost).
    """
    __tablename__ = 'portfolio_holdings'
    
    id = Column(Integer, primary_key=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), index=True)
    ticker = Column(String(20), index=True)
    name = Column(String(100))
    quantity = Column(Integer, default=0)
    buy_price = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0)
    sector = Column(String(100))
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    portfolio = relationship("Portfolio", back_populates="holdings")
