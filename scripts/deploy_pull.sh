#!/usr/bin/env bash
# 운영 서버 코드 배포: 로컬 DB는 보존, logs/ 변경은 폐기, origin/RC-1 코드만 가져옴
# Usage: bash scripts/deploy_pull.sh [BRANCH]
#        BRANCH 미지정 시 RC-1

set -euo pipefail

BRANCH="${1:-RC-1}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "▶ 운영 DB 보존 + logs 폐기 + ${BRANCH} pull"
echo "─────────────────────────────────────────"

# 1) logs 변경사항 폐기 (자동 생성 파일)
if git status --short | grep -qE '^.M logs/'; then
    echo "🗑️  logs/ 변경사항 폐기"
    git checkout -- logs/ 2>/dev/null || true
fi

# 2) 운영 DB 변경사항 stash로 백업
DB_PATH="data/stock_advisor.db"
DB_STASHED=0
if git status --short -- "$DB_PATH" | grep -q "$DB_PATH"; then
    STASH_MSG="prod-db-$(date +%Y%m%d-%H%M%S)"
    echo "💾 운영 DB stash로 백업: $STASH_MSG"
    git stash push -m "$STASH_MSG" -- "$DB_PATH"
    DB_STASHED=1
fi

# 3) 원격 fetch + branch 동기화
echo "⬇️  origin/$BRANCH fetch"
git fetch origin "$BRANCH"

LOCAL_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE_HEAD=$(git rev-parse "origin/$BRANCH")

if [[ "$LOCAL_HEAD" == "$REMOTE_HEAD" ]]; then
    echo "✅ 이미 최신 (HEAD = origin/$BRANCH)"
elif git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
    echo "⏩ fast-forward 머지"
    git merge --ff-only "origin/$BRANCH"
else
    echo "⚠️  로컬 브랜치가 origin/$BRANCH 와 분기됨 — 코드만 강제 동기화"
    # 하드 리셋 (DB는 stash에 있으니 안전)
    git reset --hard "origin/$BRANCH"
fi

# 4) DB 복원 (stash pop)
if [[ "$DB_STASHED" == "1" ]]; then
    echo "♻️  운영 DB 복원 (stash pop)"
    if git stash pop 2>&1 | grep -q "CONFLICT"; then
        echo "🔧 DB 충돌 해결: 로컬(운영) 버전 유지"
        git checkout --ours -- "$DB_PATH"
        git reset HEAD -- "$DB_PATH" 2>/dev/null || true
        # stash pop이 충돌로 멈춘 경우 stash 항목은 남아있음 → 수동 drop
        git stash drop 2>/dev/null || true
    fi
fi

# 5) 결과 요약
echo "─────────────────────────────────────────"
echo "✅ 배포 완료"
git log --oneline -3
echo
git status --short || true
