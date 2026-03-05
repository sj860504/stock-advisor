"""경제지표 발표 캘린더 서비스

캘린더 표시: FRED 과거 발표 이력으로 다음 날짜를 추정 (UI 용도)
실제 트리거: 미국 경제지표 주요 발표 시각(8:30/9:15/10:00 ET)에 FRED 관측일을
             캐시값과 비교 → 신규 발표 감지 시 macro 재계산 트리거
"""
import requests
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from config import Config
from utils.logger import get_logger

logger = get_logger("economic_calendar")

ET  = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc

FRED_BASE = "https://api.stlouisfed.org/fred"

# ── 시리즈별 메타 ─────────────────────────────────────────────────────────────
# time_et: 공식 발표 시각 (BLS/Fed/Census 기준, hardcode)
# release_id: FRED 발표 그룹 ID (캘린더 조회용)
# freq: "monthly" | "weekly" | "monthly_2" (격월)
SERIES_META: dict[str, dict] = {
    "CPIAUCSL":      {"release_id": "10",  "name": "소비자물가지수(CPI)",    "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "PPIACO":        {"release_id": "31",  "name": "생산자물가지수(PPI)",    "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "PAYEMS":        {"release_id": "50",  "name": "비농업고용(NFP)",        "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "UNRATE":        {"release_id": "50",  "name": "실업률",                 "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "CES0500000003": {"release_id": "50",  "name": "시간당평균임금",          "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "UMCSENT":       {"release_id": "290", "name": "소비자신뢰지수(미시간)", "time_et": "10:00", "weight": 2, "freq": "monthly"},
    "IPMAN":         {"release_id": "13",  "name": "제조업생산지수(산업생산)",  "time_et": "09:15", "weight": 2, "freq": "monthly"},
    "RSXFS":         {"release_id": "84",  "name": "소매판매",               "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "INDPRO":        {"release_id": "13",  "name": "산업생산지수",            "time_et": "09:15", "weight": 1, "freq": "monthly"},
    "TCU":           {"release_id": "13",  "name": "설비가동률",             "time_et": "09:15", "weight": 1, "freq": "monthly"},
    "HOUST":         {"release_id": "52",  "name": "주택착공",               "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "PERMIT":        {"release_id": "52",  "name": "건축허가",               "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "DGORDER":       {"release_id": "86",  "name": "내구재주문",             "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "ICSA":          {"release_id": "120", "name": "실업수당청구(주간)",      "time_et": "08:30", "weight": 2, "freq": "weekly"},
}

# 발표 시각별 그룹 (스케줄러 cron job 등록용)
RELEASE_WINDOWS = ["08:30", "09:15", "10:00"]


class EconomicCalendarService:
    """FRED 발표 캘린더 조회 및 신규 발표 감지 서비스."""

    # 시리즈별 마지막 확인된 관측일 캐시 (서버 수명 동안 메모리 유지)
    _last_obs_date: dict[str, str] = {}   # {series_id: "YYYY-MM-DD"}

    # ── 내부 유틸 ──────────────────────────────────────────────────────────

    @staticmethod
    def _fred_key() -> str:
        return (Config.FRED_API_KEY or "").strip()

    @staticmethod
    def _to_et(date_str: str, time_et: str) -> datetime:
        naive = datetime.strptime(f"{date_str} {time_et}", "%Y-%m-%d %H:%M")
        return naive.replace(tzinfo=ET)

    @staticmethod
    def _et_to_kst(dt_et: datetime) -> datetime:
        return dt_et.astimezone(KST)

    # ── FRED 과거 발표 이력 조회 ───────────────────────────────────────────

    @classmethod
    def _get_past_release_dates(cls, release_id: str, n: int = 6) -> list[str]:
        """FRED release/dates → 최근 N개 발표 날짜 (내림차순). 연속 일자 중복 제거."""
        key = cls._fred_key()
        if not key:
            return []
        try:
            res = requests.get(
                f"{FRED_BASE}/release/dates",
                params={
                    "release_id": release_id,
                    "api_key": key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 50,           # 충분히 많이 가져와 중복 제거 후 N개 선택
                },
                timeout=8,
            )
            res.raise_for_status()
            all_dates = [r["date"] for r in (res.json() or {}).get("release_dates", [])]
            # 연속된 날짜(비즈니스 데이 패턴)는 FRED 내부 업데이트이므로 월 기준 첫 날짜만 유지
            seen_months: set[str] = set()
            deduped: list[str] = []
            for d in all_dates:
                month_key = d[:7]  # "YYYY-MM"
                if month_key not in seen_months:
                    seen_months.add(month_key)
                    deduped.append(d)
                if len(deduped) >= n:
                    break
            return deduped
        except Exception as e:
            logger.debug(f"FRED release/dates 조회 실패 (release_id={release_id}): {e}")
            return []

    @staticmethod
    def _estimate_weekly_release_date() -> str:
        """주간 시리즈 다음 목요일 날짜 반환."""
        today_dt   = datetime.now()
        days_ahead = (3 - today_dt.weekday()) % 7  # 목=3
        if days_ahead == 0:
            days_ahead = 7
        return (today_dt + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    @classmethod
    def _estimate_monthly_release_date(cls, release_id: str) -> str | None:
        """월별 시리즈의 과거 발표 이력 기반 다음 발표일 추정.

        직전 간격(28~50일) 중앙값을 사용해 이상치에 강건하게 계산.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        past = cls._get_past_release_dates(release_id, n=6)
        if not past:
            return None
        if past[0] > today_str:
            return past[0]

        deltas = []
        for i in range(min(len(past) - 1, 4)):
            d1   = datetime.strptime(past[i],     "%Y-%m-%d")
            d2   = datetime.strptime(past[i + 1], "%Y-%m-%d")
            diff = (d1 - d2).days
            if 20 <= diff <= 50:
                deltas.append(diff)

        avg_days = deltas[len(deltas) // 2] if deltas else 30
        last_dt  = datetime.strptime(past[0], "%Y-%m-%d")
        return (last_dt + timedelta(days=avg_days)).strftime("%Y-%m-%d")

    @classmethod
    def _estimate_next_release_date(cls, release_id: str, freq: str) -> str | None:
        """과거 발표 이력으로 다음 발표 날짜 추정."""
        if freq == "weekly":
            return cls._estimate_weekly_release_date()
        return cls._estimate_monthly_release_date(release_id)

    # ── FRED 최신 관측일 조회 (신규 발표 감지용) ──────────────────────────

    @classmethod
    def _get_fred_latest_obs_date(cls, series_id: str) -> str | None:
        """FRED series/observations → 최신 관측 날짜 반환."""
        key = cls._fred_key()
        if not key:
            return None
        try:
            res = requests.get(
                f"{FRED_BASE}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
                timeout=6,
            )
            res.raise_for_status()
            obs = (res.json() or {}).get("observations", [])
            if obs:
                val = obs[0].get("value", ".")
                if val not in (".", ""):
                    return obs[0].get("date")
        except Exception:
            pass
        return None

    # ── 공개 API ──────────────────────────────────────────────────────────

    @classmethod
    def check_for_new_releases(cls) -> list[dict]:
        """모든 FRED 시리즈를 확인하여 신규 발표 목록 반환.

        Returns: [{"series_id", "name", "new_date", "prev_date"}, ...]
        """
        new_releases = []
        for series_id, meta in SERIES_META.items():
            latest = cls._get_fred_latest_obs_date(series_id)
            if not latest:
                continue
            prev = cls._last_obs_date.get(series_id)
            if prev and latest > prev:
                new_releases.append({
                    "series_id": series_id,
                    "name":      meta["name"],
                    "new_date":  latest,
                    "prev_date": prev,
                })
                logger.info(f"🆕 신규 발표 감지: {meta['name']} ({prev} → {latest})")
            cls._last_obs_date[series_id] = latest
        return new_releases

    @staticmethod
    def _build_release_groups() -> dict[str, dict]:
        """SERIES_META를 release_id 기준으로 그룹화하여 반환합니다."""
        release_groups: dict[str, dict] = {}
        for sid, meta in SERIES_META.items():
            rid = meta["release_id"]
            if rid not in release_groups:
                release_groups[rid] = {"time_et": meta["time_et"], "freq": meta["freq"], "series": []}
            release_groups[rid]["series"].append({
                "series_id": sid, "name": meta["name"], "weight": meta["weight"],
            })
        return release_groups

    @classmethod
    def _build_calendar_event(cls, rid: str, grp: dict, next_date: str, now_utc: datetime) -> dict:
        """단일 경제지표 발표 이벤트 dict를 생성합니다."""
        time_et     = grp["time_et"]
        dt_et       = cls._to_et(next_date, time_et)
        dt_utc      = dt_et.astimezone(UTC)
        dt_kst      = cls._et_to_kst(dt_et)
        series_list = grp["series"]
        return {
            "date":         next_date,
            "time_et":      time_et,
            "time_kst":     dt_kst.strftime("%H:%M"),
            "date_kst":     dt_kst.strftime("%Y-%m-%d"),
            "datetime_utc": dt_utc.isoformat(),
            "datetime_kst": dt_kst.isoformat(),
            "release_id":   rid,
            "series_ids":   [s["series_id"] for s in series_list],
            "names":        [s["name"] for s in series_list],
            "total_weight": sum(s["weight"] for s in series_list),
            "is_past":      dt_utc <= now_utc,
        }

    @classmethod
    def get_weekly_calendar(cls, days: int = 7) -> list[dict]:
        """오늘부터 N일간 경제지표 예상 발표 일정 반환 (날짜순)."""
        now_utc   = datetime.now(UTC)
        today_str = now_utc.strftime("%Y-%m-%d")
        end_str   = (now_utc + timedelta(days=days)).strftime("%Y-%m-%d")

        release_groups = cls._build_release_groups()
        events: list[dict] = []
        seen_keys: set[tuple] = set()

        for rid, grp in release_groups.items():
            next_date = cls._estimate_next_release_date(rid, grp["freq"])
            if not next_date or not (today_str <= next_date <= end_str):
                continue
            key = (next_date, rid)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            events.append(cls._build_calendar_event(rid, grp, next_date, now_utc))

        events.sort(key=lambda e: e["datetime_utc"])
        logger.info(f"📅 주간 캘린더: {len(events)}개 이벤트 ({today_str} ~ {end_str})")
        return events
