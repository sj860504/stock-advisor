from datetime import datetime, time, timedelta, date
import pytz
from config import Config

class MarketHourService:
    """한국 및 미국 시장 운영 시간 체크 서비스"""

    @staticmethod
    def _is_time_between(now_t: time, start_t: time, end_t: time) -> bool:
        """시간 구간 포함 여부 (자정 넘김 구간 지원)"""
        if start_t <= end_t:
            return start_t <= now_t <= end_t
        return now_t >= start_t or now_t <= end_t
    
    @staticmethod
    def is_kr_market_open(allow_extended: bool = False) -> bool:
        """한국 시장 운영 여부 (평일)
        - 정규장: 09:00 ~ 15:30
        - 시간외 포함: 09:00 ~ 18:00 (실전)
        - 모의투자(VTS): 시간외 미지원으로 정규장만 허용
        """
        tz = pytz.timezone('Asia/Seoul')
        now = datetime.now(tz)
        
        # 주말(토=5, 일=6) 제외
        if now.weekday() >= 5:
            return False
            
        start_time = time(9, 0)
        kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS)
        end_time = time(18, 0) if kr_allow_extended else time(15, 30)
        
        return start_time <= now.time() <= end_time

    @staticmethod
    def is_kr_after_hours_open() -> bool:
        """한국 사후장(시간외) 주문 가능 여부 (평일 15:40 ~ 18:00)"""
        tz = pytz.timezone('Asia/Seoul')
        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False
        return time(15, 40) <= now.time() <= time(18, 0)

    @staticmethod
    def is_us_market_open(allow_extended: bool = False) -> bool:
        """
        미국 시장 운영 여부 (EST 기준)
        정규장: 09:30 ~ 16:00
        프리/애프터 포함 시: 04:00 ~ 20:00
        """
        tz = pytz.timezone('America/New_York')
        now = datetime.now(tz)
        
        # 주말 제외
        if now.weekday() >= 5:
            return False
        # 미국 공휴일(뉴욕증시 휴장일) 제외
        if MarketHourService._is_us_market_holiday(now.date()):
            return False
            
        if allow_extended:
            start_time = time(4, 0)
            end_time = time(20, 0)
        else:
            start_time = time(9, 30)
            end_time = time(16, 0)
        
        return start_time <= now.time() <= end_time

    @staticmethod
    def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
        """고정 공휴일의 관측일 계산(주말이면 금/월 대체)"""
        d = date(year, month, day)
        if d.weekday() == 5:  # Saturday
            return d - timedelta(days=1)
        if d.weekday() == 6:  # Sunday
            return d + timedelta(days=1)
        return d

    @staticmethod
    def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
        """해당 월 n번째 weekday(월=0 ... 일=6)"""
        d = date(year, month, 1)
        shift = (weekday - d.weekday() + 7) % 7
        return d + timedelta(days=shift + (n - 1) * 7)

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
        """해당 월 마지막 weekday(월=0 ... 일=6)"""
        if month == 12:
            d = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            d = date(year, month + 1, 1) - timedelta(days=1)
        shift = (d.weekday() - weekday + 7) % 7
        return d - timedelta(days=shift)

    @staticmethod
    def _easter_sunday(year: int) -> date:
        """서기력 기준 부활절 일요일"""
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
        """
        미국 정규증시(NYSE) 주요 휴장일 판별
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
        """
        전략 실행 허용 시간 체크
        - KR: 정규장 시작 1시간 전 ~ 정규장 마감
        - US: (allow_extended=True) 프리장 시작 1시간 전 ~ 애프터 마감
              (allow_extended=False) 정규장 시작 1시간 전 ~ 정규장 마감
        """
        kr_tz = pytz.timezone("Asia/Seoul")
        us_tz = pytz.timezone("America/New_York")
        now_kr = datetime.now(kr_tz)
        now_us = datetime.now(us_tz)

        # 주말은 양 시장 모두 중단
        if now_kr.weekday() >= 5 and now_us.weekday() >= 5:
            return False

        # KR window: 08:00 ~ 정규장/시간외 종료
        # 모의투자(VTS)는 시간외 주문이 불가하여 KR 확장시간을 적용하지 않음
        kr_start = (datetime.combine(now_kr.date(), time(9, 0)) - timedelta(minutes=pre_open_lead_minutes)).time()
        kr_allow_extended = allow_extended and (not Config.KIS_IS_VTS)
        kr_end = time(18, 0) if kr_allow_extended else time(15, 30)
        kr_open = now_kr.weekday() < 5 and cls._is_time_between(now_kr.time(), kr_start, kr_end)

        # US window
        if allow_extended:
            us_market_start = time(4, 0)   # 프리장 시작
            us_market_end = time(20, 0)    # 애프터 종료
        else:
            us_market_start = time(9, 30)  # 정규장 시작
            us_market_end = time(16, 0)    # 정규장 종료

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
        """실시간 데이터 수동 수집 여부 (장종료 직후까지 허용)"""
        # 정규장 시간 + 1시간 (데이터 정리 시간)
        tz = pytz.timezone('Asia/Seoul' if market.upper() == "KR" else 'America/New_York')
        now = datetime.now(tz)
        if now.weekday() >= 5: return False
        
        if market.upper() == "KR":
            return time(9, 0) <= now.time() <= time(16, 30)
        else:
            return time(9, 30) <= now.time() <= time(17, 30)

    @staticmethod
    def can_fetch_history() -> bool:
        """과거 데이터(일봉/분봉) 조회는 24시간 허용 (KIS API 특성)"""
        return True
