"""
Migration: strategy_state 테이블에 trailing_high 컬럼 추가.

실행 방법:
    python scripts/migrate_trailing_high.py

변경 내용:
    - ADD COLUMN trailing_high TEXT DEFAULT '{}'
    - tick_trade 컬럼은 SQLite DROP 불가 → 코드에서만 무시 (컬럼은 유지)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.database import session_scope
from sqlalchemy import text


def migrate():
    with session_scope() as session:
        # trailing_high 컬럼 존재 여부 확인
        result = session.execute(text("PRAGMA table_info(strategy_state)")).fetchall()
        existing_cols = {row[1] for row in result}

        if "trailing_high" not in existing_cols:
            session.execute(text("ALTER TABLE strategy_state ADD COLUMN trailing_high TEXT NOT NULL DEFAULT '{}'"))
            print("✅ trailing_high 컬럼 추가 완료")
        else:
            print("ℹ️  trailing_high 컬럼 이미 존재 — 스킵")

        print(f"현재 컬럼 목록: {sorted(existing_cols | {'trailing_high'})}")


if __name__ == "__main__":
    migrate()
