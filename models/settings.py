from sqlalchemy import Column, String
from .stock_meta import Base

class Settings(Base):
    """
    시스템/전략 설정 모델
    """
    __tablename__ = 'settings'

    key = Column(String(50), primary_key=True)
    value = Column(String(255))
    description = Column(String(255))

    def __repr__(self):
        return f"<Settings(key='{self.key}', value='{self.value}')>"
