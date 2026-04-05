"""
Migration: market_regime_history 테이블에 forward_pe 컬럼 추가.

변경 내용:
    - ADD COLUMN forward_pe REAL  (S&P 500 Forward P/E 현재값)

실행 방법:
    [로컬]
        source .venv/bin/activate
        python scripts/migrate_forward_pe.py

    [운영서버 — Docker 컨테이너 내부]
        docker compose exec stock-advisor python scripts/migrate_forward_pe.py

    [운영서버 — 컨테이너 외부에서 직접 DB 지정]
        python scripts/migrate_forward_pe.py --db /path/to/stock_advisor.db
"""
import sys
import os
import sqlite3
import argparse

# ── 실행 경로에 상관없이 프로젝트 루트를 sys.path에 추가 ──────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 기본 DB 경로 (프로젝트 루트 기준 또는 Docker 내부 /app/data/)
DEFAULT_DB_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stock_advisor.db"),
    "/app/data/stock_advisor.db",
]


def resolve_db_path(cli_path: str | None) -> str:
    if cli_path:
        return cli_path
    for p in DEFAULT_DB_CANDIDATES:
        if os.path.exists(p):
            return p
    # 첫 번째 후보 경로 반환 (존재하지 않아도 SQLAlchemy 경로로 시도)
    return DEFAULT_DB_CANDIDATES[0]


def migrate_sqlite(db_path: str) -> None:
    """sqlite3 직접 연결로 마이그레이션 수행 — 의존성 최소화."""
    print(f"DB 경로: {db_path}")
    if not os.path.exists(db_path):
        print(f"❌ DB 파일 없음: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(market_regime_history)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        if "forward_pe" not in existing_cols:
            cursor.execute("ALTER TABLE market_regime_history ADD COLUMN forward_pe REAL")
            conn.commit()
            print("✅ forward_pe 컬럼 추가 완료")
        else:
            print("ℹ️  forward_pe 컬럼 이미 존재 — 스킵")

        # 결과 확인
        cursor.execute("PRAGMA table_info(market_regime_history)")
        final_cols = sorted(row[1] for row in cursor.fetchall())
        print(f"현재 컬럼 목록: {final_cols}")
    except Exception as e:
        conn.rollback()
        print(f"❌ 마이그레이션 실패: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="migrate: forward_pe 컬럼 추가")
    parser.add_argument("--db", help="DB 파일 경로 (미지정 시 자동 탐색)", default=None)
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    migrate_sqlite(db_path)
