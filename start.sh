#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh  –  Sean's Stock Advisor 시작 스크립트
#
# 역할:
#   1. 필수 디렉터리(data/, logs/) 생성
#   2. .env 파일 존재 여부 확인
#   3. Python venv 생성/활성화 및 패키지 설치
#   4. DB 마이그레이션 (alembic upgrade head)
#   5. uvicorn 실행  (FastAPI = 백엔드 API + 프론트엔드 정적 파일 통합 서빙)
#
# 사용법:
#   chmod +x start.sh
#   ./start.sh              # 기본 (host=0.0.0.0, port=8000)
#   ./start.sh --port 9000  # 포트 변경
#   ./start.sh --reload     # 개발 모드 (코드 변경 시 자동 재시작)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── 0. 스크립트 위치를 프로젝트 루트로 고정 ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="0.0.0.0"
PORT="8000"
RELOAD_FLAG=""

# ── 인자 파싱 ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)    PORT="$2";  shift 2 ;;
        --host)    HOST="$2";  shift 2 ;;
        --reload)  RELOAD_FLAG="--reload"; shift ;;
        *) echo "알 수 없는 옵션: $1"; exit 1 ;;
    esac
done

echo "======================================================"
echo "  Sean's Stock Advisor"
echo "======================================================"

# ── 1. 필수 디렉터리 생성 ─────────────────────────────────────────────────────
mkdir -p data logs
echo "[1/5] 디렉터리 확인 완료 (data/, logs/)"

# ── 2. .env 파일 확인 ─────────────────────────────────────────────────────────
if [[ ! -f ".env" ]]; then
    echo ""
    echo "⚠️  .env 파일이 없습니다. .env.example 을 복사하여 설정하세요."
    echo ""
    echo "  cp .env.example .env"
    echo "  vi .env   # KIS_APP_KEY, KIS_APP_SECRET 등 입력"
    echo ""
    exit 1
fi
echo "[2/5] .env 파일 확인 완료"

# ── 3. Python venv 설정 ───────────────────────────────────────────────────────
if [[ ! -d "venv" ]]; then
    echo "[3/4] venv 없음 → python3 -m venv venv 생성 중..."
    python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
echo "[3/5] venv 활성화 완료 ($(python --version))"

# requirements.txt 가 venv보다 최신이면 재설치
REQ="requirements.txt"
STAMP="venv/.install_stamp"
if [[ ! -f "$STAMP" ]] || [[ "$REQ" -nt "$STAMP" ]]; then
    echo "      패키지 설치/업데이트 중 (requirements.txt)..."
    pip install -q --upgrade pip
    pip install -q -r "$REQ"
    touch "$STAMP"
    echo "      패키지 설치 완료"
fi

# ── 4. DB 마이그레이션 ────────────────────────────────────────────────────────
echo "[4/5] DB 마이그레이션 실행 (alembic upgrade head)..."
alembic upgrade head
echo "      마이그레이션 완료"

# ── 5. uvicorn 실행 ───────────────────────────────────────────────────────────
echo "[5/5] 서버 시작"
echo ""
echo "  URL  : http://${HOST}:${PORT}       ← 대시보드 (프론트엔드)"
echo "  API  : http://${HOST}:${PORT}/api   ← REST API"
echo "  Docs : http://${HOST}:${PORT}/docs  ← Swagger UI"
echo ""
echo "  종료: Ctrl+C"
echo "======================================================"

# SQLite DB 및 테이블 초기화는 FastAPI lifespan 안에서 자동 실행됩니다.
# shellcheck disable=SC2086
exec uvicorn main:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    $RELOAD_FLAG
