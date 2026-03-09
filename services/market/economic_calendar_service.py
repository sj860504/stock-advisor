"""Economic indicator release calendar service.

Calendar display: estimates next release dates from FRED past release history (for UI).
Actual trigger: at US economic release times (8:30/9:15/10:00 ET), compares FRED
                observation dates with cached values -> triggers macro recalculation on new releases.
"""
import requests
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from config import Config
from models.schemas import CalendarEvent
from utils.logger import get_logger

logger = get_logger("economic_calendar")

ET  = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc

FRED_BASE = "https://api.stlouisfed.org/fred"

# ── Per-series metadata ───────────────────────────────────────────────────────
# time_et: official release time (BLS/Fed/Census, hardcoded)
# release_id: FRED release group ID (for calendar queries)
# freq: "monthly" | "weekly" | "monthly_2" (bimonthly)
SERIES_META: dict[str, dict] = {
    "CPIAUCSL":      {"release_id": "10",  "name": "CPI",                          "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "PPIACO":        {"release_id": "31",  "name": "PPI",                          "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "PAYEMS":        {"release_id": "50",  "name": "Nonfarm Payrolls (NFP)",       "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "UNRATE":        {"release_id": "50",  "name": "Unemployment Rate",            "time_et": "08:30", "weight": 3, "freq": "monthly"},
    "CES0500000003": {"release_id": "50",  "name": "Average Hourly Earnings",      "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "UMCSENT":       {"release_id": "290", "name": "UMich Consumer Sentiment",     "time_et": "10:00", "weight": 2, "freq": "monthly"},
    "IPMAN":         {"release_id": "13",  "name": "Manufacturing Production",     "time_et": "09:15", "weight": 2, "freq": "monthly"},
    "RSXFS":         {"release_id": "84",  "name": "Retail Sales",                 "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "INDPRO":        {"release_id": "13",  "name": "Industrial Production",        "time_et": "09:15", "weight": 1, "freq": "monthly"},
    "TCU":           {"release_id": "13",  "name": "Capacity Utilization",         "time_et": "09:15", "weight": 1, "freq": "monthly"},
    "HOUST":         {"release_id": "52",  "name": "Housing Starts",               "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "PERMIT":        {"release_id": "52",  "name": "Building Permits",             "time_et": "08:30", "weight": 1, "freq": "monthly"},
    "DGORDER":       {"release_id": "86",  "name": "Durable Goods Orders",         "time_et": "08:30", "weight": 2, "freq": "monthly"},
    "ICSA":          {"release_id": "120", "name": "Initial Jobless Claims (Wkly)", "time_et": "08:30", "weight": 2, "freq": "weekly"},
}

# Release time groups (for scheduler cron job registration)
RELEASE_WINDOWS = ["08:30", "09:15", "10:00"]


class EconomicCalendarService:
    """FRED release calendar query and new release detection service."""

    # Per-series last confirmed observation date cache (persists in memory for server lifetime)
    _last_obs_date: dict[str, str] = {}   # {series_id: "YYYY-MM-DD"}

    # ── Internal utilities ────────────────────────────────────────────────

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

    # ── FRED past release history queries ──────────────────────────────────

    @classmethod
    def _get_past_release_dates(cls, release_id: str, n: int = 6) -> list[str]:
        """FRED release/dates -> last N release dates (desc). Deduplicates consecutive dates."""
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
                    "limit": 50,           # Fetch enough to select N after deduplication
                },
                timeout=8,
            )
            res.raise_for_status()
            all_dates = [r["date"] for r in (res.json() or {}).get("release_dates", [])]
            # Consecutive dates (business day pattern) are FRED internal updates; keep only first per month
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
            logger.debug(f"FRED release/dates query failed (release_id={release_id}): {e}")
            return []

    @staticmethod
    def _estimate_weekly_release_date() -> str:
        """Return next Thursday date for weekly series."""
        today_dt   = datetime.now()
        days_ahead = (3 - today_dt.weekday()) % 7  # Thu=3
        if days_ahead == 0:
            days_ahead = 7
        return (today_dt + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    @classmethod
    def _estimate_monthly_release_date(cls, release_id: str) -> str | None:
        """Estimate next release date for monthly series based on past release history.

        Uses median of recent intervals (28~50 days) for outlier robustness.
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
        """Estimate next release date from past release history."""
        if freq == "weekly":
            return cls._estimate_weekly_release_date()
        return cls._estimate_monthly_release_date(release_id)

    # ── FRED latest observation date query (for new release detection) ─────

    @classmethod
    def _get_fred_latest_obs_date(cls, series_id: str) -> str | None:
        """FRED series/observations -> return latest observation date."""
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

    # ── Public API ────────────────────────────────────────────────────────

    @classmethod
    def check_for_new_releases(cls) -> list[dict]:
        """Check all FRED series for new releases.

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
                logger.info(f"🆕 New release detected: {meta['name']} ({prev} → {latest})")
            cls._last_obs_date[series_id] = latest
        return new_releases

    @staticmethod
    def _build_release_groups() -> dict[str, dict]:
        """Group SERIES_META by release_id and return."""
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
    def _build_calendar_event(cls, rid: str, grp: dict, next_date: str, now_utc: datetime) -> CalendarEvent:
        """Build a single CalendarEvent for an economic release."""
        time_et     = grp["time_et"]
        dt_et       = cls._to_et(next_date, time_et)
        dt_utc      = dt_et.astimezone(UTC)
        dt_kst      = cls._et_to_kst(dt_et)
        series_list = grp["series"]
        return CalendarEvent(
            date=next_date,
            time_et=time_et,
            time_kst=dt_kst.strftime("%H:%M"),
            date_kst=dt_kst.strftime("%Y-%m-%d"),
            datetime_utc=dt_utc.isoformat(),
            datetime_kst=dt_kst.isoformat(),
            release_id=rid,
            series_ids=[s["series_id"] for s in series_list],
            names=[s["name"] for s in series_list],
            total_weight=sum(s["weight"] for s in series_list),
            is_past=dt_utc <= now_utc,
        )

    @classmethod
    def get_weekly_calendar(cls, days: int = 7) -> list[CalendarEvent]:
        """Return expected economic release schedule for next N days (sorted by date)."""
        now_utc   = datetime.now(UTC)
        today_str = now_utc.strftime("%Y-%m-%d")
        end_str   = (now_utc + timedelta(days=days)).strftime("%Y-%m-%d")

        release_groups = cls._build_release_groups()
        events: list[CalendarEvent] = []
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

        events.sort(key=lambda e: e.datetime_utc)
        logger.info(f"📅 Weekly calendar: {len(events)} events ({today_str} ~ {end_str})")
        return events
