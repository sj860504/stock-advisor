from datetime import datetime, time, timedelta, date
import pytz
from config import Config

class MarketHourService:
    """KR and US market hours check service."""

    @staticmethod
    def _is_time_between(now_t: time, start_t: time, end_t: time) -> bool:
        """Check if time is within range (supports midnight-crossing intervals)."""
        if start_t <= end_t:
            return start_t <= now_t <= end_t
        return now_t >= start_t or now_t <= end_t
    
    @staticmethod
    def is_kr_market_open(allow_extended: bool = False) -> bool:
        """Check if KR market is open (weekdays only).
        - Regular: 09:00 ~ 15:30
        - Extended: 09:00 ~ 18:00 (live trading or with real credentials)
        - VTS only (no real credentials): regular hours only
        """
        tz = pytz.timezone('Asia/Seoul')
        now = datetime.now(tz)

        # Exclude weekends (Sat=5, Sun=6)
        if now.weekday() >= 5:
            return False

        start_time = time(9, 0)
        # Allow extended hours if real credentials present (incl. split mode)
        kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS or Config.has_real_credentials())
        end_time = time(18, 0) if kr_allow_extended else time(15, 30)

        return start_time <= now.time() <= end_time

    @staticmethod
    def is_kr_after_hours_open() -> bool:
        """Check if KR after-hours order window is open (weekdays 15:40 ~ 18:00)."""
        tz = pytz.timezone('Asia/Seoul')
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return time(15, 40) <= now.time() <= time(18, 0)

    @staticmethod
    def is_us_market_open(allow_extended: bool = False) -> bool:
        """Check if US market is open (EST).
        Regular: 09:30 ~ 16:00
        Pre/after-market: 04:00 ~ 20:00 (live trading or with real credentials)
        VTS only (no real credentials): regular hours only
        """
        tz = pytz.timezone('America/New_York')
        now = datetime.now(tz)

        # Exclude weekends
        if now.weekday() >= 5:
            return False
        # Exclude US market holidays (NYSE closed days)
        if MarketHourService._is_us_market_holiday(now.date()):
            return False

        # Allow pre/after-market if real credentials present (incl. split mode)
        us_allow_extended = allow_extended and (not Config.KIS_IS_VTS or Config.has_real_credentials())
        if us_allow_extended:
            start_time = time(4, 0)
            end_time = time(20, 0)
        else:
            start_time = time(9, 30)
            end_time = time(16, 0)

        return start_time <= now.time() <= end_time

    @staticmethod
    def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
        """Calculate observed date for fixed holidays (Fri/Mon substitution if on weekend)."""
        d = date(year, month, day)
        if d.weekday() == 5:  # Saturday
            return d - timedelta(days=1)
        if d.weekday() == 6:  # Sunday
            return d + timedelta(days=1)
        return d

    @staticmethod
    def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
        """Nth weekday of month (Mon=0 ... Sun=6)."""
        d = date(year, month, 1)
        shift = (weekday - d.weekday() + 7) % 7
        return d + timedelta(days=shift + (n - 1) * 7)

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
        """Last weekday of month (Mon=0 ... Sun=6)."""
        if month == 12:
            d = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            d = date(year, month + 1, 1) - timedelta(days=1)
        shift = (d.weekday() - weekday + 7) % 7
        return d - timedelta(days=shift)

    @staticmethod
    def _easter_sunday(year: int) -> date:
        """Easter Sunday by Gregorian calendar."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    @classmethod
    def _is_us_market_holiday(cls, d: date) -> bool:
        """Check if date is a NYSE market holiday.
        - New Year's Day, MLK Day, Presidents' Day, Good Friday,
          Memorial Day, Juneteenth(2022+), Independence Day,
          Labor Day, Thanksgiving Day, Christmas Day
        """
        year = d.year
        holidays = {
            cls._observed_fixed_holiday(year, 1, 1),     # New Year's Day
            cls._nth_weekday_of_month(year, 1, 0, 3),    # MLK Day
            cls._nth_weekday_of_month(year, 2, 0, 3),    # Presidents' Day
            cls._easter_sunday(year) - timedelta(days=2),# Good Friday
            cls._last_weekday_of_month(year, 5, 0),      # Memorial Day
            cls._observed_fixed_holiday(year, 7, 4),     # Independence Day
            cls._nth_weekday_of_month(year, 9, 0, 1),    # Labor Day
            cls._nth_weekday_of_month(year, 11, 3, 4),   # Thanksgiving
            cls._observed_fixed_holiday(year, 12, 25),   # Christmas Day
        }
        if year >= 2022:
            holidays.add(cls._observed_fixed_holiday(year, 6, 19))  # Juneteenth
        return d in holidays

    @classmethod
    def is_strategy_window_open(cls, allow_extended: bool = True, pre_open_lead_minutes: int = 60) -> bool:
        """Check if strategy execution window is open.
        - KR: 1 hour before regular market open ~ regular market close
        - US: (allow_extended=True) 1 hour before pre-market ~ after-market close
              (allow_extended=False) 1 hour before regular open ~ regular close
        """
        kr_tz = pytz.timezone("Asia/Seoul")
        us_tz = pytz.timezone("America/New_York")
        now_kr = datetime.now(kr_tz)
        now_us = datetime.now(us_tz)

        # Both markets closed on weekends
        if now_kr.weekday() >= 5 and now_us.weekday() >= 5:
            return False

        # KR window: 08:00 ~ regular/extended hours close
        # Allow extended hours if real credentials present (incl. split mode)
        kr_start = (datetime.combine(now_kr.date(), time(9, 0)) - timedelta(minutes=pre_open_lead_minutes)).time()
        kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS or Config.has_real_credentials())
        kr_end = time(18, 0) if kr_allow_extended else time(15, 30)
        kr_open = now_kr.weekday() < 5 and cls._is_time_between(now_kr.time(), kr_start, kr_end)

        # US window
        # Allow pre/after-market if real credentials present (incl. split mode)
        us_allow_extended = allow_extended and (not Config.KIS_IS_VTS or Config.has_real_credentials())
        if us_allow_extended:
            us_market_start = time(4, 0)   # Pre-market open
            us_market_end = time(20, 0)    # After-market close
        else:
            us_market_start = time(9, 30)  # Regular open
            us_market_end = time(16, 0)    # Regular close

        us_start_dt = datetime.combine(now_us.date(), us_market_start) - timedelta(minutes=pre_open_lead_minutes)
        us_start = us_start_dt.time()
        us_open = (
            now_us.weekday() < 5
            and (not cls._is_us_market_holiday(now_us.date()))
            and cls._is_time_between(now_us.time(), us_start, us_market_end)
        )

        return kr_open or us_open

    @classmethod
    def should_fetch(cls, market: str = "KR") -> bool:
        """Check if real-time data collection should run (allowed up to 1hr after market close)."""
        # Regular hours + 1 hour (data cleanup window)
        tz = pytz.timezone('Asia/Seoul' if market.upper() == "KR" else 'America/New_York')
        now = datetime.now(tz)
        if now.weekday() >= 5: return False
        
        if market.upper() == "KR":
            return time(9, 0) <= now.time() <= time(16, 30)
        else:
            return time(9, 30) <= now.time() <= time(17, 30)

    @staticmethod
    def can_fetch_history() -> bool:
        """Historical data (daily/minute candles) is available 24/7 (KIS API characteristic)."""
        return True
