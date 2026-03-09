"""System settings repository."""
from typing import Optional

from models.settings import Settings
from repositories.database import get_session, session_scope
from utils.logger import get_logger

logger = get_logger("settings_repo")


class SettingsRepo:
    """Settings table CRUD."""

    @classmethod
    def get(cls, key: str) -> Optional[str]:
        """Fetch setting value. Returns None if not found."""
        session = get_session()
        try:
            row = session.query(Settings).filter_by(key=key).first()
            return row.value if row else None
        finally:
            session.close()

    @classmethod
    def set(cls, key: str, value: str, description: str = "") -> Optional[Settings]:
        """Upsert setting value. Returns detached Settings on success, None on failure."""
        try:
            with session_scope() as session:
                row = session.query(Settings).filter_by(key=key).first()
                if row:
                    row.value = str(value)
                else:
                    row = Settings(key=key, value=str(value), description=description)
                    session.add(row)
                session.flush()
                session.expunge(row)
                return row
        except Exception as e:
            logger.error(f"❌ Error setting {key}: {e}")
            return None

    @classmethod
    def get_all(cls) -> dict:
        """Fetch all settings as {key: {value, description}}."""
        session = get_session()
        try:
            rows = session.query(Settings).all()
            return {r.key: {"value": r.value, "description": r.description} for r in rows}
        finally:
            session.close()

    @classmethod
    def upsert_many(cls, items: dict[str, tuple[str, str]]) -> None:
        """items: {key: (value, description)} — insert only if key does not exist."""
        try:
            with session_scope() as session:
                existing = {r.key for r in session.query(Settings.key).all()}
                for key, (val, desc) in items.items():
                    if key not in existing:
                        session.add(Settings(key=key, value=val, description=desc))
        except Exception as e:
            logger.error(f"❌ Error in upsert_many settings: {e}")
