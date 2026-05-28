"""TickerSignalCache repository — score 캐시 read/write 분리용."""
import json
from datetime import datetime
from typing import Dict, Iterable, Optional

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models.stock_meta import TickerSignalCache
from repositories.database import session_scope, session_ro
from utils.logger import get_logger

logger = get_logger("signal_cache_repo")


class SignalCacheRepo:
    """ticker_signal_cache CRUD. UI는 load_all/load_many만 사용, 백그라운드 잡만 upsert_many 사용."""

    @classmethod
    def load_all(cls) -> Dict[str, dict]:
        """Return {ticker: {score, reasons, breakdown, calculated_at}} for all cached tickers."""
        try:
            with session_ro() as session:
                rows = session.query(TickerSignalCache).all()
                return {r.ticker: cls._row_to_dict(r) for r in rows}
        except Exception as e:
            logger.error(f"❌ load_all error: {e}")
            return {}

    @classmethod
    def load_many(cls, tickers: Iterable[str]) -> Dict[str, dict]:
        """Return {ticker: dict} for given tickers."""
        try:
            with session_ro() as session:
                rows = session.query(TickerSignalCache).filter(
                    TickerSignalCache.ticker.in_(list(tickers))
                ).all()
                return {r.ticker: cls._row_to_dict(r) for r in rows}
        except Exception as e:
            logger.error(f"❌ load_many error: {e}")
            return {}

    @classmethod
    def upsert_many(cls, entries: list[dict]) -> int:
        """Bulk upsert. entries=[{ticker, score, reasons, breakdown}, ...]. Returns affected row count."""
        if not entries:
            return 0
        now = datetime.now()
        try:
            with session_scope() as session:
                count = 0
                for e in entries:
                    payload = {
                        "ticker":         e["ticker"],
                        "score":          int(e.get("score") or 0),
                        "reasons_json":   json.dumps(e.get("reasons") or [], ensure_ascii=False),
                        "breakdown_json": json.dumps(e.get("breakdown") or {}, ensure_ascii=False),
                        "calculated_at":  now,
                    }
                    stmt = sqlite_insert(TickerSignalCache).values(**payload)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ticker"],
                        set_={
                            "score":          stmt.excluded.score,
                            "reasons_json":   stmt.excluded.reasons_json,
                            "breakdown_json": stmt.excluded.breakdown_json,
                            "calculated_at":  stmt.excluded.calculated_at,
                        },
                    )
                    session.execute(stmt)
                    count += 1
                logger.debug(f"💾 SignalCache upserted: {count} rows")
                return count
        except Exception as e:
            logger.error(f"❌ upsert_many error: {e}")
            return 0

    @staticmethod
    def _row_to_dict(r: TickerSignalCache) -> dict:
        def _parse(s: Optional[str], default):
            if not s:
                return default
            try:
                return json.loads(s)
            except Exception:
                return default
        return {
            "score":         r.score,
            "reasons":       _parse(r.reasons_json, []),
            "breakdown":     _parse(r.breakdown_json, {}),
            "calculated_at": r.calculated_at.isoformat() if r.calculated_at else None,
        }
