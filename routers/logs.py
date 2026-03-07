"""서버 로그 조회 API."""
import os
from fastapi import APIRouter, Query
from typing import List

router = APIRouter(prefix="/logs", tags=["Logs"])

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "app.log")
MAX_LINES = 2000


@router.get("", response_model=List[str])
def get_logs(
    lines: int = Query(default=200, ge=1, le=MAX_LINES),
    level: str = Query(default="", description="INFO|WARNING|ERROR|DEBUG — empty for all"),
    search: str = Query(default="", description="Search keyword"),
) -> List[str]:
    """app.log 마지막 N줄 반환. level/search 필터 지원."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        result = [l.rstrip() for l in all_lines[-MAX_LINES:]]
        if level:
            result = [l for l in result if f" - {level.upper()} - " in l]
        if search:
            lw = search.lower()
            result = [l for l in result if lw in l.lower()]
        return result[-lines:]
    except Exception as e:
        return [f"[ERROR] Failed to read logs: {e}"]
