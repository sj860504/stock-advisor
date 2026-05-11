#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start.sh  –  Sean's Stock Advisor 시작 스크립트 (재시작 안전판)
#
# 역할:
#   0. (옵션) git fetch + pull 로 최신 develop 동기화
#   1. 기존 uvicorn 프로세스 종료
#   2. __pycache__ 캐시 정리 (옛 .pyc 로 옛 코드 로드 방지)
#   3. 필수 디렉터리(data/, logs/) 생성, .env 확인
#   4. Python venv 생성/활성화 및 패키지 설치
#   5. DB 마이그레이션 (alembic upgrade head)
#   6. uvicorn 실행 — 기본 백그라운드(nohup) / --foreground 옵션 시 포그라운드
#
# 사용법:
#   chmod +x start.sh
#   ./start.sh                  # 기본: pull + 캐시 정리 + 백그라운드 실행 (host=0.0.0.0, port=8000)
#   ./start.sh --foreground     # 포그라운드 실행 (Ctrl+C 종료)
#   ./start.sh --no-pull        # git pull 건너뜀
#   ./start.sh --port 9000      # 포트 변경
#   ./start.sh --reload         # 개발 모드 (코드 변경 시 자동 재시작, foreground 강제)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── 0. 스크립트 위치를 프로젝트 루트로 고정 ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="0.0.0.0"
PORT="8000"
RELOAD_FLAG=""
DO_PULL=1
FOREGROUND=0

# ── 인자 파싱 ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)        PORT="$2";  shift 2 ;;
        --host)        HOST="$2";  shift 2 ;;
        --reload)      RELOAD_FLAG="--reload"; FOREGROUND=1; shift ;;
        --no-pull)     DO_PULL=0; shift ;;
        --foreground)  FOREGROUND=1; shift ;;
        *) echo "알 수 없는 옵션: $1"; exit 1 ;;
    esac
done

echo "======================================================"
echo "  Sean's Stock Advisor"
echo "======================================================"

# ── 1. git 최신 동기화 ───────────────────────────────────────────────────────
if [[ "$DO_PULL" -eq 1 ]] && [[ -d ".git" ]]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [[ -n "$BRANCH" ]] && [[ "$BRANCH" != "HEAD" ]]; then
        echo "[1/7] git fetch + pull origin $BRANCH ..."
        git fetch origin "$BRANCH" 2>&1 | sed 's/^/      /'
        git pull --ff-only origin "$BRANCH" 2>&1 | sed 's/^/      /' || {
            echo "      ⚠️ fast-forward pull 실패 (로컬 변경/divergence). 수동 처리 필요."
        }
    else
        echo "[1/7] git: detached HEAD — pull 건너뜀"
    fi
else
    echo "[1/7] git pull 건너뜀 (--no-pull 또는 비-git 디렉토리)"
fi

# ── 2. 기존 uvicorn 프로세스 종료 ────────────────────────────────────────────
EXISTING_PIDS=$(pgrep -f "uvicorn main:app" || true)
if [[ -n "$EXISTING_PIDS" ]]; then
    echo "[2/7] 기존 프로세스 종료: $EXISTING_PIDS"
    # shellcheck disable=SC2086
    kill -9 $EXISTING_PIDS 2>/dev/null || true
    sleep 2
    # 끝까지 안 죽으면 한 번 더
    STILL=$(pgrep -f "uvicorn main:app" || true)
    if [[ -n "$STILL" ]]; then
        echo "      ⚠️ 잔존 PID 강제 종료: $STILL"
        # shellcheck disable=SC2086
        kill -9 $STILL 2>/dev/null || true
        sleep 1
    fi
else
    echo "[2/7] 기존 프로세스 없음"
fi

# ── 3. __pycache__ 캐시 정리 (.pyc 옛 코드 로드 방지) ─────────────────────────
echo "[3/7] __pycache__ 캐시 정리..."
find . -path ./venv -prune -o -name "__pycache__" -type d -print 2>/dev/null | xargs rm -rf 2>/dev/null || true
# .py mtime 갱신해서 .pyc 비교 명확하게
find services/ utils/ routers/ models/ repositories/ -name "*.py" -exec touch {} \; 2>/dev/null || true

# ── 4. 필수 디렉터리 생성 + .env 확인 ─────────────────────────────────────────
mkdir -p data logs
if [[ ! -f ".env" ]]; then
    echo ""
    echo "⚠️  .env 파일이 없습니다. .env.example 을 복사하여 설정하세요."
    echo ""
    echo "  cp .env.example .env"
    echo "  vi .env   # KIS_APP_KEY, KIS_APP_SECRET 등 입력"
    echo ""
    exit 1
fi
echo "[4/7] 디렉터리 + .env 확인 완료"

# ── 5. Python venv 설정 ───────────────────────────────────────────────────────
if [[ ! -d "venv" ]]; then
    echo "[5/7] venv 없음 → python3 -m venv venv 생성 중..."
    python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
echo "[5/7] venv 활성화 ($(python --version))"

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

# ── 6. DB 마이그레이션 ────────────────────────────────────────────────────────
echo "[6/7] DB 마이그레이션 실행 (alembic upgrade head)..."
alembic upgrade head 2>&1 | sed 's/^/      /'

# ── 7. uvicorn 실행 ──────────────────────────────────────────────────────────
echo "[7/7] 서버 시작"
echo ""
echo "  URL  : http://${HOST}:${PORT}       ← 대시보드"
echo "  API  : http://${HOST}:${PORT}/api"
echo "  Docs : http://${HOST}:${PORT}/docs"
echo ""

# shellcheck disable=SC2086
if [[ "$FOREGROUND" -eq 1 ]]; then
    echo "  모드 : foreground (Ctrl+C 종료)"
    echo "======================================================"
    exec uvicorn main:app \
        --host "$HOST" \
        --port "$PORT" \
        --log-level info \
        $RELOAD_FLAG
else
    echo "  모드 : background (nohup, 로그 → /tmp/uvicorn.out)"
    echo "  종료 : pkill -9 -f 'uvicorn main:app'"
    echo "======================================================"
    nohup uvicorn main:app \
        --host "$HOST" \
        --port "$PORT" \
        --log-level info \
        $RELOAD_FLAG > /tmp/uvicorn.out 2>&1 &
    NEW_PID=$!
    disown 2>/dev/null || true
    sleep 3
    if kill -0 "$NEW_PID" 2>/dev/null; then
        echo "  ✅ 시작됨 — PID $NEW_PID"
    else
        echo "  ❌ 시작 실패 — /tmp/uvicorn.out 확인:"
        tail -20 /tmp/uvicorn.out
        exit 1
    fi
fi
