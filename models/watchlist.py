"""UserWatchlist ORM model."""
from sqlalchemy import Column, String, DateTime
from datetime import datetime
from models.stock_meta import Base


class UserWatchlist(Base):
    __tablename__ = "user_watchlist"

    user_id  = Column(String(50), primary_key=True)
    ticker   = Column(String(20), primary_key=True)
    added_at = Column(DateTime, default=datetime.now)
