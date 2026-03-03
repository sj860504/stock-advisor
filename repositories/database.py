"""중앙 DB 싱글톤 — 엔진/세션 관리.

모든 Repository / Service 는 이 모듈의 get_session(), session_scope(),
session_ro() 를 통해 SQLAlchemy 세션을 획득합니다.
"""
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Generator
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy.exc import OperationalError
from utils.logger import get_logger

logger = get_logger("database")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "stock_advisor.db",
)

_engine: Engine | None = None
_Session: scoped_session | None = None


def _create_engine_and_session() -> None:
    global _engine, _Session
    _engine = create_engine(
        f"sqlite:///{DB_PATH}",
        echo=False,
        pool_size=50,
        max_overflow=50,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    _Session = scoped_session(sessionmaker(bind=_engine))


def init_db() -> None:
    """테이블 생성 및 엔진/세션 초기화 (멱등)."""
    global _engine, _Session
    if _engine:
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _create_engine_and_session()

    # 모든 ORM 모델을 Base 메타데이터에 등록시킨 뒤 create_all
    from models.stock_meta import Base  # noqa: F401
    from models.portfolio import Portfolio, PortfolioHolding  # noqa: F401
    from models.trade_history import TradeHistory  # noqa: F401
    from models.settings import Settings  # noqa: F401
    from models.strategy_state import StrategyState  # noqa: F401

    try:
        Base.metadata.create_all(_engine)
        logger.info(f"📁 Database initialized at: {DB_PATH}")
    except OperationalError as e:
        if "unsupported file format" in str(e).lower():
            backup_path = f"{DB_PATH}.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            logger.error(f"❌ Corrupted sqlite detected. Backing up to {backup_path}")
            try:
                if os.path.exists(DB_PATH):
                    os.replace(DB_PATH, backup_path)
                _engine = None
                if _Session:
                    _Session.remove()
                _Session = None
                _create_engine_and_session()
                Base.metadata.create_all(_engine)
                logger.warning("⚠️ Recreated sqlite DB after corruption recovery")
            except Exception as recover_err:
                logger.error(f"❌ DB recovery failed: {recover_err}")
                raise
        else:
            raise


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    return _engine


def get_session() -> Session:
    if _Session is None:
        init_db()
    return _Session()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """쓰기 세션 — commit / rollback / close 자동 관리."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_ro() -> Generator[Session, None, None]:
    """읽기 전용 세션 — close 자동 관리."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()
