"""시스템 설정 Repository."""
from typing import Optional

from models.settings import Settings
from repositories.database import get_session, session_scope
from utils.logger import get_logger

logger = get_logger("settings_repo")


class SettingsRepo:
    """Settings 테이블 CRUD."""

    @classmethod
    def get(cls, key: str) -> Optional[str]:
        """설정값 조회. 없으면 None."""
        session = get_session()
        try:
            row = session.query(Settings).filter_by(key=key).first()
            return row.value if row else None
        finally:
            session.close()

    @classmethod
    def set(cls, key: str, value: str, description: str = "") -> Optional[Settings]:
        """설정값 upsert. 성공 시 detached Settings, 실패 시 None."""
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
        """{key: {value, description}} 전체 조회."""
        session = get_session()
        try:
            rows = session.query(Settings).all()
            return {r.key: {"value": r.value, "description": r.description} for r in rows}
        finally:
            session.close()

    @classmethod
    def upsert_many(cls, items: dict[str, tuple[str, str]]) -> None:
        """items: {key: (value, description)} — 없는 것만 삽입."""
        try:
            with session_scope() as session:
                existing = {r.key for r in session.query(Settings.key).all()}
                for key, (val, desc) in items.items():
                    if key not in existing:
                        session.add(Settings(key=key, value=val, description=desc))
        except Exception as e:
            logger.error(f"❌ Error in upsert_many settings: {e}")
