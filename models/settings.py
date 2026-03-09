from sqlalchemy import Column, String
from .stock_meta import Base

class Settings(Base):
    """
    System/strategy settings model.
    """
    __tablename__ = 'settings'

    key = Column(String(50), primary_key=True)
    value = Column(String(255))
    description = Column(String(255))

    def __repr__(self):
        return f"<Settings(key='{self.key}', value='{self.value}')>"
