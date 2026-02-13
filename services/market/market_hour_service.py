from datetime import datetime, time, timedelta
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
            
        if allow_extended:
            start_time = time(4, 0)
            end_time = time(20, 0)
        else:
            start_time = time(9, 30)
            end_time = time(16, 0)
        
        return start_time <= now.time() <= end_time

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
        us_open = now_us.weekday() < 5 and cls._is_time_between(now_us.time(), us_start, us_market_end)

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
