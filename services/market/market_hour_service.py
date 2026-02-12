from datetime import datetime, time
import pytz

class MarketHourService:
    """한국 및 미국 시장 운영 시간 체크 서비스"""
    
    @staticmethod
    def is_kr_market_open() -> bool:
        """한국 시장 운영 여부 (09:00 ~ 15:30, 평일)"""
        tz = pytz.timezone('Asia/Seoul')
        now = datetime.now(tz)
        
        # 주말(토=5, 일=6) 제외
        if now.weekday() >= 5:
            return False
            
        start_time = time(9, 0)
        end_time = time(15, 30)
        
        return start_time <= now.time() <= end_time

    @staticmethod
    def is_us_market_open() -> bool:
        """
        미국 시장 운영 여부 (EST 기준)
        정규장: 09:30 ~ 16:00
        서머타임 고려 시 한국 시간: 22:30 ~ 05:00 (여름), 23:30 ~ 06:00 (겨울)
        """
        tz = pytz.timezone('America/New_York')
        now = datetime.now(tz)
        
        # 주말 제외
        if now.weekday() >= 5:
            return False
            
        start_time = time(9, 30)
        end_time = time(16, 0)
        
        return start_time <= now.time() <= end_time

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
