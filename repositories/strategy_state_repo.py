"""strategy_state table CRUD repository."""
import json
from typing import Optional
from utils.logger import get_logger
from repositories.database import session_scope, session_ro
from models.strategy_state import StrategyState
from models.schemas import BuyCooldownEntry, SplitOrderState, SplitSellOrderState

logger = get_logger("strategy_state_repo")

_USER_FIELDS = (
    "sell_cooldown", "add_buy_cooldown", "panic_locks",
    "split_orders", "sell_split_orders", "trailing_high",
    # Uptrend DCA 상태
    "partial_take_done", "remaining_high", "dca_done", "stop_loss_streak",
)

# Map field names to Pydantic model classes for deserialization
_MODEL_MAP = {
    "add_buy_cooldown": BuyCooldownEntry,
    "split_orders": SplitOrderState,
    "sell_split_orders": SplitSellOrderState,
}


class StrategyStateRepo:

    @classmethod
    def _serialize_value(cls, value):
        """Recursively convert Pydantic models to dicts for JSON serialization."""
        if hasattr(value, 'model_dump'):
            return value.model_dump()
        if isinstance(value, dict):
            return {k: cls._serialize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._serialize_value(v) for v in value]
        return value

    @classmethod
    def _deserialize_field(cls, field: str, raw_json: str) -> dict:
        """Deserialize a JSON field, restoring Pydantic models where mapped."""
        data = json.loads(raw_json or "{}")
        model_cls = _MODEL_MAP.get(field)
        if model_cls and isinstance(data, dict):
            return {k: model_cls(**v) if isinstance(v, dict) else v for k, v in data.items()}
        return data

    @classmethod
    def load(cls, user_id: str) -> dict:
        """Read user_id row and return as dict. Returns empty defaults if not found."""
        try:
            with session_ro() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    return {}
                return {
                    field: cls._deserialize_field(field, getattr(row, field) or "{}")
                    for field in _USER_FIELDS
                    if hasattr(row, field)
                }
        except Exception as e:
            logger.error(f"StrategyStateRepo.load({user_id}) error: {e}")
            return {}

    @classmethod
    def save(cls, user_id: str, user_state: dict) -> None:
        """Upsert user_state dict to DB."""
        try:
            with session_scope() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    row = StrategyState(user_id=user_id)
                    session.add(row)
                for field in _USER_FIELDS:
                    if hasattr(row, field):
                        setattr(row, field, json.dumps(cls._serialize_value(user_state.get(field, {})), ensure_ascii=False))
        except Exception as e:
            logger.error(f"StrategyStateRepo.save({user_id}) error: {e}")

    @classmethod
    def get_field(cls, user_id: str, field: str) -> dict:
        """Fetch a single field."""
        try:
            with session_ro() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    return {}
                return json.loads(getattr(row, field) or "{}")
        except Exception as e:
            logger.error(f"StrategyStateRepo.get_field({user_id}, {field}) error: {e}")
            return {}

    @classmethod
    def set_field(cls, user_id: str, field: str, value: dict) -> None:
        """Update a single field."""
        try:
            with session_scope() as session:
                row = session.query(StrategyState).filter_by(user_id=user_id).first()
                if row is None:
                    row = StrategyState(user_id=user_id)
                    session.add(row)
                setattr(row, field, json.dumps(cls._serialize_value(value), ensure_ascii=False))
        except Exception as e:
            logger.error(f"StrategyStateRepo.set_field({user_id}, {field}) error: {e}")
