"""UserWatchlist repository."""
from datetime import datetime

from models.watchlist import UserWatchlist
from repositories.database import session_ro, session_scope
from utils.logger import get_logger

logger = get_logger("watchlist_repo")


class WatchlistRepo:

    @classmethod
    def get_items(cls, user_id: str) -> list[UserWatchlist]:
        """유저의 Watchlist 전체 반환 (detached)."""
        with session_ro() as session:
            rows = (
                session.query(UserWatchlist)
                .filter_by(user_id=user_id)
                .order_by(UserWatchlist.added_at.desc())
                .all()
            )
            return rows

    @classmethod
    def get_tickers(cls, user_id: str) -> list[str]:
        """유저의 Watchlist 티커 목록만 반환."""
        return [row.ticker for row in cls.get_items(user_id)]

    @classmethod
    def add_ticker(cls, user_id: str, ticker: str) -> bool:
        """티커 추가. 이미 존재하면 False 반환."""
        try:
            with session_scope() as session:
                exists = session.query(UserWatchlist).filter_by(user_id=user_id, ticker=ticker).first()
                if exists:
                    return False
                session.add(UserWatchlist(user_id=user_id, ticker=ticker, added_at=datetime.now()))
            logger.info(f"✅ Watchlist add: {user_id}/{ticker}")
            return True
        except Exception as e:
            logger.error(f"❌ Watchlist add error {user_id}/{ticker}: {e}")
            return False

    @classmethod
    def remove_ticker(cls, user_id: str, ticker: str) -> bool:
        """티커 제거. 존재하지 않으면 False 반환."""
        try:
            with session_scope() as session:
                row = session.query(UserWatchlist).filter_by(user_id=user_id, ticker=ticker).first()
                if not row:
                    return False
                session.delete(row)
            logger.info(f"🗑️ Watchlist remove: {user_id}/{ticker}")
            return True
        except Exception as e:
            logger.error(f"❌ Watchlist remove error {user_id}/{ticker}: {e}")
            return False
