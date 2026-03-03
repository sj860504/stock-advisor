"""strategy_state 테이블 CRUD 레포지토리."""
import json
from typing import Optional
from utils.logger import get_logger
from repositories.database import session_scope, session_ro
from models.strategy_state import StrategyState

logger = get_logger("strategy_state_repo")

_USER_FIELDS = ("sell_cooldown", "add_buy_cooldown", "panic_locks", "tick_trade")


class StrategyStateRepo:

    @classmethod
    def load(cls, user_id: str) -> dict:
        """user_id 행을 읽어 dict로 반환. 없으면 빈 기본값 반환."""
        try:
            with session_ro() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    return {}
                return {
                    field: json.loads(getattr(row, field) or "{}")
                    for field in _USER_FIELDS
                }
        except Exception as e:
            logger.error(f"StrategyStateRepo.load({user_id}) 오류: {e}")
            return {}

    @classmethod
    def save(cls, user_id: str, user_state: dict) -> None:
        """user_state dict를 DB에 upsert."""
        try:
            with session_scope() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    row = StrategyState(user_id=user_id)
                    session.add(row)
                for field in _USER_FIELDS:
                    setattr(row, field, json.dumps(user_state.get(field, {}), ensure_ascii=False))
        except Exception as e:
            logger.error(f"StrategyStateRepo.save({user_id}) 오류: {e}")

    @classmethod
    def get_field(cls, user_id: str, field: str) -> dict:
        """단일 필드만 조회."""
        try:
            with session_ro() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    return {}
                return json.loads(getattr(row, field) or "{}")
        except Exception as e:
            logger.error(f"StrategyStateRepo.get_field({user_id}, {field}) 오류: {e}")
            return {}

    @classmethod
    def set_field(cls, user_id: str, field: str, value: dict) -> None:
        """단일 필드만 업데이트."""
        try:
            with session_scope() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    row = StrategyState(user_id=user_id)
                    session.add(row)
                setattr(row, field, json.dumps(value, ensure_ascii=False))
        except Exception as e:
            logger.error(f"StrategyStateRepo.set_field({user_id}, {field}) 오류: {e}")
